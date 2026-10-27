"""Things Omi does on his own clock: reminders, a focus timer, long tasks reporting progress.

All of them are plain state machines over an injected clock, so the daemon loop only has to call
`tick()` and act on what comes back. Reminders persist in ~/.local/state/omadot/reminders.json.
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REMINDERS = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "omadot" / "reminders.json"


def parse_when(text: str, now: datetime | None = None) -> float | None:
    """An ISO 8601 moment (what the reply protocol asks Hermes for) as a Unix timestamp, in local time."""
    try:
        dt = datetime.fromisoformat(str(text).strip())
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.timestamp()


@dataclass
class Reminder:
    id: str
    text: str
    at: float
    fired: bool = False


class Reminders:
    def __init__(self, path: Path = REMINDERS, clock=time.time):
        self._path = path
        self._clock = clock
        self._items: list[Reminder] = []
        self._load()

    def add(self, text: str, at: float) -> Reminder:
        r = Reminder(id=uuid.uuid4().hex[:8], text=text, at=at)
        self._items.append(r)
        self._save()
        return r

    def due(self) -> list[Reminder]:
        """Reminders whose time came and that nobody has seen yet; each is returned once."""
        now = self._clock()
        out = [r for r in self._items if not r.fired and r.at <= now]
        for r in out:
            r.fired = True
        if out:
            self._save()
        return out

    def done(self, rid: str) -> bool:
        before = len(self._items)
        self._items = [r for r in self._items if r.id != rid]
        self._save()
        return len(self._items) < before

    def snooze(self, rid: str, minutes: float = 60) -> Reminder | None:
        for r in self._items:
            if r.id == rid:
                r.at = self._clock() + minutes * 60
                r.fired = False
                self._save()
                return r
        return None

    def pending(self) -> list[Reminder]:
        return sorted((r for r in self._items if not r.fired), key=lambda r: r.at)

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text())
            self._items = [Reminder(**r) for r in raw]
        except (OSError, ValueError, TypeError):
            self._items = []

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps([r.__dict__ for r in self._items], ensure_ascii=False, indent=1))
        os.replace(tmp, self._path)


@dataclass
class Focus:
    """A countdown next to Omi; notifications are paused for its duration."""

    minutes: float
    started: float
    paused_at: float | None = None

    def remaining(self, now: float) -> float:
        end = self.started + self.minutes * 60
        return max(0.0, end - (self.paused_at if self.paused_at is not None else now))

    def pill(self, now: float) -> str:
        left = round(self.remaining(now))
        return f"{left // 60:02d}:{left % 60:02d}"

    def over(self, now: float) -> bool:
        return self.paused_at is None and self.remaining(now) <= 0

    def pause(self, now: float) -> None:
        if self.paused_at is None:
            self.paused_at = now

    def resume(self, now: float) -> None:
        if self.paused_at is not None:
            self.started += now - self.paused_at
            self.paused_at = None


@dataclass
class Task:
    id: str
    label: str
    progress: float = 0  # 0-100
    detail: str = ""  # "2/6"
    where: str = ""  # a folder to open when it is done


@dataclass
class Tasks:
    """Long work Hermes (or a script it ran) reports with `omadot progress ID PCT [LABEL] [DETAIL]`."""

    items: dict[str, Task] = field(default_factory=dict)

    def update(self, tid: str, progress: float, label: str = "", detail: str = "", where: str = "") -> Task:
        t = self.items.get(tid) or Task(id=tid, label=label or tid)
        t.progress = max(0.0, min(100.0, float(progress)))
        if label:
            t.label = label
        if detail:
            t.detail = detail
        if where:
            t.where = where
        self.items[tid] = t
        return t

    def running(self) -> list[Task]:
        return [t for t in self.items.values() if t.progress < 100]

    def pill(self) -> str:
        """What the bar shows: the detail or percentage of the task furthest from done."""
        running = self.running()
        if not running:
            return ""
        t = min(running, key=lambda t: t.progress)
        return t.detail or f"{t.progress:.0f}%"

    def finish(self, tid: str) -> Task | None:
        return self.items.pop(tid, None)
