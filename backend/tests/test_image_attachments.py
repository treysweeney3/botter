from __future__ import annotations

import base64
import json

import httpx
import pytest
from pydantic import ValidationError

from botterd.hermes import HermesClient
from botterd.models import ChatRequest
from botterd.normalize import normalize_row


PNG_DATA_URL = "data:image/png;base64," + base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"test-image"
).decode()


def test_chat_request_accepts_one_inline_image_and_rejects_disguised_bytes():
    request = ChatRequest.model_validate(
        {
            "message": [
                {"type": "text", "text": "Describe this"},
                {"type": "image_url", "image_url": {"url": PNG_DATA_URL, "detail": "auto"}},
            ]
        }
    )
    assert len(request.message) == 2

    fake_png = "data:image/png;base64," + base64.b64encode(b"not-a-png").decode()
    with pytest.raises(ValidationError, match="do not match"):
        ChatRequest.model_validate(
            {"message": [{"type": "image_url", "image_url": {"url": fake_png}}]}
        )


@pytest.mark.asyncio
async def test_hermes_run_wraps_multimodal_content_as_the_last_user_message():
    image_parts = [
        {"type": "text", "text": "Describe this"},
        {"type": "image_url", "image_url": {"url": PNG_DATA_URL, "detail": "auto"}},
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"data": []})
        if request.method == "POST" and request.url.path.endswith("/v1/runs"):
            payload = json.loads(request.content)
            assert payload["input"] == [{"role": "user", "content": image_parts}]
            assert payload["session_id"] == "session-1"
            return httpx.Response(202, json={"run_id": "run_image", "status": "started"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        client = HermesClient(http, "http://hermes", "secret", "provider/model")
        started = await client.start_run("vision", "session-1", image_parts)
        assert started["run_id"] == "run_image"
    finally:
        await http.aclose()


def test_normalize_row_preserves_image_for_history_rendering():
    message = normalize_row(
        {
            "id": "message-1",
            "session_id": "session-1",
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
            ],
            "timestamp": 1,
        },
        bot_id="bot-1",
    )
    assert message.kind == "attachment"
    assert message.text == "What is this?"
    assert message.attachments[0].media_type == "image/png"
    assert message.attachments[0].url == PNG_DATA_URL
