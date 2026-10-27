"""The structured part of Omi's reply: what the card draws besides the spoken sentences.

Hermes is asked to end a reply with one line `@@omi {json}` when there is something to show: files to
drag, a page to open, a draft to confirm, a reminder it set... The line is never spoken. `MEDIA:/path`
tags (Hermes' own way of sending files) and bare absolute paths of existing files become file tiles too.
"""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

MARK = "@@omi"
_MEDIA = re.compile(r"MEDIA:\s*(\S+)")
_PATH = re.compile(r"(?<![\w/])(~?/[\w.\-/ ]+?\.[A-Za-z0-9]{1,5})(?=[\s,;:)\]\"']|$)")

# Keys the card knows how to draw; anything else is dropped so a creative model cannot break the card.
KINDS = {
    "files",       # [{"path", "label"?}] → draggable tiles
    "url",         # "https://…" → the small browser
    "app",         # {"name", "where"?} → "Opening X"
    "mails",       # [{"from", "subject", "tag": urgent|today|later}]
    "draft",       # {"to", "text"} → editable draft with Send / Discard (asks before it acts)
    "reminder",    # {"text", "at": "2026-11-04T09:00"} → the daemon schedules it
    "focus",       # {"minutes": 25} → the daemon runs the timer
    "now_playing", # {"title", "artist"}
    "rewrite",     # {"before", "after"} → Replace / Copy
    "diff",        # [{"sign": "-|+| ", "text"}] → Apply
    "device",      # {"name", "detail", "state": found|connecting|on|off|not_found}
    "plan",        # [{"time", "title"}] → Add to calendar
    "task",        # {"id", "label", "progress": 0-100} → progress, lives in the bar
    "ask",         # {"question", "yes": "…", "no": "…"} → the two buttons send those answers back
    "clips",       # [{"kind": text|link|image, "label", "when"}]
    "actions",     # [{"label", "say": "…"}] → extra buttons that answer by voice
}


@dataclass
class Reply:
    spoken: str  # everything meant for the voice: the text before the marker and any text after the JSON
    card: dict = field(default_factory=dict)
    tail: str = ""  # the part of `spoken` that came after the marker (still unspoken when streaming)


def _expand(p: str) -> str:
    return os.path.expanduser(p.strip())


def _file_entries(paths: list[str], exists=os.path.exists) -> list[dict]:
    out, seen = [], set()
    for p in paths:
        full = _expand(p)
        if full in seen or not exists(full):
            continue
        seen.add(full)
        out.append({"path": full, "label": Path(full).name})
    return out


def _blocks(text: str) -> tuple[list[str], list[dict]]:
    """Split the marker blocks out: (the speech segments between/around the `@@omi {json}` lines, the JSON objects in order)."""
    out, objs, pos = [], [], 0
    decoder = json.JSONDecoder()
    while True:
        i = text.find(MARK, pos)
        if i < 0:
            out.append(text[pos:])
            break
        out.append(text[pos:i].rstrip("*`"))  # the emphasis or fence that wrapped the marker is not speech
        j = i + len(MARK)
        while j < len(text) and text[j] in " \t\r\n`*":  # tolerate a fenced ```json block or **@@omi**
            j += 1
        if text.startswith("json", j):
            j += 4
            while j < len(text) and text[j] in " \t\r\n":
                j += 1
        try:
            obj, end = decoder.raw_decode(text, j)
        except ValueError:  # not JSON: drop the rest of that line, it was meant for the card, not the voice
            logger.warning(f"card line is not JSON, dropped: {text[i:i + 120]!r}")
            nl = text.find("\n", j)
            pos = len(text) if nl < 0 else nl
            continue
        if isinstance(obj, dict):
            objs.append(obj)
        else:
            logger.warning(f"card line is not an object, dropped: {type(obj).__name__}")
        pos = end
        while pos < len(text) and text[pos] in " \t\r\n`":  # the closing fence, if any
            pos += 1
    return out, objs


def parse(text: str, exists=os.path.exists) -> Reply:
    """Split a full reply into what is spoken and what the card shows."""
    segments, objs = _blocks(text)
    spoken_all = "".join(segments)
    data: dict = {}
    for obj in objs:  # a second marker line adds to (or overrides) the first
        data.update(obj)
    card = {k: v for k, v in data.items() if k in KINDS and v not in (None, "", [], {})}
    media = _MEDIA.findall(spoken_all)
    spoken_all = _MEDIA.sub("", spoken_all)
    raw_files = card.get("files", [])
    if isinstance(raw_files, (str, dict)):  # a model that meant one file
        raw_files = [raw_files]
    files = [f.get("path", "") if isinstance(f, dict) else str(f) for f in raw_files if f]
    entries = _file_entries(files + media + _PATH.findall(spoken_all), exists)
    if entries:
        card["files"] = entries
    elif "files" in card:
        del card["files"]
    spoken = " ".join(spoken_all.split())
    tail = " ".join(_MEDIA.sub("", "".join(segments[1:])).split())
    return Reply(spoken=spoken, card=card, tail=tail)


def holdback(buffered: str) -> tuple[str, str]:
    """For streaming: (text safe to speak now, text to keep) so no part of `@@omi …` reaches the TTS.

    Keeps the tail that could be the start of the marker (e.g. a lone "@" or "@@om").
    """
    i = buffered.find(MARK)
    if i >= 0:
        return buffered[:i], buffered[i:]
    for n in range(min(len(MARK) - 1, len(buffered)), 0, -1):
        if buffered.endswith(MARK[:n]):
            return buffered[:-n], buffered[-n:]
    return buffered, ""


PROTOCOL = (
    "When your answer has something to show, end it with exactly one line that starts with "
    + MARK
    + " followed by compact JSON (no code fence) with any of these keys: "
    '"files" (absolute paths of files you mention, so they can be dragged), "url" (a page to show), '
    '"app" ({"name","where"}) when you open an application, "mails" ([{"from","subject","tag":"urgent|today|later"}]), '
    '"draft" ({"to","text"}) for a message you prepared but did not send, "reminder" ({"text","at":"ISO 8601"}) for a reminder '
    "the desktop will ring (the daemon schedules it from this line: without the line nothing is scheduled), "
    '"focus" ({"minutes"}), "now_playing" ({"title","artist"}), "rewrite" ({"before","after"}), '
    '"diff" ([{"sign":"-|+","text"}]) for a code change you propose, "device" ({"name","detail","state":"found|connecting|on|off|not_found"}), '
    '"plan" ([{"time","title"}]) for any schedule or itinerary with times, "task" ({"id","label","progress"}) for long work, '
    '"ask" ({"question","yes","no"}) and "actions" ([{"label","say"}]) for follow-ups. '
    "Rules: if you say you set a reminder, drafted a message, made a plan or propose a change, that same reply must carry the matching "
    "card, never report it without the data. Before any tool that sends, deletes, moves, overwrites, pays or changes settings, end your "
    "reply with an ask card and stop; call that tool only after the user says yes (looking something up first is fine, acting is not). "
    "A plain spoken answer with no card is right when nothing fits or a tool failed. "
    "When you reword or translate any text (selected, pasted, or your own last answer), put it in a rewrite card so it can be copied. "
    "Never enumerate (1), 2), bullets) or write paragraphs: if an answer needs more than three sentences, say the gist in one or two "
    "and offer to go on. "
    "The spoken part never contains backticks, code, file paths as code, URLs or markdown: a code change goes in the diff card, a page in url. "
    "The card line is not spoken; everything before it is."
)
