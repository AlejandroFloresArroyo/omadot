import asyncio

import pytest
from pipecat.frames.frames import (
    InterruptionFrame,
    LLMContextFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.tests.utils import SleepFrame, run_test

from omadot.agent import HERMES_DOWN, HERMES_ERROR, HermesAgent, OmiAskFrame, OmiCardFrame, OmiErrorFrame, OmiWorkFrame
from omadot.hermes import HermesUnavailable


class FakeHermes:
    def __init__(
        self, first_token_delay: float, deltas: list, fail: Exception | None = None, down: int = 0
    ):
        self.first_token_delay = first_token_delay
        # a str is a text delta; a dict is any other run event (tool.started, approval.request…)
        self._deltas = deltas
        self.approvals: list[tuple[str, str, str]] = []
        self._approved = asyncio.Event()
        self._fail = fail
        self.down = down  # how many more start() calls find the gateway down
        self.started: list[str] = []
        self.stopped: list[str] = []
        self._cancelled = asyncio.Event()

    async def start(self, text: str) -> str:
        self.started.append(text)
        if self.down:
            self.down -= 1
            raise HermesUnavailable("ConnectError: refused")
        return "run-1"

    async def events(self, run_id: str):
        try:
            await asyncio.wait_for(self._cancelled.wait(), self.first_token_delay)
            raise RuntimeError("run.cancelled")  # what Hermes' event stream ends with after /stop
        except TimeoutError:
            pass
        if self._fail:
            raise self._fail
        for d in self._deltas:
            yield d if isinstance(d, dict) else {"event": "message.delta", "delta": d}
            if isinstance(d, dict) and d["event"] == "approval.request":
                await self._approved.wait()  # the real stream stays open until the client answers

    async def approve(self, run_id: str, request_id: str, choice: str) -> None:
        self.approvals.append((run_id, request_id, choice))
        self._approved.set()

    async def stop(self, run_id: str) -> None:
        self.stopped.append(run_id)
        self._cancelled.set()
        await asyncio.sleep(0.05)  # the HTTP round trip: the event stream sees the cancel meanwhile


def _turn(text: str) -> LLMContextFrame:
    return LLMContextFrame(context=LLMContext(messages=[{"role": "user", "content": text}]))


def _kinds(frames):
    return [
        type(f)
        for f in frames
        if isinstance(
            f,
            (LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame, TTSSpeakFrame, OmiErrorFrame),
        )
    ]


@pytest.mark.asyncio
async def test_slow_agent_gets_a_filler_before_the_answer():
    agent = HermesAgent(FakeHermes(0.3, ["Hola."]), filler_after=0.1, fillers=["A ver."])
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.6)])

    assert _kinds(down) == [
        LLMFullResponseStartFrame,
        TTSSpeakFrame,
        LLMTextFrame,
        LLMFullResponseEndFrame,
    ]
    assert next(f for f in down if isinstance(f, TTSSpeakFrame)).text == "A ver."


@pytest.mark.asyncio
async def test_fast_agent_skips_the_filler():
    agent = HermesAgent(FakeHermes(0.0, ["Hola."]), filler_after=0.3, fillers=["A ver."])
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.6)])

    assert _kinds(down) == [LLMFullResponseStartFrame, LLMTextFrame, LLMFullResponseEndFrame]


@pytest.mark.asyncio
async def test_hermes_failure_is_said_out_loud_and_flagged():
    agent = HermesAgent(FakeHermes(0.0, [], fail=RuntimeError("run.failed: boom")), filler_after=None)
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.3)])

    assert _kinds(down) == [LLMFullResponseStartFrame, OmiErrorFrame, TTSSpeakFrame, LLMFullResponseEndFrame]
    assert next(f for f in down if isinstance(f, TTSSpeakFrame)).text == HERMES_ERROR
    assert next(f for f in down if isinstance(f, OmiErrorFrame)).text == HERMES_ERROR


@pytest.mark.asyncio
async def test_an_empty_reply_counts_as_a_failure():
    agent = HermesAgent(FakeHermes(0.0, []), filler_after=None)
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.3)])

    assert OmiErrorFrame in _kinds(down)


@pytest.mark.asyncio
async def test_barge_in_cancels_the_run_without_an_error():
    hermes = FakeHermes(0.2, ["Hola."])
    agent = HermesAgent(hermes, filler_after=None)
    down, _ = await run_test(
        agent,
        frames_to_send=[_turn("hola"), SleepFrame(0.05), InterruptionFrame(), SleepFrame(0.4)],
    )

    assert hermes.stopped == ["run-1"]
    assert OmiErrorFrame not in _kinds(down)
    assert TTSSpeakFrame not in _kinds(down)


@pytest.mark.asyncio
async def test_a_context_that_does_not_end_in_a_user_turn_is_not_answered():
    # Pipecat's empty-turn recovery appends a developer prompt; answering it would replay the last question.
    hermes = FakeHermes(0.0, ["Hola."])
    agent = HermesAgent(hermes, filler_after=None)
    context = LLMContext(
        messages=[
            {"role": "user", "content": "¿Qué día es hoy?"},
            {"role": "assistant", "content": "Sábado."},
            {"role": "developer", "content": "Briefly ask them to repeat what they said."},
        ]
    )
    await run_test(agent, frames_to_send=[LLMContextFrame(context=context), SleepFrame(0.2)])

    assert hermes.started == []


def _texts(frames, kind):
    return [f.text for f in frames if isinstance(f, kind)]


@pytest.mark.asyncio
async def test_a_turn_is_resent_while_hermes_is_down_and_answered_when_it_is_back():
    hermes = FakeHermes(0.0, ["Hola."], down=2)
    agent = HermesAgent(hermes, filler_after=None, retry_every=0.05, retries=3)
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.4)])

    assert hermes.started == ["hola", "hola", "hola"]
    assert _texts(down, TTSSpeakFrame) == [HERMES_DOWN]  # said once, not on every attempt
    assert _texts(down, OmiErrorFrame) == ["Hermes no responde. Reintento 1 de 3…", "Hermes no responde. Reintento 2 de 3…"]
    assert _texts(down, LLMTextFrame) == ["Hola."]


@pytest.mark.asyncio
async def test_retries_give_up_after_the_limit():
    hermes = FakeHermes(0.0, ["Hola."], down=99)
    agent = HermesAgent(hermes, filler_after=None, retry_every=0.05, retries=2)
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.4)])

    assert len(hermes.started) == 3
    assert _texts(down, OmiErrorFrame)[-1] == HERMES_ERROR
    assert _texts(down, TTSSpeakFrame) == [HERMES_DOWN, HERMES_ERROR]
    assert _kinds(down)[-1] is LLMFullResponseEndFrame


@pytest.mark.asyncio
async def test_retry_now_skips_the_wait():
    hermes = FakeHermes(0.0, ["Hola."], down=1)
    agent = HermesAgent(hermes, filler_after=None, retry_every=30, retries=3)
    asyncio.get_running_loop().call_later(0.1, agent.retry_now)
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.4)])

    assert _texts(down, LLMTextFrame) == ["Hola."]


@pytest.mark.asyncio
async def test_retry_now_after_giving_up_asks_the_same_question_again():
    hermes = FakeHermes(0.0, ["Hola."], down=1)
    agent = HermesAgent(hermes, filler_after=None, retry_every=0.05, retries=0)
    asyncio.get_running_loop().call_later(0.2, agent.retry_now)
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.5)])

    assert hermes.started == ["hola", "hola"]
    assert _texts(down, OmiErrorFrame) == [HERMES_ERROR]
    assert _texts(down, LLMTextFrame) == ["Hola."]


@pytest.mark.asyncio
async def test_closing_the_mic_stops_retrying():
    hermes = FakeHermes(0.0, ["Hola."], down=99)
    agent = HermesAgent(hermes, filler_after=None, retry_every=0.1, retries=5)
    asyncio.get_running_loop().call_later(0.05, agent.abandon_retry)
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.5)])

    assert hermes.started == ["hola"]
    assert HERMES_ERROR not in _texts(down, OmiErrorFrame)

    agent.retry_now()  # nothing pending any more
    assert hermes.started == ["hola"]


@pytest.mark.asyncio
async def test_a_failed_run_is_not_resent():
    # The run started, so Hermes may have used tools already: sending it again could repeat them.
    hermes = FakeHermes(0.0, [], fail=RuntimeError("run.failed: boom"))
    agent = HermesAgent(hermes, filler_after=None, retry_every=0.05, retries=3)
    await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.4)])

    assert hermes.started == ["hola"]


@pytest.mark.asyncio
async def test_the_card_line_is_never_spoken_and_arrives_as_a_card(tmp_path):
    f = tmp_path / "a.pdf"
    f.write_text("x")
    hermes = FakeHermes(0.0, ["Aquí ", "está. @", "@omi {\"files\": [\"" + str(f) + "\"], \"url\": \"https://omarchy.org\"}"])
    agent = HermesAgent(hermes, filler_after=None)
    down, _ = await run_test(agent, frames_to_send=[_turn("dame el archivo"), SleepFrame(0.3)])

    assert "".join(_texts(down, LLMTextFrame)) == "Aquí está. "
    card = next(f for f in down if isinstance(f, OmiCardFrame)).card
    assert card["url"] == "https://omarchy.org" and card["files"][0]["label"] == "a.pdf"
    assert _kinds(down)[-1] is LLMFullResponseEndFrame


@pytest.mark.asyncio
async def test_a_lone_at_sign_at_the_end_is_still_spoken():
    hermes = FakeHermes(0.0, ["Escríbeme a ana", "@"])
    agent = HermesAgent(hermes, filler_after=None)
    down, _ = await run_test(agent, frames_to_send=[_turn("x"), SleepFrame(0.3)])
    assert "".join(_texts(down, LLMTextFrame)) == "Escríbeme a ana@"


@pytest.mark.asyncio
async def test_tool_events_and_approvals_are_relayed_and_answered():
    hermes = FakeHermes(0.0, [{"event": "tool.started", "tool": "terminal"},
                              {"event": "approval.request", "request_id": "req-7", "tool": "terminal", "command": "rm -rf build"},
                              "Listo."])
    agent = HermesAgent(hermes, filler_after=None)
    asyncio.get_running_loop().call_later(0.1, lambda: asyncio.ensure_future(agent.approve("once")))
    down, _ = await run_test(agent, frames_to_send=[_turn("limpia el build"), SleepFrame(0.4)])

    assert next(f for f in down if isinstance(f, OmiWorkFrame)).tool == "terminal"
    ask = next(f for f in down if isinstance(f, OmiAskFrame))
    assert ask.request_id == "req-7" and "rm -rf build" in ask.question
    assert hermes.approvals == [("run-1", "req-7", "once")]
    assert await agent.approve("deny") is False  # nothing pending any more


@pytest.mark.asyncio
async def test_ask_starts_a_turn_without_the_microphone():
    hermes = FakeHermes(0.0, ["Guardado."])
    agent = HermesAgent(hermes, filler_after=None)
    asyncio.get_running_loop().call_later(0.05, lambda: asyncio.ensure_future(agent.ask("El usuario soltó /tmp/a.pdf")))
    down, _ = await run_test(agent, frames_to_send=[SleepFrame(0.4)])

    assert hermes.started == ["El usuario soltó /tmp/a.pdf"]
    assert _texts(down, LLMTextFrame) == ["Guardado."]


@pytest.mark.asyncio
async def test_mic_turns_are_enriched_before_hermes_sees_them():
    from omadot.context import Turn

    hermes = FakeHermes(0.0, ["Es Herdr."])
    agent = HermesAgent(hermes, filler_after=None, enrich=lambda said: Turn(said + "\n\n[Screenshot: /tmp/s.png]", {"shot": "/tmp/s.png"}))
    down, _ = await run_test(agent, frames_to_send=[_turn("¿qué es esto?"), SleepFrame(0.3)])

    assert hermes.started == ["¿qué es esto?\n\n[Screenshot: /tmp/s.png]"]
    assert next(f for f in down if isinstance(f, OmiCardFrame)).card == {"shot": "/tmp/s.png"}


def test_fillers_do_not_repeat_within_four_turns():
    from omadot.agent import FILLERS

    agent = HermesAgent(FakeHermes(0.0, []), filler_after=None)
    picks = [agent.pick_filler() for _ in range(40)]
    assert set(picks) <= set(FILLERS) and len(set(picks)) >= 8
    for i in range(3, 40):
        assert picks[i] not in picks[i - 3:i]


@pytest.mark.asyncio
async def test_words_after_the_card_line_are_still_spoken():
    hermes = FakeHermes(0.0, ["Aquí tienes. @@omi {\"url\": \"https://a.com\"}", " Por cierto, gracias."])
    agent = HermesAgent(hermes, filler_after=None)
    down, _ = await run_test(agent, frames_to_send=[_turn("x"), SleepFrame(0.3)])
    assert "".join(_texts(down, LLMTextFrame)).split() == ["Aquí", "tienes.", "Por", "cierto,", "gracias."]
    assert next(f for f in down if isinstance(f, OmiCardFrame)).card == {"url": "https://a.com"}


class FakeFast:
    def __init__(self, text="", escalated=False, delay=0.05):
        self.text, self.escalated, self.delay = text, escalated, delay
        self.asked = []

    async def ask(self, text):
        from omadot.fastlane import FastReply

        self.asked.append(text)
        await asyncio.sleep(self.delay)
        return FastReply(text=self.text, escalated=self.escalated, ms=int(self.delay * 1000))


@pytest.mark.asyncio
async def test_fast_lane_answers_first_and_hermes_is_stopped():
    hermes = FakeHermes(0.5, ["Respuesta lenta."])
    agent = HermesAgent(hermes, filler_after=None, fast=FakeFast("Son las cinco."))
    down, _ = await run_test(agent, frames_to_send=[_turn("¿qué hora es?"), SleepFrame(0.8)])
    assert _texts(down, LLMTextFrame) == ["Son las cinco."]
    assert hermes.stopped == ["run-1"]
    assert _kinds(down)[-1] is LLMFullResponseEndFrame and OmiErrorFrame not in _kinds(down)


@pytest.mark.asyncio
async def test_fast_lane_escalates_and_hermes_answers():
    hermes = FakeHermes(0.1, ["Aquí está el archivo."])
    agent = HermesAgent(hermes, filler_after=None, fast=FakeFast(escalated=True))
    down, _ = await run_test(agent, frames_to_send=[_turn("dame el archivo"), SleepFrame(0.5)])
    assert _texts(down, LLMTextFrame) == ["Aquí está el archivo."] and hermes.stopped == []


@pytest.mark.asyncio
async def test_hermes_first_makes_the_fast_lane_moot():
    hermes = FakeHermes(0.0, ["Hola."])
    fast = FakeFast("Hola rápido.", delay=0.4)
    agent = HermesAgent(hermes, filler_after=None, fast=fast)
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.7)])
    assert _texts(down, LLMTextFrame) == ["Hola."] and hermes.stopped == []


@pytest.mark.asyncio
async def test_fast_lane_can_be_switched_off():
    hermes = FakeHermes(0.0, ["Hola."])
    fast = FakeFast("Hola rápido.")
    agent = HermesAgent(hermes, filler_after=None, fast=fast)
    agent.fast_enabled = False
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.3)])
    assert fast.asked == [] and _texts(down, LLMTextFrame) == ["Hola."]


@pytest.mark.asyncio
async def test_fast_lane_can_be_attached_after_start():
    # Opt-in at runtime (`omadot set fast on`): the lane starts later than the agent.
    hermes = FakeHermes(0.3, ["Hola."])
    agent = HermesAgent(hermes, filler_after=None)
    assert agent.fast_enabled is False
    fast = FakeFast("Hola rápido.")
    agent.use_fast(fast)
    down, _ = await run_test(agent, frames_to_send=[_turn("hola"), SleepFrame(0.7)])
    assert fast.asked == ["hola"] and _texts(down, LLMTextFrame)[0] == "Hola rápido."
