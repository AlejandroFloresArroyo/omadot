import json

import httpx
import pytest

from omadot.hermes import HermesClient, HermesUnavailable


def _sse(events):
    lines = [": keepalive", ""]
    for i, ev in enumerate(events):
        lines += [f"id: {i}", f"data: {json.dumps(ev)}", ""]
    return "\n".join(lines).encode()


def _client(handler, **kw):
    return HermesClient(
        base_url="http://hermes.test",
        api_key="k",
        session_id="omadot",
        transport=httpx.MockTransport(handler),
        rotate=False,
        **kw,
    )


@pytest.mark.asyncio
async def test_run_streams_deltas_until_completed():
    seen = []

    def handler(request: httpx.Request):
        seen.append((request.method, request.url.path, request.headers["authorization"]))
        if request.url.path == "/v1/runs":
            body = json.loads(request.content)
            assert body == {"input": "hola", "session_id": "omadot"}
            return httpx.Response(200, json={"run_id": "r1", "status": "started"})
        return httpx.Response(
            200,
            content=_sse(
                [
                    {"event": "message.delta", "delta": "Ho"},
                    {"event": "tool.started", "tool": "terminal"},
                    {"event": "message.delta", "delta": "la."},
                    {"event": "run.completed", "output": "Hola."},
                    {"event": "message.delta", "delta": "never"},
                ]
            ),
        )

    client = _client(handler)
    run = await client.start("hola")
    deltas = [d async for d in client.deltas(run)]
    events = [e["event"] async for e in client.events(run)]

    assert deltas == ["Ho", "la."]
    assert events == ["message.delta", "tool.started", "message.delta"]
    assert seen[0] == ("POST", "/v1/runs", "Bearer k")
    assert seen[1][1] == "/v1/runs/r1/events"
    await client.aclose()


@pytest.mark.asyncio
async def test_voice_instructions_ride_along_with_each_run():
    bodies = []

    def handler(request: httpx.Request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"run_id": "r9", "status": "started"})

    client = HermesClient(
        base_url="http://hermes.test",
        api_key="k",
        session_id="omadot",
        instructions="Responde breve.",
        transport=httpx.MockTransport(handler),
        rotate=False,
    )
    await client.start("hola")
    assert bodies == [{"input": "hola", "session_id": "omadot", "instructions": "Responde breve."}]
    await client.aclose()


@pytest.mark.asyncio
async def test_failed_run_raises():
    def handler(request: httpx.Request):
        if request.url.path == "/v1/runs":
            return httpx.Response(200, json={"run_id": "r2", "status": "started"})
        return httpx.Response(200, content=_sse([{"event": "run.failed", "error": "boom"}]))

    client = _client(handler)
    run = await client.start("x")
    with pytest.raises(RuntimeError, match="boom"):
        [d async for d in client.deltas(run)]
    await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "respond",
    [
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("refused", request=request)),
        lambda request: httpx.Response(503, text="gateway restarting"),
    ],
    ids=["gateway down", "gateway 5xx"],
)
async def test_a_run_that_never_started_is_reported_as_unavailable(respond):
    # Only these are safe to retry: Hermes never saw the turn, so nothing ran twice.
    client = _client(respond)
    with pytest.raises(HermesUnavailable):
        await client.start("hola")
    await client.aclose()


@pytest.mark.asyncio
async def test_a_rejected_run_is_not_unavailable():
    client = _client(lambda request: httpx.Response(401, text="bad key"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.start("hola")
    await client.aclose()


@pytest.mark.asyncio
async def test_stop_posts_to_run():
    paths = []

    def handler(request: httpx.Request):
        paths.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "stopping"})

    client = _client(handler)
    await client.stop("r3")
    assert paths == [("POST", "/v1/runs/r3/stop")]
    await client.aclose()


@pytest.mark.asyncio
async def test_approve_posts_the_choice_for_the_request():
    calls = []

    def handler(request: httpx.Request):
        calls.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "running"})

    client = _client(handler)
    await client.approve("r4", "req-1", "deny")
    assert calls == [("POST", "/v1/runs/r4/approval", {"choice": "deny", "request_id": "req-1"})]
    await client.aclose()


@pytest.mark.asyncio
async def test_the_session_rotates_after_idle_time_or_many_turns():
    from omadot.hermes import SESSION_IDLE, SESSION_TURNS

    sessions = []
    clock = {"t": 0.0}

    def handler(request: httpx.Request):
        sessions.append(json.loads(request.content)["session_id"])
        return httpx.Response(200, json={"run_id": "r", "status": "started"})

    client = HermesClient(base_url="http://hermes.test", api_key="k", session_id="omadot", transport=httpx.MockTransport(handler), clock=lambda: clock["t"])
    await client.start("a")
    clock["t"] += 60
    await client.start("b")
    assert sessions[0] == sessions[1] and sessions[0].startswith("omadot-")  # same conversation
    clock["t"] += SESSION_IDLE + 1
    await client.start("c")
    assert sessions[2] != sessions[1]  # a new conversation after the idle gap
    for _ in range(SESSION_TURNS):
        clock["t"] += 1
        await client.start("x")
    assert sessions[-1] != sessions[2]  # and after many turns
    assert len(set(sessions)) == 3
    await client.aclose()
