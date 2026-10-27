"""What Omi can see besides your words: the clipboard, the selected text, the active window, a screenshot.

`enrich()` decides, from what you said, which of these to attach to the turn. Everything that touches the
system goes through `run`, so tests inject fake commands.
"""

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

CLIPBOARD = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state") / "omarchy" / "clipboard-history.json"
SHOTS = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "omadot" / "shots"

# "esto" alone is everyday speech; a screenshot needs a screen-referring phrase (decision: no capture on "no quiero esto").
_SCREEN = re.compile(r"\b(qué es esto|que es esto|qué es eso|mira esto|ves esto|esta ventana|la pantalla|en pantalla|captura|screenshot|what is this|what'?s this|look at this|this window|the screen|on screen)\b", re.I)
_CLIP = re.compile(r"\b(copi[eé]|copiad[oa]|portapapeles|clipboard|copied|pegar|paste)\b", re.I)
_SELECT = re.compile(r"\b(seleccion|lo que marqu|este texto|selected|selection|highlighted|reescr|rewrite|más formal|more formal|resume esto|hazlo más)\b", re.I)
_TERMINAL = re.compile(r"\b(fall[oó]|error|terminal|por qué no|why did it|failed|stack ?trace)\b", re.I)


def run(argv: list[str], timeout: float = 5.0, capture: bool = True) -> str:
    """Run a desktop tool. `capture=False` for tools that fork and keep running (wl-copy, hyprctl exec):
    with a captured pipe their child would hold it open and the call would wait for the whole timeout."""
    try:
        if not capture:
            subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
            return ""
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def clipboard(n: int = 5, path: Path = CLIPBOARD) -> list[dict]:
    """The latest clipboard entries Omarchy keeps: [{"kind": text|link|image, "label", "when", "path"?}]."""
    try:
        entries = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    out = []
    for e in entries[:n]:
        if not isinstance(e, dict):
            continue
        when = e.get("capturedAt", "")
        if e.get("type") == "image" and e.get("path"):
            out.append({"kind": "image", "label": Path(e["path"]).name, "when": when, "path": e["path"]})
        elif e.get("type") == "text" and e.get("text"):
            text = " ".join(str(e["text"]).split())
            kind = "link" if re.match(r"https?://\S+$", text) else "text"
            out.append({"kind": kind, "label": text[:90], "when": when, "text": text})
    return out


def selection(run=run) -> str:
    """The primary selection (what is highlighted in the active window)."""
    return run(["wl-paste", "-p", "-n"]).strip()


def active_window(run=run) -> dict:
    try:
        w = json.loads(run(["hyprctl", "activewindow", "-j"]) or "{}")
    except ValueError:
        return {}
    if not w:
        return {}
    return {"title": w.get("title", ""), "app": w.get("class", ""), "at": w.get("at", [0, 0]), "size": w.get("size", [0, 0])}


def screenshot(window: dict | None, run=run, out_dir: Path = SHOTS, now=time.time) -> str:
    """Capture the active window (or the whole screen) with grim; returns the PNG path or ""."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"shot-{int(now())}.png"
    argv = ["grim"]
    if window and window.get("size", [0, 0])[0]:
        x, y = window["at"]
        w, h = window["size"]
        argv += ["-g", f"{x},{y} {w}x{h}"]
    run(argv + [str(path)], timeout=10)
    return str(path) if path.exists() else ""


def now_playing(run=run) -> dict | None:
    out = run(["playerctl", "metadata", "--format", "{{status}}\t{{title}}\t{{artist}}"]).strip()
    if not out or out.startswith("No players"):
        return None
    status, title, artist = (out.split("\t") + ["", "", ""])[:3]
    return {"title": title, "artist": artist, "playing": status == "Playing"}


@dataclass
class Turn:
    text: str  # what Hermes receives
    card: dict = field(default_factory=dict)  # what the card shows right away (clips, the screenshot)


def enrich(said: str, *, run=run, clipboard_path: Path = CLIPBOARD, shots: Path = SHOTS, now=time.time) -> Turn:
    """Attach the context the words call for. The active window's title always rides along: it is cheap."""
    lines, card = [], {}
    # The date and time ride along: "¿qué hora es?" and "¿cuántos días faltan?" then need no tool (a tool costs ~1.5 s).
    lines.append(f"[Now: {datetime.fromtimestamp(now()).strftime('%A %Y-%m-%d %H:%M')} local time]")
    window = active_window(run)
    if window.get("title"):
        lines.append(f"[Active window: {window['title']} ({window['app']})]")
    if _SCREEN.search(said):
        shot = screenshot(window, run, shots, now)
        if shot:
            lines.append(f"[Screenshot of the active window: {shot} — look at it with your image tools]")
            card["shot"] = shot
    if _CLIP.search(said):
        clips = clipboard(5, clipboard_path)
        if clips:
            lines.append("[Clipboard, newest first: " + " | ".join(f"{c['kind']}: {c.get('text') or c.get('path')}" for c in clips) + "]")
            card["clips"] = [{k: c[k] for k in ("kind", "label", "when") if k in c} for c in clips]
    if _SELECT.search(said):
        sel = selection(run)
        if sel:
            lines.append(f"[Selected text: {sel[:2000]}]")
            card["selection"] = sel[:300]
    if _TERMINAL.search(said) and window.get("app", "").lower() in ("alacritty", "ghostty", "foot", "kitty", "com.mitchellh.ghostty"):
        lines.append("[The active window is a terminal: read its output with your tools before answering]")
    text = said + "\n\n" + "\n".join(lines)
    return Turn(text=text, card=card)
