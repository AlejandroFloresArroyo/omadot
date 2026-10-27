import asyncio

import pytest

from omadot.ipc import CommandServer, send


@pytest.mark.asyncio
async def test_round_trip_sync_and_async_handlers(tmp_path):
    seen = []

    async def drop(req):
        seen.append(req["path"])
        return {"saved": True}

    server = CommandServer(tmp_path / "sock", {"drop": drop, "ping": lambda req: {"pong": req.get("n")}})
    await server.start()
    try:
        r1 = await asyncio.to_thread(send, tmp_path / "sock", "drop", path="/tmp/a.pdf")
        r2 = await asyncio.to_thread(send, tmp_path / "sock", "ping", n=3)
    finally:
        await server.close()
    assert r1 == {"ok": True, "saved": True}
    assert r2 == {"ok": True, "pong": 3}
    assert seen == ["/tmp/a.pdf"]


@pytest.mark.asyncio
async def test_unknown_command_and_handler_errors_are_replies_not_crashes(tmp_path):
    def boom(req):
        raise ValueError("nope")

    server = CommandServer(tmp_path / "sock", {"boom": boom})
    await server.start()
    try:
        r1 = await asyncio.to_thread(send, tmp_path / "sock", "nothing")
        r2 = await asyncio.to_thread(send, tmp_path / "sock", "boom")
        r3 = await asyncio.to_thread(send, tmp_path / "sock", "boom")  # still alive after the error
    finally:
        await server.close()
    assert r1["ok"] is False and "unknown" in r1["error"]
    assert r2 == {"ok": False, "error": "ValueError: nope"}
    assert r3["ok"] is False


def test_send_without_daemon_raises_connection_error(tmp_path):
    with pytest.raises(ConnectionError):
        send(tmp_path / "missing", "toggle")


@pytest.mark.asyncio
async def test_an_oversized_line_gets_an_error_reply_and_the_server_lives_on(tmp_path):
    import socket

    server = CommandServer(tmp_path / "sock", {"ping": lambda req: {"pong": True}})
    await server.start()
    try:
        def blast():
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(5)
                s.connect(str(tmp_path / "sock"))
                try:
                    s.sendall(b"x" * (2 << 20))
                    return s.recv(65536)
                except BrokenPipeError:  # the server refused the line before we finished sending it
                    return b"refused"

        reply = await asyncio.to_thread(blast)
        assert reply == b"refused" or b'"ok": false' in reply
        assert await asyncio.to_thread(send, tmp_path / "sock", "ping") == {"ok": True, "pong": True}
    finally:
        await server.close()
