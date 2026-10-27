"""User settings: ~/.config/omadot/settings.json, written by `omadot set` (the bar's panel), read by the daemon.

The daemon reloads them on SIGHUP, which `omadot set` sends after writing.
"""

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

SETTINGS_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "omadot" / "settings.json"
# The Spanish voices the local Kokoro server ships (GET :8880/v1/audio/voices).
VOICES = ("ef_dora", "em_alex", "em_santa")
SPEED_RANGE = (0.8, 1.4)
# Voice engines: Kokoro (the fast default, ~0.1 s per sentence) or OmniVoice (~0.7 s per sentence, more natural),
# each an OpenAI-style server. OmniVoice voices are attribute lists: "male, young adult, low pitch".
ENGINES = {"kokoro": "http://127.0.0.1:8880", "omni": "http://127.0.0.1:8882"}
_TRUE = {"true", "on", "1", "yes", "sí", "si"}
_FALSE = {"false", "off", "0", "no"}


@dataclass(frozen=True)
class Settings:
    voice: str = "ef_dora"
    speed: float = 1.0
    speak: bool = True  # answer out loud; off = the card shows the answer, no audio
    transcript: bool = True  # show what you said and the answer in the card
    sleep: bool = True  # close the mic after SLEEP_AFTER without anyone talking
    fast: bool = False  # opt-in: race every turn against the fast lane (Haiku through omp, your Claude account); Hermes keeps the hard ones
    tts: str = "kokoro"  # kokoro | omni (see ENGINES)
    omni_voice: str = "male, young adult, moderate pitch"  # OmniVoice attributes when tts == "omni"

    @property
    def tts_url(self) -> str:
        return ENGINES.get(self.tts, ENGINES["kokoro"])

    @property
    def tts_voice(self) -> str:
        return f"design:{self.omni_voice}" if self.tts == "omni" else self.voice

    @property
    def show_text(self) -> bool:
        return self.transcript or not self.speak


def _valid(key: str, value) -> bool:
    if key == "voice":
        return value in VOICES
    if key == "speed":
        return isinstance(value, int | float) and not isinstance(value, bool) and SPEED_RANGE[0] <= value <= SPEED_RANGE[1]
    if key == "tts":
        return value in ENGINES
    if key == "omni_voice":
        return isinstance(value, str) and 0 < len(value) <= 120
    return isinstance(value, bool)


def load(path: Path = SETTINGS_FILE) -> Settings:
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return Settings()
    if not isinstance(raw, dict):
        return Settings()
    return Settings(**{f.name: raw[f.name] for f in fields(Settings) if f.name in raw and _valid(f.name, raw[f.name])})


def _parse(key: str, text: str):
    if key not in {f.name for f in fields(Settings)}:
        raise ValueError(f"unknown setting {key!r}")
    if key in ("voice", "tts", "omni_voice"):
        value = text
    elif key == "speed":
        try:
            value = round(float(text), 2)
        except ValueError:
            raise ValueError(f"speed must be a number, got {text!r}") from None
    elif text.lower() in _TRUE:
        value = True
    elif text.lower() in _FALSE:
        value = False
    else:
        raise ValueError(f"{key} must be on or off, got {text!r}")
    if not _valid(key, value):
        allowed = ", ".join(VOICES) if key == "voice" else ", ".join(ENGINES) if key == "tts" else "up to 120 characters" if key == "omni_voice" else f"{SPEED_RANGE[0]}-{SPEED_RANGE[1]}"
        raise ValueError(f"{key} must be one of {allowed}, got {text!r}")
    return value


def set_value(path: Path, key: str, text: str) -> Settings:
    data = asdict(load(path)) | {key: _parse(key, text)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    return Settings(**data)
