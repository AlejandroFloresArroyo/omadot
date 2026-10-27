"""OpenAI-style speech server for Qwen3-TTS or OmniVoice, so OmaDot can switch voices with `--kokoro URL`.

    python tts_server.py --engine qwen --port 8881            # Qwen3-TTS 1.7B CustomVoice (speakers: Ryan, Serena…)
    python tts_server.py --engine qwen-design --port 8881     # Qwen3-TTS 1.7B VoiceDesign (voice = "design:<description>")
    python tts_server.py --engine omni --port 8882            # OmniVoice (voice = "design:<description>" | "clone:<wav>|<transcript>")

POST /v1/audio/speech {"input", "voice", "lang_code", "speed", "response_format": "pcm"|"wav"} -> 24 kHz mono.
Runs in its own venv (`omadot setup omni` builds ~/.local/share/omadot/tts-omni): torch and the models never
enter OmaDot's venv.
"""

import argparse
import io
import time

import numpy as np
import soundfile as sf
import uvicorn
from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel

RATE = 24000
LANG = {"e": "Spanish", "a": "English", "b": "English", "p": "Portuguese", "f": "French", "i": "Italian", "j": "Japanese", "z": "Chinese"}
DEFAULT_DESIGN = "Hombre joven, voz cálida y cercana, acento mexicano neutro, habla con calma y claridad"


class Speech(BaseModel):
    input: str
    voice: str = ""
    lang_code: str = "e"
    speed: float = 1.0
    response_format: str = "pcm"


class Engine:
    name = ""

    def say(self, text: str, voice: str, lang: str, speed: float) -> np.ndarray:  # float32 mono at RATE
        raise NotImplementedError


class Qwen(Engine):
    def __init__(self, design: bool, size: str = "1.7B"):
        import torch
        from qwen_tts import Qwen3TTSModel

        self.design = design
        self.name = "qwen-design" if design else "qwen"
        repo = f"Qwen/Qwen3-TTS-12Hz-{size}-VoiceDesign" if design else f"Qwen/Qwen3-TTS-12Hz-{size}-CustomVoice"
        self.model = Qwen3TTSModel.from_pretrained(repo, device_map="cuda:0", dtype=torch.bfloat16)

    def say(self, text, voice, lang, speed):
        if self.design:
            instruct = voice.split(":", 1)[1] if voice.startswith("design:") else DEFAULT_DESIGN
            wavs, sr = self.model.generate_voice_design(text=text, language=lang, instruct=instruct)
        else:
            wavs, sr = self.model.generate_custom_voice(text=text, language=lang, speaker=voice or "Ryan")
        return _mono(wavs, sr)


class Omni(Engine):
    name = "omni"

    def __init__(self):
        import torch
        from omnivoice import OmniVoice

        self.model = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cuda:0", dtype=torch.float16)

    def say(self, text, voice, lang, speed):
        kw = {"speed": speed} if speed and speed != 1.0 else {}
        if voice.startswith("clone:"):
            ref, _, transcript = voice[6:].partition("|")
            audio = self.model.generate(text=text, ref_audio=ref, ref_text=transcript or None, **kw)
        else:
            instruct = voice.split(":", 1)[1] if voice.startswith("design:") else DEFAULT_DESIGN
            audio = self.model.generate(text=text, instruct=instruct, **kw)
        return _mono(audio, RATE)


def _mono(wavs, sr) -> np.ndarray:
    w = wavs[0] if isinstance(wavs, (list, tuple)) else wavs
    if hasattr(w, "detach"):
        w = w.detach().float().cpu().numpy()
    w = np.asarray(w, dtype=np.float32).squeeze()
    if w.ndim > 1:
        w = w.mean(axis=0 if w.shape[0] < w.shape[1] else 1)
    if sr != RATE:
        n = int(len(w) * RATE / sr)
        w = np.interp(np.linspace(0, len(w) - 1, n), np.arange(len(w)), w).astype(np.float32)
    return np.clip(w, -1, 1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--engine", choices=["qwen", "qwen-design", "omni"], required=True)
    p.add_argument("--port", type=int, default=8881)
    p.add_argument("--size", default="1.7B", help="Qwen3-TTS size: 1.7B or 0.6B")
    args = p.parse_args()
    t0 = time.perf_counter()
    engine = Qwen(design=args.engine == "qwen-design", size=args.size) if args.engine.startswith("qwen") else Omni()
    print(f"{engine.name} loaded in {time.perf_counter() - t0:.1f}s", flush=True)

    app = FastAPI()

    @app.get("/v1/audio/voices")
    def voices():
        return {"engine": engine.name, "voices": ["Ryan", "Aiden", "Eric", "Dylan", "Serena", "Vivian"] if engine.name == "qwen" else ["design:<descripción>"]}

    @app.post("/v1/audio/speech")
    def speech(req: Speech):
        t = time.perf_counter()
        audio = engine.say(req.input, req.voice, LANG.get(req.lang_code, "Spanish"), req.speed)
        ms = round((time.perf_counter() - t) * 1000)
        print(f"{engine.name}: {len(audio) / RATE:.1f}s of audio in {ms} ms  «{req.input[:60]}»", flush=True)
        if req.response_format == "wav":
            buf = io.BytesIO()
            sf.write(buf, audio, RATE, format="WAV", subtype="PCM_16")
            return Response(buf.getvalue(), media_type="audio/wav", headers={"X-Generation-Ms": str(ms)})
        pcm = (audio * 32767).astype("<i2").tobytes()
        return Response(pcm, media_type="audio/pcm", headers={"X-Generation-Ms": str(ms)})

    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
