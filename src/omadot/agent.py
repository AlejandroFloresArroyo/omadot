"""Pipecat processor that hands each finished user turn to Hermes and streams the reply."""

import asyncio
import random
from dataclasses import dataclass, field

from loguru import logger
from pipecat.frames.frames import (
    DataFrame,
    Frame,
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from omadot.hermes import HermesClient, HermesUnavailable
from omadot.payload import MARK, holdback, parse


def last_user_text(messages) -> str:
    """The user's words if the context ends in a user turn; anything else (e.g. a developer prompt) is not ours."""
    if not messages or messages[-1].get("role") != "user":
        return ""
    content = messages[-1].get("content")
    if isinstance(content, str):
        return content
    return " ".join(p.get("text", "") for p in content if p.get("type") == "text")


# Short, varied, none of them a hum ("Mmm" came out of Kokoro as a buzz). `_filler` never repeats the last three.
FILLERS = ["A ver.", "Un segundo.", "Déjame ver.", "Ya voy.", "Un momento.", "Lo miro.", "Vamos a ver.", "Déjame revisar.", "Ahora mismo.", "Ya lo busco.", "Voy.", "Dame un segundo."]
HERMES_ERROR = "No pude hablar con Hermes. Inténtalo de nuevo."
HERMES_DOWN = "Hermes no responde; lo sigo intentando."
RETRY_EVERY = 5.0  # seconds between attempts while the gateway is down
RETRIES = 6  # ~30 s, then give up until "Reintentar ahora" or the next turn


@dataclass
class OmiErrorFrame(DataFrame):
    """Omi could not answer: the bar and the card show `text` until the next turn."""

    text: str


@dataclass
class OmiCardFrame(DataFrame):
    """The structured part of the reply (files, a draft, a diff…): what the card draws, never spoken."""

    card: dict = field(default_factory=dict)


@dataclass
class OmiWorkFrame(DataFrame):
    """Hermes started a tool: Omi is working, not just thinking."""

    tool: str = ""


@dataclass
class OmiAskFrame(DataFrame):
    """Hermes wants permission for a tool; the card asks and `omadot approve` answers."""

    question: str
    request_id: str


def approval_question(ev: dict) -> str:
    for key in ("description", "reason", "message", "summary"):
        if ev.get(key):
            return str(ev[key])
    tool, command = ev.get("tool") or ev.get("tool_name") or "", ev.get("command") or ""
    return f"¿Ejecuto {tool} {command}?".replace("  ", " ").strip()


class HermesAgent(FrameProcessor):
    """Streams Hermes' reply; if the first token is slow, says a short filler so the turn feels live.

    If the gateway is down, the turn is sent again every `retry_every` seconds, up to `retries` times.
    The `@@omi {json}` line at the end of a reply is held back from the TTS and published as an OmiCardFrame.
    """

    def __init__(
        self,
        client: HermesClient,
        *,
        filler_after: float | None = 0.6,
        fillers: list[str] = FILLERS,
        retry_every: float = RETRY_EVERY,
        retries: int = RETRIES,
        enrich=None,
        fast=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._client = client
        self._enrich = enrich  # words from the mic -> context.Turn (text for Hermes + what the card shows now)
        self._fast = fast  # FastLane: answers trivial turns in well under a second, escalates the rest
        self.fast_enabled = fast is not None
        self._filler_after = filler_after
        self._fillers = fillers
        self._retry_every = retry_every
        self._retries = retries
        self._task: asyncio.Task | None = None
        self._run_id: str | None = None
        self._pending: str | None = None  # the last turn Hermes did not answer
        self._waiting = False  # between two attempts
        self._wake = asyncio.Event()
        self._approval: tuple[str, str] | None = None  # (run_id, request_id) Hermes is waiting on
        self._recent_fillers: list[str] = []

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            text = last_user_text(frame.context.get_messages()).strip()
            if text:
                await self._cancel()
                if self._enrich:  # hyprctl/grim/wl-paste run off the loop: the mic must keep flowing
                    turn = await asyncio.to_thread(self._enrich, text)
                    if turn.card:
                        await self.push_frame(OmiCardFrame(turn.card))
                    text = turn.text
                self._task = self.create_task(self._respond(text))
            return
        if isinstance(frame, InterruptionFrame):
            await self._cancel()
        await self.push_frame(frame, direction)

    async def hush(self) -> None:
        """Omi closed while he answers: stop the run; the caller interrupts the voice."""
        await self._cancel()

    async def ask(self, text: str) -> None:
        """A turn that did not come from the microphone: a dropped file, a card button, a reminder's reply."""
        await self._cancel()
        self._task = self.create_task(self._respond(text))

    async def approve(self, choice: str) -> bool:
        """Answer Hermes' pending approval request (once | session | always | deny)."""
        if not self._approval:
            return False
        run_id, request_id = self._approval
        self._approval = None
        await self._client.approve(run_id, request_id, choice)
        return True


    def use_fast(self, fast, *, enabled: bool = True) -> None:
        """Attach a fast lane started after the agent (it is opt-in and may be turned on at runtime)."""
        self._fast = fast
        self.fast_enabled = enabled

    def retry_now(self) -> None:
        """Skip the wait between attempts, or ask the last unanswered turn again."""
        if self._waiting:
            self._wake.set()
        elif self._pending and (self._task is None or self._task.done()):
            self._task = self.create_task(self._respond(self._pending))

    def abandon_retry(self) -> None:
        """The mic was closed: stop insisting on a turn the user walked away from."""
        self._pending = None
        if self._waiting and self._task:
            task, self._task = self._task, None
            task.cancel()

    async def _respond(self, text: str):
        self._pending = text
        await self.push_frame(LLMFullResponseStartFrame())
        filler = self.create_task(self._filler()) if self._filler_after is not None else None
        answered = failed = False
        full, kept = "", ""
        fast_task = asyncio.ensure_future(self._fast.ask(text)) if (self._fast and self.fast_enabled) else None
        try:
            attempt = 0
            while True:
                try:
                    self._run_id = await self._client.start(text)
                    break
                except HermesUnavailable as e:
                    if attempt >= self._retries:
                        raise
                    attempt += 1
                    logger.warning(f"Hermes unavailable ({e}); attempt {attempt}/{self._retries} in {self._retry_every}s")
                    if filler:
                        await self.cancel_task(filler)
                        filler = None
                    await self.push_frame(OmiErrorFrame(f"Hermes no responde. Reintento {attempt} de {self._retries}…"))
                    if attempt == 1:
                        await self.push_frame(TTSSpeakFrame(HERMES_DOWN))
                    await self._wait_to_retry()
            events = self._client.events(self._run_id).__aiter__()
            nxt = asyncio.ensure_future(events.__anext__())
            try:
                while True:
                    waiting = [nxt] + ([fast_task] if fast_task and not fast_task.done() else [])
                    done, _ = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
                    if fast_task and fast_task in done and not answered:
                        fast_reply = fast_task.result()
                        fast_task = None
                        if fast_reply.answered:  # the fast lane got there first: Hermes' run is not needed
                            logger.info(f"fast lane answered in {fast_reply.ms} ms")
                            nxt.cancel()
                            if filler:
                                await self.cancel_task(filler)
                                filler = None
                            answered = True
                            full = fast_reply.text
                            await self.push_frame(LLMTextFrame(full))
                            await self._stop_run()
                            break
                        if nxt not in done:
                            continue
                    if nxt not in done:
                        continue
                    try:
                        ev = nxt.result()
                    except StopAsyncIteration:
                        break
                    nxt = asyncio.ensure_future(events.__anext__())
                    kind = ev.get("event")
                    if kind == "message.delta":
                        if filler:
                            await self.cancel_task(filler)
                            filler = None
                        if not answered and fast_task:  # Hermes spoke first: the fast lane's answer is moot
                            fast_task.cancel()
                            fast_task = None
                        answered = True
                        full += ev["delta"]
                        safe, kept = holdback(kept + ev["delta"])
                        if safe:
                            await self.push_frame(LLMTextFrame(safe))
                    elif kind == "tool.started":
                        await self.push_frame(OmiWorkFrame(str(ev.get("tool") or ev.get("tool_name") or "")))
                    elif kind == "approval.request":
                        self._approval = (self._run_id, str(ev.get("request_id", "")))
                        await self.push_frame(OmiAskFrame(approval_question(ev), self._approval[1]))
            finally:
                if not nxt.done():
                    nxt.cancel()
            reply = parse(full)
            if kept and not kept.startswith(MARK):  # a lone "@" that never became the marker
                await self.push_frame(LLMTextFrame(kept))
            elif reply.tail:  # words the model put after its card line are still worth saying
                await self.push_frame(LLMTextFrame(" " + reply.tail))
            if reply.card:
                await self.push_frame(OmiCardFrame(reply.card))
            if answered:
                self._pending = None
            else:
                logger.error("Hermes run finished without a reply")
                failed = True
        except Exception as e:
            logger.error(f"Hermes run failed: {e}")
            failed = True
        finally:
            if filler:
                await self.cancel_task(filler)
            if fast_task and not fast_task.done():
                fast_task.cancel()
            self._run_id = None
            self._approval = None
            if failed:  # never on barge-in: a cancelled task skips both branches above
                await self.push_frame(OmiErrorFrame(HERMES_ERROR))
                await self.push_frame(TTSSpeakFrame(HERMES_ERROR))
            await self.push_frame(LLMFullResponseEndFrame())

    async def _stop_run(self):
        run_id, self._run_id = self._run_id, None
        if run_id:
            try:
                await self._client.stop(run_id)
            except Exception as e:
                logger.warning(f"Hermes stop failed: {e}")

    async def _wait_to_retry(self):
        self._wake.clear()
        self._waiting = True
        try:
            await asyncio.wait_for(self._wake.wait(), self._retry_every)
        except TimeoutError:
            pass
        finally:
            self._waiting = False

    def pick_filler(self) -> str:
        pool = [f for f in self._fillers if f not in self._recent_fillers] or list(self._fillers)
        choice = random.choice(pool)
        self._recent_fillers = (self._recent_fillers + [choice])[-3:]
        return choice

    async def _filler(self):
        await asyncio.sleep(self._filler_after)
        await self.push_frame(TTSSpeakFrame(self.pick_filler()))

    async def _cancel(self):
        run_id, task = self._run_id, self._task
        self._run_id = self._task = None
        # Task first: once /stop lands, the event stream ends in run.cancelled, which must not read as a failure.
        if task and not task.done():
            await self.cancel_task(task)
        if run_id:
            try:
                await self._client.stop(run_id)
            except Exception as e:
                logger.warning(f"Hermes stop failed: {e}")
