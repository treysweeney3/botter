from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from botterd.chat import ChatManager
from botterd.db import Database
from botterd.events import EventBus
from botterd.hermes import HermesClient
from botterd.models import Bot


@pytest.mark.asyncio
async def test_chat_proxy_translates_run_stream_and_approval_choice(tmp_path):
    seen_requests: list[httpx.Request] = []
    message_reads = 0
    stream_text = "\n\n".join(
        [
            'data: {"event":"message.delta","delta":"Hello ","timestamp":1}',
            'data: {"event":"tool.started","tool":"terminal","preview":"echo hi","timestamp":2}',
            'data: {"event":"tool.completed","tool":"terminal","preview":"hi","timestamp":3}',
            'data: {"event":"approval.request","description":"Send the result","timestamp":4}',
            'data: {"event":"run.completed","output":"Hello done","timestamp":5}',
        ]
    ) + "\n\n: stream closed\n\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal message_reads
        seen_requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/messages"):
            message_reads += 1
            if message_reads > 1:
                return httpx.Response(
                    200,
                    json={"data": [{"id": 42, "session_id": "session-1", "role": "assistant", "content": "Hello done", "timestamp": 5}]},
                )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"role": "user", "content": "oldest"},
                        {"role": "assistant", "content": "newest"},
                    ]
                },
            )
        if request.method == "POST" and request.url.path.endswith("/v1/runs"):
            body = json.loads(request.content)
            assert body["session_id"] == "session-1"
            assert body["conversation_history"] == [
                {"role": "user", "content": "oldest"},
                {"role": "assistant", "content": "newest"},
            ]
            return httpx.Response(202, json={"run_id": "run_1", "status": "started"})
        if request.method == "GET" and request.url.path.endswith("/events"):
            return httpx.Response(200, text=stream_text, headers={"content-type": "text/event-stream"})
        if request.method == "POST" and request.url.path.endswith("/approval"):
            assert json.loads(request.content) == {"choice": "once"}
            return httpx.Response(200, json={"run_id": "run_1", "choice": "once", "resolved": 1})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    db = Database(tmp_path / "botter.db")
    await db.connect()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    hermes = HermesClient(client, "http://hermes", "secret", "provider/model")
    manager = ChatManager(hermes, db, EventBus())
    bot = Bot(
        id="bot-1", slug="sales", display_name="Sales", title="Outbound", description="Sell",
        avatar_color="#2EC7A6", avatar_glyph="paperplane", approval_boundary="Ask",
        default_session_id="session-1", archived=False,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    await db.insert_bot({
        **bot.model_dump(mode="json"),
        "archived": 0,
    })
    try:
        active = await manager.start(bot, "session-1", "hello")
        output = "".join([chunk async for chunk in manager.stream(active, heartbeat_seconds=1)])
        assert "event: delta" in output
        assert '"text":"Hello "' in output
        assert "event: tool_event" in output
        assert '"status":"ok"' in output
        assert "event: approval_required" in output
        assert "event: message_complete" in output
        assert '"id":"42"' in output
        pending = await db.get_approval("run_1")
        assert pending and pending["summary"] == "Send the result"
        await hermes.approve("sales", "run_1", "once")
    finally:
        await manager.close()
        await client.aclose()
        await db.close()


class GatedStream(httpx.AsyncByteStream):
    def __init__(self, gate: asyncio.Event):
        self.gate = gate

    async def __aiter__(self):
        yield b'data: {"event":"message.delta","delta":"first"}\n\n'
        await self.gate.wait()
        yield b'data: {"event":"run.completed","output":"finished","timestamp":5}\n\n'


@pytest.mark.asyncio
async def test_downstream_disconnect_does_not_cancel_upstream_consumer(tmp_path):
    gate = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"data": []})
        if request.method == "POST":
            return httpx.Response(202, json={"run_id": "run_detached", "status": "started"})
        return httpx.Response(200, stream=GatedStream(gate), headers={"content-type": "text/event-stream"})

    db = Database(tmp_path / "botter.db")
    await db.connect()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    manager = ChatManager(HermesClient(client, "http://hermes", "key", "model"), db, EventBus())
    bot = Bot(
        id="bot", slug="worker", display_name="Worker", title="Worker", description="Works",
        avatar_color="#3B82F6", avatar_glyph="briefcase", approval_boundary="Ask",
        default_session_id="session", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    active = await manager.start(bot, "session", "go")
    downstream = manager.stream(active, heartbeat_seconds=1)
    first = await anext(downstream)
    assert "event: delta" in first
    await downstream.aclose()
    assert active.task is not None and not active.task.cancelled() and not active.task.done()
    gate.set()
    await asyncio.wait_for(active.task, timeout=1)
    await manager.close()
    await client.aclose()
    await db.close()
