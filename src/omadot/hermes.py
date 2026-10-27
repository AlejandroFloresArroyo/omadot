"""Client for Hermes Agent's runs API (POST /v1/runs, SSE events, stop, approval)."""

import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

# A conversation is a session; a new one starts after this long without a turn, or after this many turns.
# Hermes' long-lived memory survives sessions; the per-session history is what grows the prompt (measured:
# first token 1.1-1.3 s on a fresh session vs 1.8-3.3 s on one with a day of turns).
SESSION_IDLE = 10 * 60.0
SESSION_TURNS = 24

TERMINAL_OK = {"run.completed"}
TERMINAL_ERR = {"run.failed", "run.cancelled", "run.interrupted"}
# The events the agent reacts to besides text: a tool runs (Omi "works"), Hermes asks permission.
RELAYED = {"message.delta", "tool.started", "tool.completed", "approval.request"}


class HermesUnavailable(RuntimeError):
    """Hermes never got the turn (gateway down or failing): safe to send it again."""


def read_env(path: Path) -> dict[str, str]:
    """KEY=value lines of a Hermes .env; {} when the file does not exist."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return {}
    out = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and not key.lstrip().startswith("#"):
            out[key.strip()] = value.strip().strip('"')
    return out


def read_api_key(env_file: Path) -> str:
    if key := read_env(env_file).get("API_SERVER_KEY"):
        return key
    raise RuntimeError(f"API_SERVER_KEY not found in {env_file}")


class HermesClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        session_id: str,
        instructions: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        rotate: bool = True,
        clock=time.monotonic,
    ):
        self._base_session = session_id
        self.session_id = f"{session_id}-{uuid.uuid4().hex[:8]}" if rotate else session_id
        self.instructions = instructions
        self._rotate = rotate
        self._clock = clock
        self._last_turn: float | None = None
        self._turns = 0
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(10.0, read=None),
            transport=transport,
        )

    def _session_for_turn(self) -> str:
        now = self._clock()
        if self._rotate and ((self._last_turn is not None and now - self._last_turn > SESSION_IDLE) or self._turns >= SESSION_TURNS):
            self.session_id = f"{self._base_session}-{uuid.uuid4().hex[:8]}"
            self._turns = 0
        self._last_turn = now
        self._turns += 1
        return self.session_id

    async def start(self, text: str) -> str:
        body = {"input": text, "session_id": self._session_for_turn()}
        if self.instructions:
            body["instructions"] = self.instructions
        try:
            r = await self._http.post("/v1/runs", json=body)
        except httpx.TransportError as e:
            raise HermesUnavailable(f"{type(e).__name__}: {e}") from e
        if r.status_code >= 500:
            raise HermesUnavailable(f"HTTP {r.status_code}: {r.text[:200]}")
        r.raise_for_status()
        return r.json()["run_id"]

    async def events(self, run_id: str) -> AsyncIterator[dict]:
        """Yield the relayed events (text deltas, tool progress, approval requests) until the run ends."""
        async with self._http.stream("GET", f"/v1/runs/{run_id}/events") as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:])
                kind = ev.get("event")
                if kind in RELAYED:
                    yield ev
                elif kind in TERMINAL_OK:
                    return
                elif kind in TERMINAL_ERR:
                    raise RuntimeError(f"{kind}: {ev.get('error', '')}")

    async def deltas(self, run_id: str) -> AsyncIterator[str]:
        """Only the assistant's text."""
        async for ev in self.events(run_id):
            if ev["event"] == "message.delta":
                yield ev["delta"]

    async def approve(self, run_id: str, request_id: str, choice: str) -> None:
        """Answer an approval.request: choice is once | session | always | deny."""
        r = await self._http.post(f"/v1/runs/{run_id}/approval", json={"choice": choice, "request_id": request_id})
        r.raise_for_status()

    async def stop(self, run_id: str) -> None:
        r = await self._http.post(f"/v1/runs/{run_id}/stop")
        r.raise_for_status()

    async def aclose(self) -> None:
        await self._http.aclose()
