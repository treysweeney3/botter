from __future__ import annotations

import httpx
import pytest

from mockserver.main import create_app


@pytest.mark.asyncio
async def test_health_is_public_and_other_routes_require_constant_time_bearer_contract():
    app = create_app(token="test-token")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/v1/health")).status_code == 200
        missing = await client.get("/v1/bots")
        assert missing.status_code == 401
        assert missing.json() == {
            "error": {"code": "unauthorized", "message": "A valid bearer token is required"}
        }
        wrong = await client.get("/v1/bots", headers={"Authorization": "Bearer wrong"})
        assert wrong.status_code == 401
        good = await client.get("/v1/bots", headers={"Authorization": "Bearer test-token"})
        assert good.status_code == 200


@pytest.mark.asyncio
async def test_validation_and_unknown_routes_use_contract_error_envelope():
    app = create_app(token="test-token")
    headers = {"Authorization": "Bearer test-token"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.post("/v1/sessions/session-1/chat", headers=headers, json={"message": ""})
        assert invalid.status_code == 422
        assert set(invalid.json()["error"]) == {"code", "message"}
        missing = await client.get("/v1/not-a-route", headers=headers)
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"

