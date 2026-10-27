"""TTS service for the local Kokoro server (OpenAI-style /v1/audio/speech, pcm 24 kHz)."""

import re
from collections.abc import AsyncGenerator

import httpx
from pipecat.frames.frames import ErrorFrame, Frame, TTSAudioRawFrame
from pipecat.services.settings import TTSSettings
from pipecat.services.tts_service import TTSService

KOKORO_SAMPLE_RATE = 24000

_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL = re.compile(r"https?://\S+")
_MEDIA = re.compile(r"MEDIA:\S+")
_MARKS = re.compile(r"[*_`#>]+")
_BULLET = re.compile(r"^\s*(?:[-+]|\d+\.)\s+", re.MULTILINE)
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿️]")


def speakable(text: str) -> str:
    """Drop markdown, links and emoji the TTS would otherwise read out loud."""
    text = _LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    text = _MEDIA.sub("", text)
    text = _BULLET.sub("", text)
    text = _MARKS.sub("", text)
    text = _EMOJI.sub("", text)
    return "\n".join(" ".join(line.split()) for line in text.splitlines()).strip()


class KokoroTTSService(TTSService):
    def __init__(self, *, base_url: str, voice: str, lang_code: str, speed: float = 1.0, **kwargs):
        super().__init__(
            sample_rate=KOKORO_SAMPLE_RATE,
            settings=TTSSettings(model=None, voice=voice, language=None),
            **kwargs,
        )
        self._voice = voice
        self._lang_code = lang_code
        self._speed = speed
        self._http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10.0, read=60.0))

    def can_generate_metrics(self) -> bool:
        return True

    def configure(self, voice: str, speed: float, base_url: str | None = None) -> None:
        """New voice/speed (and server) from the settings; applies from the next sentence."""
        self._voice = voice
        self._speed = speed
        if base_url and str(self._http.base_url).rstrip("/") != base_url.rstrip("/"):
            self._http = httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(10.0, read=60.0))

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame, None]:
        text = speakable(text)
        if not text:
            return
        body = {
            "input": text,
            "voice": self._voice,
            "lang_code": self._lang_code,
            "speed": self._speed,
            "response_format": "pcm",
        }
        async with self._http.stream("POST", "/v1/audio/speech", json=body) as r:
            if r.status_code != 200:
                yield ErrorFrame(error=f"Kokoro {r.status_code}: {(await r.aread())[:200]!r}")
                return
            await self.start_tts_usage_metrics(text)
            leftover = b""
            async for chunk in r.aiter_bytes(self.chunk_size):
                chunk = leftover + chunk
                # int16 samples: never split one across frames
                cut = len(chunk) - len(chunk) % 2
                leftover = chunk[cut:]
                if cut:
                    await self.stop_ttfb_metrics()
                    yield TTSAudioRawFrame(chunk[:cut], self.sample_rate, 1, context_id=context_id)
