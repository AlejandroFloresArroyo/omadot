"""Local OpenAI-style speech server (/v1/audio/speech) on Kokoro-82M, on the GPU.

OmaDot's default voice (`omadot setup kokoro` installs it on 127.0.0.1:8880). Hermes can use it too, as
`tts.provider: openai` with `tts.openai.base_url: http://127.0.0.1:8880/v1`.
- response_format=pcm  -> real streaming: int16 mono 24 kHz, one chunk per sentence.
- mp3 / opus / wav / flac / aac -> the whole file (encoded with ffmpeg).
- Spanish with Latin American phonemes (espeak-ng `es-419`: seseo, no "θ") instead of Spain's `es`.
- `lexicon.json` (next to this file) fixes pronunciations: technical anglicisms and the English expansions
  Hermes' normaliser inserts ("percent", "degrees Celsius", ...). Re-read on every request: edit it and the
  next sentence already uses it.

Variables: LOCAL_TTS_HOST (127.0.0.1), LOCAL_TTS_PORT (8880), LOCAL_TTS_VOICE (em_alex),
           LOCAL_TTS_LANG (e), LOCAL_TTS_ES_DIALECT (es-419), LOCAL_TTS_DEVICE (cuda).
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
LEXICON = HERE / "lexicon.json"
SAMPLE_RATE = 24000
DEFAULT_VOICE = os.environ.get("LOCAL_TTS_VOICE", "em_alex")
DEFAULT_LANG = os.environ.get("LOCAL_TTS_LANG", "e")
ES_DIALECT = os.environ.get("LOCAL_TTS_ES_DIALECT", "es-419")
DEVICE = os.environ.get("LOCAL_TTS_DEVICE", "cuda")

log = logging.getLogger("local-tts")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Kokoro voices and their language (the id's first letter). Spanish: ef_dora, em_alex, em_santa.
LANG_BY_PREFIX = {"a": "a", "b": "b", "e": "e", "f": "f", "h": "h", "i": "i", "j": "j", "p": "p", "z": "z"}
CONTENT_TYPES = {"mp3": "audio/mpeg", "opus": "audio/ogg", "aac": "audio/aac", "flac": "audio/flac",
                 "wav": "audio/wav", "pcm": f"audio/pcm;rate={SAMPLE_RATE}"}

_model = None
_pipelines: dict[str, object] = {}
_lock = threading.Lock()  # one GPU, one synthesis at a time


def _pipeline(lang: str):
    global _model
    from kokoro import KModel, KPipeline
    with _lock:
        if _model is None:
            t = time.time()
            _model = KModel().to(DEVICE).eval()
            log.info("Kokoro loaded on %s in %.1fs", DEVICE, time.time() - t)
        if lang not in _pipelines:
            pipe = KPipeline(lang_code=lang, model=_model)
            if lang == "e" and ES_DIALECT != "es":
                from misaki import espeak
                pipe.g2p = espeak.EspeakG2P(language=ES_DIALECT)
            _pipelines[lang] = pipe
        return _pipelines[lang]


def _lexicon() -> list[tuple[re.Pattern, str]]:
    try:
        data = json.loads(LEXICON.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if LEXICON.exists():
            log.warning("unreadable lexicon.json (%s); ignored", exc)
        return []
    rules = []
    # Longest phrases first, so "pull request" wins over "pull".
    for src, dst in sorted(data.items(), key=lambda kv: -len(kv[0])):
        if src.startswith("_"):
            continue
        rules.append((re.compile(r"(?<![\w])" + re.escape(src) + r"(?![\w])", re.IGNORECASE), dst))
    return rules


def _respell(text: str, lang: str) -> str:
    if lang != "e":
        return text
    # "1,200" would read "uno coma doscientos" in Spanish: drop the thousands separator.
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)
    for pattern, dst in _lexicon():
        text = pattern.sub(dst, text)
    return text


def _chunks(text: str, voice: str, speed: float, lang: str):
    pipe = _pipeline(lang)
    spoken = _respell(text, lang)
    with _lock:
        for result in pipe(spoken, voice=voice, speed=speed, split_pattern=r"\n+|(?<=[.!?…])\s+"):
            audio = result.audio
            if audio is None:
                continue
            pcm = (np.clip(audio.cpu().numpy(), -1.0, 1.0) * 32767).astype("<i2")
            yield pcm.tobytes()


def _encode(pcm: bytes, fmt: str) -> bytes:
    if fmt == "pcm":
        return pcm
    if fmt == "wav":
        import soundfile as sf
        buf = io.BytesIO()
        sf.write(buf, np.frombuffer(pcm, dtype="<i2"), SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return buf.getvalue()
    codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "96k", "-f", "mp3"],
             "opus": ["-c:a", "libopus", "-b:a", "48k", "-f", "ogg"],
             "aac": ["-c:a", "aac", "-b:a", "96k", "-f", "adts"],
             "flac": ["-c:a", "flac", "-f", "flac"]}[fmt]
    proc = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
                           "-i", "pipe:0", *codec, "pipe:1"], input=pcm, capture_output=True, check=False)
    if proc.returncode != 0:
        raise HTTPException(500, f"ffmpeg: {proc.stderr.decode(errors='replace')[:300]}")
    return proc.stdout


class SpeechRequest(BaseModel):
    model: str | None = "kokoro"
    input: str
    voice: str | None = None
    response_format: str | None = "mp3"
    speed: float | None = 1.0
    lang_code: str | None = None
    instructions: str | None = None  # accepted and dropped (Kokoro does not use it)

    model_config = {"extra": "ignore"}


app = FastAPI(title="local-tts (Kokoro)")
# Hermes' desktop app calls this server DIRECTLY from its renderer (voice.client_direct) with
# Authorization + JSON => a CORS preflight. It only listens on 127.0.0.1; no cookies or credentials.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["*"], expose_headers=["X-Audio-Sample-Rate"])


@app.get("/health")
def health():
    return {"ok": True, "loaded": _model is not None, "device": DEVICE, "voice": DEFAULT_VOICE, "es": ES_DIALECT}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "kokoro", "object": "model", "owned_by": "local"}]}


@app.get("/v1/audio/voices")
def voices():
    return {"voices": ["em_alex", "em_santa", "ef_dora"], "default": DEFAULT_VOICE}


@app.post("/v1/audio/speech")
def speech(req: SpeechRequest):
    text = (req.input or "").strip()
    if not text:
        raise HTTPException(400, "empty input")
    voice = req.voice if req.voice and req.voice not in {"alloy", "default"} else DEFAULT_VOICE
    lang = (req.lang_code or LANG_BY_PREFIX.get(voice[:1], DEFAULT_LANG))[:1]
    if lang == "s":  # "es" sent as lang_code
        lang = "e"
    speed = max(0.5, min(2.0, float(req.speed or 1.0)))
    fmt = (req.response_format or "mp3").lower()
    if fmt not in CONTENT_TYPES:
        raise HTTPException(400, f"unsupported response_format: {fmt}")
    t0 = time.time()
    if fmt == "pcm":
        def gen():
            first = True
            for chunk in _chunks(text, voice, speed, lang):
                if first:
                    log.info("pcm first chunk in %.0f ms (%d chars, voice %s)", (time.time() - t0) * 1000, len(text), voice)
                    first = False
                yield chunk
        return StreamingResponse(gen(), media_type=CONTENT_TYPES["pcm"],
                                 headers={"X-Audio-Sample-Rate": str(SAMPLE_RATE)})
    pcm = b"".join(_chunks(text, voice, speed, lang))
    body = _encode(pcm, fmt)
    log.info("%s %.0f ms, %.1fs of audio (%d chars, voice %s)", fmt, (time.time() - t0) * 1000,
             len(pcm) / 2 / SAMPLE_RATE, len(text), voice)
    return Response(body, media_type=CONTENT_TYPES[fmt])


@app.on_event("startup")
def _warm():
    # Preload in the background: the first real reply does not pay for loading the model.
    def run():
        try:
            for _ in _chunks("Listo.", DEFAULT_VOICE, 1.0, DEFAULT_LANG):
                pass
        except Exception as exc:  # noqa: BLE001
            log.warning("preload failed: %s", exc)
    threading.Thread(target=run, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("LOCAL_TTS_HOST", "127.0.0.1"),
                port=int(os.environ.get("LOCAL_TTS_PORT", "8880")), log_level="warning")
