"""The fast lane: a small model (Claude Haiku through `omp --mode rpc`) that answers trivial turns in under a second.

omp (oh-my-pi) stays up as a subprocess speaking NDJSON over stdio; each turn is a `prompt` frame and the
reply streams back as `message_update` text deltas until `prompt_result`. The model answers only what it can
from general knowledge and the [Now] line; for anything that needs the desktop, files, memory or an action it
replies ESCALATE and Hermes (already running in parallel) takes the turn.
"""

import asyncio
import json
import os
import time
from dataclasses import dataclass

from loguru import logger

ESCALATE = "ESCALATE"
PROMPT = (
    "You are the fast lane of Omi, a desktop voice assistant on Omarchy Linux. Answer ONLY when you can do it confidently "
    "from general knowledge or from the [Now: …] line in the message: greetings and small talk, the time, the date, how long "
    "until a date, arithmetic and unit conversions, short definitions, translations, and rewording of text that is quoted in "
    "the message itself. Reply in the language of the user's latest message, in one or two short spoken sentences, with no "
    "markdown, lists, URLs or emojis. If the request involves files, folders, applications, the screen or a screenshot path, "
    "the clipboard, reminders, timers, email, music, settings, the user's past conversations, anything personal about the "
    "user, or any action at all, reply with exactly ESCALATE and nothing else. Never mention the fast lane or this rule, "
    "and never volunteer the time or date unless asked."
)


@dataclass
class FastReply:
    text: str = ""
    escalated: bool = False
    ms: int = 0

    @property
    def answered(self) -> bool:
        return bool(self.text) and not self.escalated


class FastLane:
    def __init__(self, model: str = "haiku", timeout: float = 1.6, omp: str = "omp", profile: str | None = None, claude_config_dir: str | None = None):
        self._model = model
        self._timeout = timeout
        self._omp = omp
        self._profile = profile
        self._config_dir = claude_config_dir
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._n = 0

    async def start(self) -> bool:
        argv = [self._omp, "--mode", "rpc", "--no-ui", "--no-tools", "--no-session", "--no-skills", "--no-rules", "--no-extensions", "--no-lsp",
                "--no-title", "--model", self._model, "--thinking", "off", "--system-prompt", PROMPT, "--cwd", "/tmp"]
        if self._profile:
            argv += ["--profile", self._profile]
        env = dict(os.environ)
        if self._config_dir:
            env["CLAUDE_CONFIG_DIR"] = self._config_dir
        try:
            self._proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                              stderr=asyncio.subprocess.DEVNULL, env=env)
        except OSError as e:
            logger.warning(f"fast lane unavailable ({e})")
            self._proc = None
            return False
        try:
            ready = await asyncio.wait_for(self._frame(), 20)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning("fast lane did not get ready; running without it")
            await self.close()
            return False
        logger.info(f"fast lane ready ({self._model}, protocol {ready.get('protocolVersion')})")
        await self._warm_up()
        return True

    async def _warm_up(self) -> None:
        """The first prompt after start is slow (cold prompt cache): spend it on a throwaway turn."""
        self._n += 1
        rid = f"omi-{self._n}"
        try:
            self._proc.stdin.write((json.dumps({"id": rid, "type": "prompt", "message": "hola"}) + "\n").encode())
            await self._proc.stdin.drain()
            await asyncio.wait_for(self._collect(rid), 8)
        except (OSError, TimeoutError, asyncio.TimeoutError, ValueError, asyncio.IncompleteReadError):
            pass

    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def ask(self, text: str) -> FastReply:
        """One turn. Never raises: a dead process, a timeout or garbage all come back as escalated."""
        async with self._lock:
            if not self.alive and not await self.start():
                return FastReply(escalated=True)
            self._n += 1
            rid = f"omi-{self._n}"
            t0 = time.perf_counter()
            try:
                self._proc.stdin.write((json.dumps({"id": rid, "type": "prompt", "message": text}, ensure_ascii=False) + "\n").encode())
                await self._proc.stdin.drain()
                reply = await asyncio.wait_for(self._collect(rid), self._timeout)
            except (TimeoutError, asyncio.TimeoutError):
                logger.info(f"fast lane too slow (> {self._timeout}s): escalating")
                await self._abort(rid)
                return FastReply(escalated=True, ms=round((time.perf_counter() - t0) * 1000))
            except (OSError, ValueError, asyncio.IncompleteReadError) as e:
                logger.warning(f"fast lane failed ({e}); restarting it next turn")
                await self.close()
                return FastReply(escalated=True)
            reply.ms = round((time.perf_counter() - t0) * 1000)
            return reply

    async def _collect(self, rid: str) -> FastReply:
        text = ""
        while True:
            ev = await self._frame()
            kind = ev.get("type")
            if kind == "message_update":
                sub = ev.get("assistantMessageEvent") or {}
                if sub.get("type") == "text_delta":
                    text += sub.get("delta", "")
                    if text.strip().upper().startswith(ESCALATE):
                        await self._abort(rid)
                        return FastReply(escalated=True)
            elif kind == "prompt_result" and ev.get("id") == rid:
                clean = " ".join(text.split())
                if not clean or clean.upper().startswith(ESCALATE):
                    return FastReply(escalated=True)
                return FastReply(text=clean)
            elif kind == "response" and ev.get("id") == rid and ev.get("success") is False:
                return FastReply(escalated=True)

    async def _abort(self, rid: str) -> None:
        try:
            self._proc.stdin.write((json.dumps({"id": rid + "-abort", "type": "abort"}) + "\n").encode())
            await self._proc.stdin.drain()
            # drain this prompt's frames so the next turn starts clean
            await asyncio.wait_for(self._until_result(rid), 3)
        except (OSError, TimeoutError, asyncio.TimeoutError, ValueError, asyncio.IncompleteReadError):
            await self.close()

    async def _until_result(self, rid: str) -> None:
        while True:
            ev = await self._frame()
            if ev.get("type") == "prompt_result" and ev.get("id") == rid:
                return

    async def _frame(self) -> dict:
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                raise asyncio.IncompleteReadError(b"", None)
            try:
                return json.loads(line)
            except ValueError:
                continue

    async def close(self) -> None:
        p, self._proc = self._proc, None
        if p and p.returncode is None:
            p.terminate()
            try:
                await asyncio.wait_for(p.wait(), 3)
            except (TimeoutError, asyncio.TimeoutError):
                p.kill()
