"""Publishes the assistant's state to a file the Omarchy bar widget and the card watch.

States: idle (mic closed), muted (mic switched off on purpose, Super+Shift+.), sleeping (closed by itself
after a while with nobody talking), listening, thinking, working (Hermes is running a tool), speaking,
done (the reply ended and the card stays with its content), confused (nothing was understood), error
(Omi could not answer), catch (a file is being dragged over Omi), notify (a reminder, a finished task).
No file means the daemon is off.

The file is JSON: {"state", "you", "omi", "card", "pill", "buttons"}. `card` is the structured part of
the reply (see payload.py) plus "notify"/"approval" when those apply; `pill` is the short text the bar
shows next to Omi (a focus countdown, a task's progress); `buttons` are the card's actions, each with
the `omadot` arguments it runs.
"""

import json
import os
from pathlib import Path

from omadot.settings import Settings

NOT_UNDERSTOOD = "No te entendí."
SLEEP_AFTER = 600.0  # "dormir tras 10 min": seconds with the mic open and nobody talking
LINGER_PLAIN = 2.5  # a spoken-only reply closes the card after this
LINGER_ACTIONS = 15.0  # a reply with files or buttons waits for you this long
ACTIONABLE = {"files", "url", "draft", "ask", "diff", "rewrite", "plan", "device", "now_playing", "task", "mails", "clips", "actions", "approval", "reminder", "shot", "selection"}
DOUBLE_TAP = 0.4  # seconds between the two Super taps that summon Omi


def toggle_action(mic_open: bool, state: str) -> str:
    """What summoning Omi does (Super twice, a click on him): "open" the mic at rest, otherwise "close" everything (mic, voice, card)."""
    return "close" if mic_open or state not in ("idle", "sleeping", "muted") else "open"


class DoubleTap:
    """Super pressed alone twice within `window` seconds; the tap after a pair starts a new one."""

    def __init__(self, window: float = DOUBLE_TAP):
        self._window = window
        self._last: float | None = None

    def tap(self, now: float) -> bool:
        if self._last is not None and now - self._last <= self._window:
            self._last = None
            return True
        self._last = now
        return False


def should_sleep(settings: Settings, state: str, idle_for: float, after: float = SLEEP_AFTER) -> bool:
    """Close the mic when Omi has been waiting for you (not answering) for `after` seconds."""
    return settings.sleep and state in ("listening", "error", "done", "confused") and idle_for >= after


def buttons_for(card: dict) -> list[dict]:
    """The card's buttons: label + the `omadot …` arguments the click runs. Primary first."""
    out: list[dict] = []

    def add(label, *args, primary=False):
        out.append({"label": label, "args": list(args), "primary": primary})

    if a := card.get("approval"):
        add(a.get("yes", "Sí, adelante"), "approve", "once", primary=True)
        add(a.get("no", "No"), "approve", "deny")
    if n := card.get("notify"):
        for b in n.get("buttons", []):
            add(b["label"], *b["args"], primary=b.get("primary", False))
    if a := card.get("ask"):
        add(a.get("yes", "Sí"), "ask", a.get("yes", "Sí"), primary=True)
        add(a.get("no", "No"), "ask", a.get("no", "No"))
    if d := card.get("draft"):
        add("Enviar", "ask", f"Sí, envíalo a {d.get('to', '')}".strip(), primary=True)
        add("Más corto", "ask", "Hazlo más corto")
        add("Descartar", "ask", "Descarta el borrador")
    if card.get("diff"):
        add("Aplicar", "ask", "Sí, aplica el cambio", primary=True)
        add("Explícame más", "ask", "Explícame el cambio con más detalle")
    if card.get("rewrite"):
        add("Reemplazar", "replace", primary=True)
        add("Copiar", "copy")
        add("Otra versión", "ask", "Dame otra versión")
    if card.get("plan"):
        add("Agendar", "ask", "Sí, agéndalo en el calendario", primary=True)
        add("Cambiar", "ask", "Quiero cambiar algo del plan")
    if (dev := card.get("device")) and dev.get("state") == "found":
        add("Conectar", "ask", f"Conecta {dev.get('name', '')}".strip(), primary=True)
    if u := card.get("url"):
        add("Ver al lado", "open", u, "mini", primary=True)
        add("Abrir en Chromium", "open", u)
    if t := card.get("task"):
        if t.get("progress", 0) >= 100 and t.get("where"):
            add("Abrir carpeta", "open", t["where"], primary=True)
    for extra in card.get("actions", []):
        if extra.get("label") and extra.get("say"):
            add(extra["label"], "ask", extra["say"])
    return out


class StatePublisher:
    def __init__(self, path: Path, mic_open: bool, settings: Settings = Settings()):
        self._path = path
        self._mic_open = mic_open
        self._asleep = False
        self._muted = False
        self._phase: str | None = None  # "thinking" | "working" | "speaking" while a reply is in flight
        self._error = False
        self._done = False
        self._confused = False
        self._catch = False
        self._reply_open = False
        self._settings = settings
        self._you = ""
        self._reply = ""
        self._notice = ""  # errors and "no te entendí": shown even with the transcript hidden
        self._card: dict = {}
        self._notifies: list[dict] = []  # pending notifications, shown one at a time
        self._pill = ""
        self._last_reply = ""  # survives dismiss, for "repite"
        self._written: dict | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        self._publish()

    @property
    def state(self) -> str:
        if self._error:
            return "error"
        if self._notifies:
            return "notify"
        if self._catch:
            return "catch"
        if self._phase:
            return self._phase
        if self._confused:
            return "confused"
        if self._done:
            return "done"
        if self._mic_open:
            return "listening"
        if self._muted:
            return "muted"
        return "sleeping" if self._asleep else "idle"

    @property
    def card(self) -> dict:
        return dict(self._card)

    @property
    def last_reply(self) -> str:
        return self._last_reply

    @property
    def muted(self) -> bool:
        """The real flag, whatever the card shows on top of it."""
        return self._muted

    @property
    def linger(self) -> float | None:
        """Seconds the card should stay after this state, or None if it stays until something happens."""
        if self.state == "confused":
            return LINGER_PLAIN
        if self.state != "done":
            return None
        return LINGER_ACTIONS if ACTIONABLE & set(self._card) else LINGER_PLAIN

    def configure(self, settings: Settings) -> None:
        self._settings = settings
        self._publish()

    def set_mic(self, open_: bool) -> None:
        self._mic_open = open_
        self._asleep = False
        if open_:
            self._muted = False
        if self._error:
            self._error = False
            self._notice = ""
        self._done = self._confused = False
        self._publish()

    def set_muted(self, muted: bool) -> None:
        self._muted = muted
        if muted:
            self._mic_open = False
        self._publish()

    def sleep(self) -> None:
        self._mic_open = False
        self._asleep = True
        self._done = self._confused = False
        self._publish()

    def turn_ended(self, text: str) -> None:
        self._done = self._confused = False
        if text.strip():
            self._phase = "thinking"
        else:  # nothing transcribed (a cough, or speech Whisper could not make out): Hermes never runs
            self._phase = None
            self._notice = NOT_UNDERSTOOD
            self._confused = True
        self._publish()

    def error(self, text: str) -> None:
        self._error = True
        self._notice = text
        self._publish()

    def working(self, tool: str = "") -> None:
        """Hermes started a tool: Omi rolls up his sleeves until the next token."""
        if self._phase == "thinking":
            self._phase = "working"
            self._publish()

    def bot_started(self) -> None:
        self._phase = "speaking"
        self._publish()

    def reply_started(self) -> None:
        self._reply_open = True

    def reply_ended(self) -> None:
        self._reply_open = False
        if not self._settings.speak:  # no audio will follow: the text was the answer
            self._phase = None
            self._done = bool(self._reply or self._card)
            self._publish()

    def bot_stopped(self) -> None:
        # A filler can finish before the agent's reply has even started streaming.
        if self._reply_open:
            self._phase = "thinking"
        else:
            self._phase = None
            self._done = bool(self._reply or self._card)
        self._publish()

    def user_started(self) -> None:
        self._phase = None
        self._error = self._done = self._confused = False
        self._reply_open = False
        self._you = self._reply = self._notice = ""
        self._card = {}
        self._publish()

    def user_said(self, text: str) -> None:
        self._you = " ".join(filter(None, [self._you, text.strip()]))
        self._publish()

    def omi_said(self, delta: str) -> None:
        # Hermes is answering: whatever went wrong before (a retry) is over.
        self._error = False
        self._notice = ""
        self._reply += delta
        self._last_reply = self._reply
        if not self._settings.speak:
            self._phase = "speaking"
        self._publish()

    def show(self, card: dict) -> None:
        """The structured part of the reply (files, a draft, a diff…) arrived: merge it into the card."""
        self._card = {**self._card, **card}
        self._publish()

    def ambient(self, card: dict) -> None:
        """Something that is not part of the conversation (a task's progress, a reminder set from the terminal):
        it gets the card to itself when nothing else is showing; while a reply is in flight or still on
        screen it only joins the card, so a progress tick never wipes an answer you are reading."""
        if self._phase is None and not self._done and not self._reply:
            self._you = self._reply = self._notice = ""
            self._card = {}
            self._done = True
        self._card = {**self._card, **card}
        self._publish()

    def approval(self, question: str, request_id: str) -> None:
        """Hermes wants permission for a tool: the card asks, the buttons answer through `omadot approve`."""
        self._card["approval"] = {"question": question, "request_id": request_id}
        self._publish()

    def approved(self) -> None:
        self._card.pop("approval", None)
        self._publish()

    def catch(self, on: bool) -> None:
        self._catch = on
        self._publish()

    def pill(self, text: str) -> None:
        self._pill = text
        self._publish()

    def notify(self, text: str, buttons: list[dict] | None = None, card: dict | None = None) -> None:
        """Something happened on its own (a reminder rang, a task finished): the card drops with buttons.

        Several at once (after a suspend, say) queue up: each one waits for the previous to be dismissed.
        """
        self._notifies.append({"text": text, "buttons": buttons or [], "card": card or {}})
        if self._phase is None:  # the conversation is over: the notification is the whole card
            self._you = self._reply = self._notice = ""
            self._card = {}
        self._publish()

    def dismiss(self) -> None:
        """Close the card: the reply, its content and the current notification are gone; the mic state stays."""
        self._done = self._confused = False
        if self._notifies:
            self._notifies.pop(0)
        self._you = self._reply = self._notice = ""
        self._card = {}
        self._publish()

    def close(self) -> None:
        self._path.unlink(missing_ok=True)

    def _publish(self) -> None:
        show = self._settings.show_text
        card = dict(self._card)
        if self._notifies:
            current = self._notifies[0]
            card.update(current["card"])
            card["notify"] = {"text": current["text"], "buttons": current["buttons"]}
        data = {
            "state": self.state,
            "you": self._you if show else "",
            "omi": self._notice or (self._reply.strip() if show else ""),
            "card": card,
            "pill": self._pill,
            "buttons": buttons_for(card),
        }
        if data == self._written:
            return
        # Atomic replace so the watcher never reads a half-written file.
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        os.replace(tmp, self._path)
        self._written = data
