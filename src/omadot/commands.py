"""What the daemon does with each command from the socket, each card the reply carries, and each tick of the clock.

Everything here is plain Python over injected collaborators (state, agent, a command runner, a clock) so the
flows are testable without a microphone: a dropped file becomes a turn, a button press becomes a turn or an
approval, a reminder rings as a notification, a card lingers and then goes away.
"""

import asyncio
import subprocess
import time
from pathlib import Path

from loguru import logger

from omadot import context
from omadot.services import Focus, Reminders, Tasks, parse_when
from omadot.state import StatePublisher

DROP_PROMPT = (
    "The user dropped a file on you: {path}. Move it into the folder where it belongs under ~/Documents "
    "(by its content: Finance, Work, Personal, Photos…; create the folder if needed; keep the name), then say in one "
    "sentence where you put it and what it is, and ask whether they want a reminder or anything else. "
    "Include the new path in the files of your @@omi line."
)
DROP_LABEL = "📎 {name}"
MINI_W, MINI_H = 760, 640  # the small browser next to the card
TICK = 1.0


def _onoff(value) -> bool:
    v = str(value).lower()
    if v in ("on", "true", "1", "yes"):
        return True
    if v in ("off", "false", "0", "no"):
        return False
    raise ValueError(f"expected on or off, got {value!r}")


def _minutes(value) -> float:
    try:
        m = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"minutes must be a number, got {value!r}") from None
    if not 0 < m <= 24 * 60:
        raise ValueError(f"minutes must be between 0 and 1440, got {value!r}")
    return m


def notify_send(summary: str, body: str = "", run=context.run) -> None:
    """Fire and forget: never waits for the notification daemon."""
    if run is context.run:
        try:
            subprocess.Popen(["notify-send", "-a", "OmaDot", summary, body], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass
    else:
        run(["notify-send", "-a", "OmaDot", summary, body])


class Commands:
    def __init__(
        self,
        state: StatePublisher,
        agent,
        *,
        reminders: Reminders,
        tasks: Tasks | None = None,
        run=context.run,
        clock=time.time,
        on_mute=None,
        say=None,
        clipboard_path: Path = context.CLIPBOARD,
        shots: Path = context.SHOTS,
    ):
        self.state = state
        self.agent = agent
        self.reminders = reminders
        self.tasks = tasks or Tasks()
        self.focus: Focus | None = None
        self._run = run
        self._clock = clock
        self._on_mute = on_mute
        self._say = say  # speaks a text through the pipeline (no Hermes)
        self._clipboard_path = clipboard_path
        self._shots = shots
        self._lingering: tuple[str, dict, float] | None = None  # (state, card, since)
        self._rewrite: dict = {}

    def handlers(self) -> dict:
        return {
            "drop": self.drop, "ask": self.ask, "approve": self.approve, "catch": self.catch, "dismiss": self.dismiss,
            "open": self.open, "copy": self.copy, "replace": self.replace, "progress": self.progress, "action": self.action,
            "mute": self.mute, "remind": self.remind, "focus": self.start_focus, "reminders": self.list_reminders,
            "repeat": self.repeat, "say": self.say,
        }

    # ---------------------------------------------------------------- the voice without Hermes
    async def say(self, req: dict) -> dict:
        """Speak a text as Omi (a script, a notification you want read out loud)."""
        text = " ".join(str(req.get("text", "")).split())
        if not text:
            raise ValueError("nothing to say")
        if not self._say:
            raise ValueError("the voice is not available")
        await self._say(text)
        return {}

    async def repeat(self, req: dict) -> dict:
        """Say the last reply again ("repite")."""
        text = self.state.last_reply
        if not text:
            raise ValueError("nothing to repeat yet")
        return await self.say({"text": text})

    # ---------------------------------------------------------------- turns that do not come from the mic
    async def drop(self, req: dict) -> dict:
        path = str(req.get("path", "")).strip()
        if not path or not Path(path).exists():
            raise ValueError(f"no such file: {path!r}")
        self.state.catch(False)
        self.state.user_started()
        self.state.user_said(DROP_LABEL.format(name=Path(path).name))
        self.state.turn_ended(path)
        await self.agent.ask(DROP_PROMPT.format(path=path))
        return {}

    async def ask(self, req: dict) -> dict:
        text = " ".join(str(req.get("text", "")).split())
        if not text:
            raise ValueError("nothing to ask")
        self.state.user_started()
        self.state.user_said(text)
        turn = await asyncio.to_thread(context.enrich, text, run=self._run, clipboard_path=self._clipboard_path, shots=self._shots, now=self._clock)
        if turn.card:
            self.state.show(turn.card)
        self.state.turn_ended(text)
        await self.agent.ask(turn.text)
        return {}

    async def approve(self, req: dict) -> dict:
        choice = str(req.get("choice", "once"))
        if choice not in ("once", "session", "always", "deny"):
            raise ValueError(f"bad choice {choice!r}")
        answered = await self.agent.approve(choice)
        self.state.approved()
        return {"answered": answered}

    # ---------------------------------------------------------------- the card
    def catch(self, req: dict) -> dict:
        self.state.catch(_onoff(req.get("on", "on")))
        return {}

    def dismiss(self, req: dict) -> dict:
        self.state.dismiss()
        self._lingering = None
        return {}

    async def open(self, req: dict) -> dict:
        """xdg-open a file or URL; with mode "mini", a page opens as a small floating Chromium under the card."""
        target = str(req.get("target", ""))
        if not target:
            raise ValueError("nothing to open")
        if str(req.get("mode", "")) == "mini" and target.startswith(("http://", "https://")):
            # Hyprland floats and places the window; the card (380 wide, 12 from the right edge) stays above it.
            await asyncio.to_thread(self._run, ["hyprctl", "dispatch", "exec", f"[float; size {MINI_W} {MINI_H}; move 100%-{MINI_W + 12} 60] chromium --app={target} --new-window"], 5.0, False)
        else:
            self._spawn(["xdg-open", target])
        return {}

    async def copy(self, req: dict) -> dict:
        text = str(req.get("text") or self._rewrite.get("after", ""))
        if not text:
            raise ValueError("nothing to copy")
        await asyncio.to_thread(self._run, ["wl-copy", "--", text], 5.0, False)
        return {}

    async def replace(self, req: dict) -> dict:
        """Put the rewritten text where the selection was: copy it and type it into the focused window."""
        text = str(req.get("text") or self._rewrite.get("after", ""))
        if not text:
            raise ValueError("nothing to replace with")
        await asyncio.to_thread(self._run, ["wl-copy", "--", text], 5.0, False)
        await asyncio.to_thread(self._run, ["wtype", "--", text], 30, False)
        self.state.show({"rewrite": {**self._rewrite, "replaced": True}})
        return {}

    def on_card(self, card: dict) -> None:
        """Side effects of what the reply carries: schedule, start timers, remember the rewrite."""
        if r := card.get("reminder"):
            at = parse_when(r.get("at"))
            if at is not None:
                rem = self.reminders.add(str(r.get("text", "")), at)
                card = {**card, "reminder": {**r, "id": rem.id}}
            else:
                logger.warning(f"reminder without a usable time: {r}")
        if f := card.get("focus"):
            try:
                self.focus = Focus(minutes=_minutes(f.get("minutes", 25)), started=self._clock())
                self.state.pill(self.focus.pill(self._clock()))
            except ValueError as e:
                logger.warning(f"focus ignored: {e}")
        if t := card.get("task"):
            self.tasks.update(str(t.get("id", "task")), float(t.get("progress", 0) or 0), str(t.get("label", "")), str(t.get("detail", "")), str(t.get("where", "")))
            self.state.pill(self._pill())
        if rw := card.get("rewrite"):
            self._rewrite = dict(rw)
        self.state.show(card)

    # ---------------------------------------------------------------- services
    def progress(self, req: dict) -> dict:
        t = self.tasks.update(str(req["id"]), float(req.get("pct", 0)), str(req.get("label", "")), str(req.get("detail", "")), str(req.get("where", "")))
        self.state.pill(self._pill())
        card = {"id": t.id, "label": t.label, "progress": t.progress, "detail": t.detail, "where": t.where}
        if t.progress >= 100:
            card["detail"] = "listo"
            self.state.notify(f"{t.label}: listo", [], card={"task": card})  # buttons_for adds "Abrir carpeta" from the task
            notify_send("OmaDot", f"{t.label}: listo", self._run)
            self.tasks.finish(t.id)
        else:
            self.state.ambient({"task": card})
        return {"pill": self.state._pill}

    def remind(self, req: dict) -> dict:
        at = parse_when(req.get("at"))
        if at is None:
            raise ValueError(f"when? {req.get('at')!r} is not ISO 8601")
        r = self.reminders.add(str(req.get("text", "")), at)
        self.state.ambient({"reminder": {"id": r.id, "text": r.text, "at": req.get("at")}})
        return {"id": r.id}

    def list_reminders(self, req: dict) -> dict:
        from datetime import datetime

        return {"reminders": [{**r.__dict__, "when": datetime.fromtimestamp(r.at).strftime("%Y-%m-%d %H:%M")} for r in self.reminders.pending()]}

    def start_focus(self, req: dict) -> dict:
        self.focus = Focus(minutes=_minutes(req.get("minutes", 25)), started=self._clock())
        self.state.pill(self.focus.pill(self._clock()))
        return {"pill": self.state._pill}

    def action(self, req: dict) -> dict:
        kind, rid, op = str(req.get("kind", "")), str(req.get("id", "")), str(req.get("op", ""))
        now = self._clock()
        if kind == "reminder":
            if op == "done":
                found = self.reminders.done(rid)
            elif op == "snooze":
                found = self.reminders.snooze(rid, _minutes(req.get("minutes", 60))) is not None
            else:
                raise ValueError(f"unknown reminder op {op!r}")
            if not found:
                raise ValueError(f"no such reminder {rid!r}")
            self.state.dismiss()
        elif kind == "focus":
            if not self.focus:
                raise ValueError("no focus running")
            if op == "pause":
                self.focus.pause(now)
            elif op == "resume":
                self.focus.resume(now)
            elif op == "cancel":
                self.focus = None
                self.state.pill(self._pill())
                self.state.dismiss()
            elif op == "another":
                self.focus = Focus(minutes=self.focus.minutes, started=now)
                self.state.dismiss()
            else:
                raise ValueError(f"unknown focus op {op!r}")
            if self.focus:
                self.state.pill(self.focus.pill(now))
        else:
            raise ValueError(f"unknown action kind {kind!r}")
        return {}

    def mute(self, req: dict) -> dict:
        """Switch the mic off on purpose (or back on). Unmuting never opens the mic: that is Super twice's job."""
        on = str(req.get("on", "toggle")).lower()
        muted = (not self.state.muted) if on == "toggle" else _onoff(on)
        if self._on_mute:
            self._on_mute(muted)
        self.state.set_muted(muted)
        return {"muted": muted}

    # ---------------------------------------------------------------- the clock
    def tick(self) -> None:
        """Once a second: ring due reminders, move the focus pill, end the focus, let cards linger out."""
        now = self._clock()
        if self.focus:
            if self.focus.over(now):
                minutes = int(self.focus.minutes) if self.focus.minutes == int(self.focus.minutes) else self.focus.minutes
                self.focus = None
                self.state.pill(self._pill())
                self.state.notify(f"Se acabó el tiempo de enfoque. ¿Otros {minutes}?", [
                    {"label": f"Otros {minutes}", "args": ["focus", str(minutes)], "primary": True},
                    {"label": "Terminar", "args": ["dismiss"]},
                ])
                notify_send("Enfoque", "Se acabó el tiempo", self._run)
            else:
                self.state.pill(self.focus.pill(now))
        # Focus means do not disturb: reminders wait until the timer ends (due() would mark them fired).
        for r in ([] if self.focus and not self.focus.over(now) else self.reminders.due()):
            buttons = [
                {"label": "Hecho", "args": ["action", "reminder", r.id, "done"], "primary": True},
                {"label": "Posponer 1 h", "args": ["action", "reminder", r.id, "snooze"]},
            ]
            self.state.notify(r.text, buttons, card={"reminder": {"id": r.id, "text": r.text, "at": r.at, "due": True}})
            notify_send("Recordatorio", r.text, self._run)
        linger = self.state.linger
        if linger is None:
            self._lingering = None
            return
        key = (self.state.state, self.state.card)
        if self._lingering is None or self._lingering[:2] != key:
            self._lingering = (key[0], key[1], now)
        elif now - self._lingering[2] >= linger:
            self.state.dismiss()
            self._lingering = None

    def _pill(self) -> str:
        if self.focus:
            return self.focus.pill(self._clock())
        return self.tasks.pill()

    def _spawn(self, argv: list[str]) -> None:
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise ValueError(f"cannot run {argv[0]}: {e}") from e
