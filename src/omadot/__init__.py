"""OmaDot: local voice loop (faster-whisper -> Hermes -> Kokoro), summoned by pressing Super twice.

`omadot` starts the daemon (mic closed). Control it with `omadot toggle` (open/close the mic), `omadot retry`
(ask Hermes again now), `omadot set KEY VALUE` (settings), `omadot status` (JSON) and, through the command
socket: `tap` (Super released alone; two taps within 0.4 s toggle the mic), `drop PATH`, `ask TEXT…`,
`approve once|deny`, `catch on|off`, `dismiss`,
`open TARGET`, `copy`, `replace`, `progress ID PCT [LABEL] [DETAIL] [WHERE]`, `action KIND ID OP`,
`mute [on|off]`, `remind AT TEXT…`, `focus MINUTES`, `reminders`, `repeat`, `say TEXT…`.
Settings: voice, speed, speak, transcript, sleep, fast, tts, omni_voice.
Install: `omadot setup echo-cancel|kokoro|omni|hermes|plugin|service|binds` (see omadot.install).
"""

import argparse
import asyncio
import json
import os
import re
import signal
import sys
import time
from dataclasses import asdict
from pathlib import Path

from omadot.ipc import SOCKET, send
from omadot.payload import PROTOCOL
from omadot.settings import ENGINES, SETTINGS_FILE, SPEED_RANGE, VOICES, load, set_value
from omadot.state import SLEEP_AFTER, DoubleTap, should_sleep, toggle_action

PIDFILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "omadot.pid"
# Sent with every turn so replies fit a voice: the profile's SOUL/memory stay untouched.
VOICE_INSTRUCTIONS = (
    "You are Omi, speaking out loud through a desktop voice assistant on Omarchy (Hyprland). Reply in the language "
    "of the user's latest message, even if earlier turns were in another language, in one to three short spoken sentences. No markdown, lists, code blocks, emojis "
    "or URLs in the spoken part. If a task takes tools, first say in a few words what you are doing, then do it. "
    "Lines in [brackets] after the user's words come from the desktop (active window, clipboard, selection, "
    "a screenshot path), not from the user: use them, do not read them back. The [Now: …] line is the current local "
    "date and time: answer any question about the time, the date or how long until something from it, without tools. "
    + PROTOCOL
)
FILLER_AFTER = 2.0  # a filler only when Hermes is really slow; a quick "hola" should not get "déjame ver"
STATE_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "omadot" / "state"
AEC_SOURCE = "omadot_aec_source"
AEC_SINK = "omadot_aec_sink"


def _cuda_libs() -> None:
    """faster-whisper needs cuBLAS/cuDNN 12; they ship as wheels inside the venv."""
    import nvidia

    root = Path(next(iter(nvidia.__path__)))
    libs = ":".join(str(root / n / "lib") for n in ("cublas", "cudnn"))
    if libs not in os.environ.get("LD_LIBRARY_PATH", ""):
        os.environ["LD_LIBRARY_PATH"] = f"{libs}:{os.environ.get('LD_LIBRARY_PATH', '')}"
        os.execv(sys.executable, [sys.executable, "-m", "omadot", *sys.argv[1:]])


def _pyaudio_device(name: str) -> int | None:
    import pyaudio

    pa = pyaudio.PyAudio()
    try:
        for i in range(pa.get_device_count()):
            if pa.get_device_info_by_index(i)["name"] == name:
                return i
        return None
    finally:
        pa.terminate()


def aec_route(sources: set[str], sinks: set[str]) -> dict[str, str]:
    """Route audio through the echo-cancel nodes only if both exist; a missing source leaves the mic unopenable."""
    if AEC_SOURCE in sources and AEC_SINK in sinks:
        return {"PULSE_SOURCE": AEC_SOURCE, "PULSE_SINK": AEC_SINK}
    return {}


def _pulse_names(kind: str) -> set[str]:
    import subprocess

    try:
        out = subprocess.run(
            ["pactl", "list", "short", kind], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {cols[1] for line in out.splitlines() if len(cols := line.split("\t")) > 1}


CONTROL_SIGNALS = {signal.SIGUSR1, signal.SIGUSR2, signal.SIGHUP}  # toggle, retry, reload settings


def unblock_control_signals() -> None:
    """Some terminals spawn children with SIGUSR1 blocked, so `omadot toggle` would stay pending forever.

    Call before any thread starts: threads inherit the mask they are created with.
    """
    signal.pthread_sigmask(signal.SIG_UNBLOCK, CONTROL_SIGNALS)


def turn_strategies():
    from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
    from pipecat.turns.user_turn_strategies import UserTurnStrategies

    # Smart Turn decides when you finished; when it doubts, give up after 1 s of silence (default 3 s).
    analyzer = LocalSmartTurnAnalyzerV3(params=SmartTurnParams(stop_secs=1.0))
    return UserTurnStrategies(stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=analyzer)])


def make_task(pipeline, observers):
    from pipecat.pipeline.task import PipelineParams, PipelineTask

    # The daemon waits to be summoned indefinitely: no idle timeout (Pipecat's default cancels after 5 min).
    return PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True),
        observers=observers,
        idle_timeout_secs=None,
    )


def _start_card():
    """The invocation card is its own Quickshell instance (the package's card/), like voxtype's OSD. Its buttons
    run `omadot …` through OMADOT_BIN: the binary this daemon runs from, wherever it was installed."""
    import shutil
    import subprocess

    from omadot.install import card_dir, omadot_bin

    qs = shutil.which("qs")
    if not qs:
        return None
    env = {**os.environ, "OMADOT_BIN": str(omadot_bin())}
    return subprocess.Popen([qs, "-p", str(card_dir())], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _daemon_pid() -> int | None:
    try:
        pid = int(PIDFILE.read_text())
        os.kill(pid, 0)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return None
    return pid


def _signal_daemon(sig: signal.Signals) -> bool:
    pid = _daemon_pid()
    if pid is None:
        return False
    os.kill(pid, sig)
    return True


def _hermes_target(args) -> tuple[str, Path]:
    """One host gateway serves every Hermes profile; a profile is reached at /p/<name>/ with its own key."""
    from omadot.install import Paths

    hermes_home = Paths.from_env().hermes
    if args.profile == "default":
        return args.hermes, hermes_home
    return f"{args.hermes}/p/{args.profile}", hermes_home / "profiles" / args.profile


def shortcut(bindings: str) -> str:
    """The Hyprland bind that summons OmaDot, as people write it ("Super × 2", "Super + .")."""
    m = re.search(r'bind\(\s*"([^"]+)"[^\n]*omadot (toggle|tap)\b', bindings)
    if not m:
        return ""
    names = {"SUPER": "Super", "SUPER_L": "Super", "SUPER_R": "Super", "CTRL": "Ctrl", "ALT": "Alt", "SHIFT": "Shift", "PERIOD": ".", "COMMA": ",", "SPACE": "Space"}
    # "SUPER + Super_L" (the lone-Super release bind) reads as one key.
    keys = " + ".join(dict.fromkeys(names.get(k.strip().upper(), k.strip()) for k in m.group(1).split("+")))
    return f"{keys} × 2" if m.group(2) == "tap" else keys


def status(args) -> dict:
    """Everything the settings panel shows, in one call."""
    import httpx
    import yaml

    base_url, hermes_home = _hermes_target(args)
    try:
        model = (yaml.safe_load((hermes_home / "config.yaml").read_text()) or {}).get("model") or {}
    except (OSError, yaml.YAMLError):
        model = {}
    try:
        connected = httpx.get(f"{base_url}/health", timeout=2.0).status_code == 200
    except httpx.HTTPError:
        connected = False
    try:
        bindings = (Path.home() / ".config/hypr/bindings.lua").read_text()
    except OSError:
        bindings = ""
    return {
        "running": _daemon_pid() is not None,
        "settings": asdict(load()),
        "voices": list(VOICES),
        "engines": list(ENGINES),
        "speedRange": list(SPEED_RANGE),
        "sleepAfterMin": round(args.sleep_after / 60),
        "shortcut": shortcut(bindings),
        "hermes": {
            "endpoint": base_url,
            "profile": args.profile,
            "model": model.get("default", ""),
            "provider": model.get("provider", ""),
            "connected": connected,
        },
    }


async def run(args) -> None:
    import numpy as np
    from loguru import logger
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        Frame,
        InputAudioRawFrame,
        InterruptionWorkerFrame,
        LLMFullResponseEndFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        TranscriptionFrame,
        TTSSpeakFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.observers.base_observer import BaseObserver, FramePushed
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
    from pipecat.services.whisper.stt import WhisperSTTService
    from pipecat.transcriptions.language import Language
    from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams

    from omadot import context as desktop
    from omadot.agent import HermesAgent, OmiAskFrame, OmiCardFrame, OmiErrorFrame, OmiWorkFrame
    from omadot.commands import TICK, Commands
    from omadot.fastlane import FastLane
    from omadot.hermes import HermesClient, read_api_key
    from omadot.ipc import CommandServer
    from omadot.kokoro import KokoroTTSService
    from omadot.services import Reminders
    from omadot.state import StatePublisher

    settings = load()
    state = StatePublisher(STATE_FILE, mic_open=args.open, settings=settings)
    last_activity = time.monotonic()
    commands: Commands | None = None

    def touch():
        nonlocal last_activity
        last_activity = time.monotonic()

    class StateObserver(BaseObserver):
        """Feeds pipeline events to the state file the bar widget watches."""

        EVENTS = {
            UserStartedSpeakingFrame: "user_started",
            LLMFullResponseStartFrame: "reply_started",
            LLMFullResponseEndFrame: "reply_ended",
            BotStartedSpeakingFrame: "bot_started",
            BotStoppedSpeakingFrame: "bot_stopped",
        }

        def __init__(self):
            super().__init__()
            self._seen: set[int] = set()

        async def on_push_frame(self, data: FramePushed):
            f = data.frame
            if f.id in self._seen:
                return
            if isinstance(f, TranscriptionFrame):
                state.user_said(f.text)
            elif isinstance(f, LLMTextFrame):
                state.omi_said(f.text)
            elif isinstance(f, OmiErrorFrame):
                state.error(f.text)
            elif isinstance(f, OmiCardFrame):
                (commands.on_card if commands else state.show)(f.card)
            elif isinstance(f, OmiWorkFrame):
                state.working(f.tool)
            elif isinstance(f, OmiAskFrame):
                state.approval(f.question, f.request_id)
            elif type(f) in self.EVENTS:
                getattr(state, self.EVENTS[type(f)])()
            else:
                return
            self._seen.add(f.id)
            touch()

    class MicGate(FrameProcessor):
        """Drops mic audio unless OmaDot is listening (Super twice toggles it)."""

        def __init__(self, open_: bool):
            super().__init__()
            self.open = open_

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame) and not self.open:
                return
            await self.push_frame(frame, direction)

    class VoiceGate(FrameProcessor):
        """With the voice off Omi answers in the card only: no text reaches the TTS."""

        def __init__(self, speak: bool):
            super().__init__()
            self.speak = speak

        async def process_frame(self, frame: Frame, direction: FrameDirection):
            await super().process_frame(frame, direction)
            if not self.speak and isinstance(frame, LLMTextFrame | TTSSpeakFrame):
                return
            await self.push_frame(frame, direction)

    class Latency(BaseObserver):
        """Prints, per turn, time from end of speech to transcript, first token, first audio."""

        def __init__(self):
            super().__init__()
            self._seen: set[int] = set()
            self._t: dict[str, float] = {}

        async def on_push_frame(self, data: FramePushed):
            f = data.frame
            if f.id in self._seen:
                return
            self._seen.add(f.id)
            now = time.perf_counter()
            # "stop" is the last VAD end-of-speech before the reply; a pause mid-sentence resets it.
            if isinstance(f, VADUserStoppedSpeakingFrame):
                self._t = {"stop": now}
            elif isinstance(f, TranscriptionFrame):
                self._t.setdefault("stt", now)
                logger.info(f"tú: {f.text}")
            elif isinstance(f, UserStoppedSpeakingFrame):
                self._t.setdefault("turn", now)
            elif isinstance(f, LLMTextFrame):
                if "token" not in self._t and "stop" in self._t:
                    self._t["token"] = now
                    logger.info(f"primer token de Hermes: {round((now - self._t['stop']) * 1000)} ms desde fin de voz")
            elif isinstance(f, BotStartedSpeakingFrame) and "stop" in self._t and "audio" not in self._t:
                t, s = self._t, self._t["stop"]
                self._t["audio"] = now
                ms = {k: round((t[k] - s) * 1000) for k in ("stt", "turn", "token") if k in t}
                logger.success(f"latencia (ms desde fin de voz): {ms} audio={round((now - s) * 1000)}" + (" (relleno)" if "token" not in t else ""))
            elif isinstance(f, LLMFullResponseEndFrame):
                self._t = {}

    if "PULSE_SOURCE" not in os.environ and "PULSE_SINK" not in os.environ:
        route = aec_route(_pulse_names("sources"), _pulse_names("sinks"))
        if not route:
            logger.warning(f"echo-cancel nodes ({AEC_SOURCE}) missing: using the default mic/speakers")
        os.environ.update(route)

    stt = WhisperSTTService(
        settings=WhisperSTTService.Settings(model="large-v3-turbo", language=Language.ES),
        device="cuda",
        compute_type="float16",
    )
    t0 = time.perf_counter()
    list(stt._model.transcribe(np.zeros(16000, dtype=np.float32), language="es")[0])
    logger.info(f"whisper warm in {time.perf_counter() - t0:.1f}s")

    base_url, hermes_home = _hermes_target(args)
    hermes = HermesClient(
        base_url,
        read_api_key(hermes_home / ".env"),
        session_id=args.session,
        instructions=VOICE_INSTRUCTIONS,
    )
    # The ALSA "pulse" device honours PULSE_SOURCE/PULSE_SINK; "default" (pipewire-alsa) does not.
    pulse = _pyaudio_device("pulse")
    transport = LocalAudioTransport(
        LocalAudioTransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            input_device_index=pulse,
            output_device_index=pulse,
        )
    )
    context = LLMContext()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
            user_turn_strategies=turn_strategies(),
        ),
    )

    @aggregators.user().event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        # The turn's final transcript: empty means Hermes will not run, so Omi must not stay "thinking".
        state.turn_ended(message.content or "")

    gate = MicGate(open_=args.open)
    fast = None

    async def start_fast():
        # Started only on demand: off by default, it must not even spawn omp.
        nonlocal fast
        lane = FastLane(model=args.fast_model, profile=args.fast_profile or None)
        if await lane.start():
            fast = lane
            agent.use_fast(lane, enabled=fast_lane_wanted(settings, args.fast))

    agent = HermesAgent(hermes, filler_after=None if args.no_filler else args.filler_after, enrich=None if args.no_context else desktop.enrich)
    if fast_lane_wanted(settings, args.fast):
        await start_fast()
    voice = VoiceGate(settings.speak)
    tts = KokoroTTSService(base_url=args.kokoro or settings.tts_url, voice=settings.tts_voice, speed=settings.speed, lang_code="e")
    pipeline = Pipeline(
        [
            transport.input(),
            gate,
            stt,
            aggregators.user(),
            agent,
            voice,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )
    task = make_task(pipeline, observers=[Latency(), StateObserver()])

    async def hush():
        # Stop Hermes, then the voice: the worker turns InterruptionWorkerFrame into the barge-in interruption.
        await agent.hush()
        await task.queue_frame(InterruptionWorkerFrame())

    def on_toggle():
        if toggle_action(gate.open, state.state) == "open":
            gate.open = True
            state.set_mic(True)
            logger.info("🎙 escuchando")
        else:
            gate.open = False
            agent.abandon_retry()
            if state.state in ("thinking", "working", "speaking"):
                asyncio.ensure_future(hush())
            state.set_mic(False)
            state.dismiss()
            logger.info("🎙 micrófono cerrado, tarjeta cerrada")
        touch()

    taps = DoubleTap()

    def on_tap():
        if taps.tap(time.monotonic()):
            on_toggle()

    def on_mute(muted: bool):
        # Muting closes the mic; unmuting leaves it closed (Super twice opens it): nobody is listened to by surprise.
        if muted:
            gate.open = False
            agent.abandon_retry()
        touch()
        logger.info("🔇 micrófono apagado" if muted else "🔈 silencio quitado; Super dos veces para hablar")

    async def say(text: str):
        await task.queue_frame(TTSSpeakFrame(text))

    commands = Commands(state, agent, reminders=Reminders(), on_mute=on_mute, say=say)
    server = CommandServer(SOCKET, {
        **commands.handlers(),
        "toggle": lambda req: on_toggle(),
        "tap": lambda req: on_tap(),
        "retry": lambda req: agent.retry_now(),
        "reload": lambda req: on_reload(),
    })

    async def clock():
        while True:
            await asyncio.sleep(TICK)
            try:
                commands.tick()
            except Exception as e:  # a bad reminder file must not stop the loop
                logger.warning(f"tick failed: {e}")

    def on_reload():
        nonlocal settings
        settings = load()
        tts.configure(settings.tts_voice, settings.speed, args.kokoro or settings.tts_url)
        voice.speak = settings.speak
        agent.fast_enabled = fast is not None and fast_lane_wanted(settings, args.fast)
        if fast is None and fast_lane_wanted(settings, args.fast):
            asyncio.ensure_future(start_fast())
        state.configure(settings)
        logger.info(f"ajustes: {settings}")

    async def doze():
        while True:
            await asyncio.sleep(5)
            if gate.open and should_sleep(settings, state.state, time.monotonic() - last_activity, args.sleep_after):
                gate.open = False
                agent.abandon_retry()
                state.sleep()
                logger.info("💤 nadie habló en un rato: micrófono cerrado")

    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGUSR1, on_toggle)
    loop.add_signal_handler(signal.SIGUSR2, agent.retry_now)
    loop.add_signal_handler(signal.SIGHUP, on_reload)
    PIDFILE.write_text(str(os.getpid()))
    await server.start()
    card = None if args.no_card else _start_card()
    dozing = asyncio.create_task(doze())
    ticking = asyncio.create_task(clock())
    logger.info(f"listo. mic {'abierto' if args.open else 'cerrado'}; Super dos veces (`omadot tap` ×2) u `omadot toggle` para cambiar")
    try:
        await PipelineRunner(handle_sigint=True).run(task)
    finally:
        dozing.cancel()
        ticking.cancel()
        await server.close()
        PIDFILE.unlink(missing_ok=True)
        state.close()
        if card:
            card.terminate()
        if fast:
            await fast.close()
        await hermes.aclose()


def fast_lane_wanted(settings, flag: bool) -> bool:
    """The fast lane is opt-in: it sends each turn to Anthropic with your Claude account through omp."""
    return bool(settings.fast or flag)


def _params(*names, rest=None):
    """Positional CLI params -> the request's fields; `rest` joins everything after the named ones."""

    def build(params: list[str]) -> dict:
        if len(params) < len([n for n in names if not n.endswith("?")]):
            raise ValueError(f"usage: omadot … {' '.join(n.upper() for n in names)}{' ' + rest.upper() + '…' if rest else ''}")
        out = {n.rstrip("?"): v for n, v in zip(names, params) if v != "" or not n.endswith("?")}
        if rest:
            out[rest] = " ".join(params[len(names):])
        return out

    return build


# command -> how its positional params become the socket request (see commands.Commands)
SOCKET_COMMANDS = {
    "tap": _params(),
    "drop": _params("path"),
    "ask": _params(rest="text"),
    "approve": _params("choice?"),
    "catch": _params("on?"),
    "dismiss": _params(),
    "open": _params("target", "mode?"),
    "copy": _params(rest="text"),
    "replace": _params(rest="text"),
    "progress": _params("id", "pct", "label?", "detail?", "where?"),
    "action": _params("kind", "id", "op", "minutes?"),
    "mute": _params("on?"),
    "remind": _params("at", rest="text"),
    "focus": _params("minutes?"),
    "reminders": _params(),
    "repeat": _params(),
    "say": _params(rest="text"),
}


def main() -> None:
    p = argparse.ArgumentParser(prog="omadot")
    p.add_argument("command", nargs="?", choices=["run", "toggle", "retry", "set", "status", "setup", *SOCKET_COMMANDS], default="run")
    p.add_argument("params", nargs="*", help="set: KEY VALUE (voice, speed, speak, transcript, sleep, fast, tts, omni_voice); setup: TARGET; see the module doc for the rest")
    p.add_argument("--open", action="store_true", help="start with the mic open")
    p.add_argument("--hermes", default="http://127.0.0.1:8642")
    p.add_argument("--profile", default="omadot", help="Hermes profile ('default' for the root one)")
    p.add_argument("--no-card", action="store_true", help="don't show the transcript card under the bar")
    p.add_argument("--no-filler", action="store_true", help="never say a filler while Hermes thinks")
    p.add_argument("--filler-after", type=float, default=FILLER_AFTER, help="seconds without a first token before Omi says a filler")
    p.add_argument("--no-context", action="store_true", help="don't attach the active window, clipboard or screenshots to turns")
    p.add_argument("--fast", action="store_true", help="turn the fast lane (Haiku through omp) on for this run; `omadot set fast on` keeps it on")
    p.add_argument("--fast-model", default="haiku", help="omp model for the fast lane")
    p.add_argument("--fast-profile", default="", help="omp --profile for the fast lane's auth")
    p.add_argument("--kokoro", default="", help="speech server URL; by default the one the `tts` setting picks (Kokoro :8880, OmniVoice :8882)")
    p.add_argument("--session", default="omadot")
    p.add_argument("--sleep-after", type=float, default=SLEEP_AFTER, help="seconds of silence before the mic closes")
    p.add_argument("--no-enable", action="store_true", help="setup: write the files but don't start or enable anything")
    p.add_argument("--dry-run", action="store_true", help="setup hermes: print the hermes commands (keys masked) without running them")
    args = p.parse_args()
    if args.command in ("toggle", "retry"):
        sig = signal.SIGUSR1 if args.command == "toggle" else signal.SIGUSR2
        if not _signal_daemon(sig):
            sys.exit("omadot is not running")
    elif args.command == "set":
        if len(args.params) != 2:
            p.error("usage: omadot set KEY VALUE")
        try:
            set_value(SETTINGS_FILE, *args.params)
        except ValueError as e:
            sys.exit(str(e))
        _signal_daemon(signal.SIGHUP)  # a stopped daemon reads the file when it starts
    elif args.command == "setup":
        from omadot.install import TARGETS, SetupError, setup

        if len(args.params) != 1 or args.params[0] not in TARGETS:
            p.error(f"usage: omadot setup {{{','.join(TARGETS)}}}")
        try:
            for note in setup(args.params[0], profile=args.profile, enable=not args.no_enable, dry_run=args.dry_run):
                print(note)
        except SetupError as e:
            sys.exit(f"omadot setup {args.params[0]}: {e}")
    elif args.command == "status":
        print(json.dumps(status(args), ensure_ascii=False))
    elif args.command in SOCKET_COMMANDS:
        try:
            reply = send(SOCKET, args.command, **SOCKET_COMMANDS[args.command](args.params))
        except (ConnectionError, ValueError) as e:
            sys.exit(str(e))
        if not reply.get("ok"):
            sys.exit(reply.get("error", "failed"))
        if len(reply) > 1:
            print(json.dumps({k: v for k, v in reply.items() if k != "ok"}, ensure_ascii=False))
    else:
        unblock_control_signals()
        _cuda_libs()
        asyncio.run(run(args))
