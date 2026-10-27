"""Command channel between `omadot <command>` and the daemon: a Unix stream socket, one JSON line per request.

The signals (SIGUSR1 toggle, SIGUSR2 retry, SIGHUP reload) stay for a click on Omi and the settings panel;
the double Super (`tap`) and everything with arguments (drop a file, ask a question, press a card button,
report progress) goes here.
"""

import asyncio
import json
import os
import socket
from collections.abc import Awaitable, Callable
from pathlib import Path

SOCKET = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "omadot" / "sock"
LIMIT = 1 << 20  # a request line longer than 1 MiB is refused, not crashed on

Handler = Callable[[dict], Awaitable[dict] | dict]


class CommandServer:
    """Dispatches `{"cmd": name, ...}` requests to handlers; replies `{"ok": true, ...}` or `{"ok": false, "error": ...}`."""

    def __init__(self, path: Path, handlers: dict[str, Handler]):
        self._path = path
        self._handlers = handlers
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.unlink(missing_ok=True)
        self._server = await asyncio.start_unix_server(self._serve, path=str(self._path), limit=LIMIT)
        os.chmod(self._path, 0o600)

    async def close(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._path.unlink(missing_ok=True)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            try:
                line = await reader.readline()
                req = json.loads(line or b"{}")
                handler = self._handlers.get(req.get("cmd", ""))
                if handler is None:
                    reply = {"ok": False, "error": f"unknown command {req.get('cmd')!r}"}
                else:
                    result = handler(req)
                    if asyncio.iscoroutine(result):
                        result = await result
                    reply = {"ok": True, **(result or {})}
            except Exception as e:  # a bad request must not take the daemon down
                reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            try:
                writer.write((json.dumps(reply, ensure_ascii=False) + "\n").encode())
                await writer.drain()
            except (ConnectionError, OSError):
                pass  # the client went away before reading: routine, not an error
        finally:
            writer.close()


def send(sock: Path, cmd: str, timeout: float = 5.0, **params) -> dict:
    """Blocking client for the CLI. Raises ConnectionError when the daemon is not listening."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(str(sock))
            s.sendall((json.dumps({"cmd": cmd, **params}, ensure_ascii=False) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
    except BrokenPipeError as e:
        raise ConnectionError("omadot closed the connection: the request was too large") from e
    except OSError as e:
        raise ConnectionError(f"omadot is not running ({e})") from e
    return json.loads(data or b'{"ok": false, "error": "empty reply"}')
