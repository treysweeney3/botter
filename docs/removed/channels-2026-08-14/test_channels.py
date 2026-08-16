from __future__ import annotations

import asyncio

import httpx
import pytest

from botterd.channels import EXCLUDED_CHANNELS, ChannelService
from botterd.errors import APIError
from botterd.hermes_serve import SESSION_HEADER, HermesServe
from botterd.models import ChannelUpdate


class RecordingEvents:
    def __init__(self):
        self.published: list[tuple[str, dict[str, str]]] = []

    async def publish(self, event: str, data: dict[str, str]) -> None:
        self.published.append((event, data))


class RecordingRunner:
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    async def run(self, args, *, timeout=120, check=True):
        from botterd.registry import CommandResult

        self.calls.append(tuple(str(item) for item in args))
        return CommandResult(0)


class FakeServe:
    """Stands in for HermesServe with scripted dashboard responses."""

    def __init__(self, catalog: list[dict], statuses: dict[str, dict] | None = None):
        self.catalog = catalog
        self.statuses = statuses or {}
        self.requests: list[tuple[str, str, dict | None]] = []

    async def request(self, method, path, *, json=None, params=None, retry=True):
        self.requests.append((method, path, json))
        if method == "GET" and path == "/api/messaging/platforms":
            return {"platforms": self.catalog}
        if method == "PUT" and path.startswith("/api/messaging/platforms/"):
            return {"ok": True, "platform": path.rsplit("/", 1)[-1]}
        if method == "POST" and path.endswith("/test"):
            channel_id = path.split("/")[-2]
            return self.statuses.get(channel_id) or next(
                entry for entry in self.catalog if entry["id"] == channel_id
            )
        raise AssertionError(f"Unexpected dashboard request: {method} {path}")


def catalog_entry(channel_id: str, **overrides) -> dict:
    entry = {
        "id": channel_id,
        "name": channel_id.title(),
        "description": f"{channel_id} channel",
        "docs_url": f"https://docs.example/{channel_id}",
        "enabled": False,
        "configured": False,
        "gateway_running": True,
        "state": "disabled",
        "error_code": None,
        "error_message": None,
        "updated_at": None,
        "home_channel": None,
        "env_vars": [
            {
                "key": f"{channel_id.upper()}_BOT_TOKEN",
                "required": True,
                "is_set": False,
                "redacted_value": None,
                "description": "Bot token",
                "prompt": "Bot token",
                "help": "",
                "url": None,
                "is_password": True,
                "advanced": False,
            }
        ],
    }
    entry.update(overrides)
    return entry


async def healthy() -> bool:
    return True


@pytest.mark.asyncio
async def test_channel_list_maps_payloads_and_excludes_locked_platforms():
    serve = FakeServe(
        [
            catalog_entry("telegram"),
            catalog_entry("slack"),
            catalog_entry("api_server"),
            catalog_entry("webhook"),
            catalog_entry("discord", enabled=True, configured=True, state="running"),
        ]
    )
    service = ChannelService(serve, RecordingEvents(), runner=RecordingRunner(), health_check=healthy)

    channels = await service.list()

    assert [channel.id for channel in channels] == ["telegram", "discord"]
    telegram = channels[0]
    assert telegram.env_vars[0].key == "TELEGRAM_BOT_TOKEN"
    assert telegram.env_vars[0].label == "Bot token"
    assert telegram.env_vars[0].is_password is True
    assert channels[1].state == "running"


@pytest.mark.asyncio
async def test_channel_update_writes_via_dashboard_then_restarts_and_publishes():
    serve = FakeServe(
        [catalog_entry("telegram")],
        statuses={
            "telegram": catalog_entry(
                "telegram", enabled=True, configured=True, state="running",
                env_vars=[
                    {
                        "key": "TELEGRAM_BOT_TOKEN", "required": True, "is_set": True,
                        "redacted_value": "••••1234", "description": "", "prompt": "Bot token",
                        "help": "", "url": None, "is_password": True, "advanced": False,
                    }
                ],
            )
        },
    )
    events = RecordingEvents()
    runner = RecordingRunner()
    service = ChannelService(serve, events, runner=runner, health_check=healthy)

    channel, restarted = await service.update(
        "telegram",
        ChannelUpdate(env={"TELEGRAM_BOT_TOKEN": " token-value "}, enabled=True),
    )

    method, path, body = serve.requests[0]
    assert (method, path) == ("PUT", "/api/messaging/platforms/telegram")
    assert body == {"env": {"TELEGRAM_BOT_TOKEN": "token-value"}, "clear_env": [], "enabled": True}
    assert any("kickstart" in call for call in runner.calls[0])
    assert channel.state == "running"
    assert restarted is True
    assert events.published == [("channel_updated", {"id": "telegram", "state": "running"})]


@pytest.mark.asyncio
async def test_channel_update_guards_and_empty_body():
    service = ChannelService(FakeServe([]), RecordingEvents(), runner=RecordingRunner(), health_check=healthy)

    with pytest.raises(APIError) as slack_error:
        await service.update("slack", ChannelUpdate(enabled=False))
    assert slack_error.value.status_code == 403
    assert slack_error.value.code == "channel_not_managed"

    for excluded in EXCLUDED_CHANNELS - {"slack"}:
        with pytest.raises(APIError) as excluded_error:
            await service.update(excluded, ChannelUpdate(enabled=False))
        assert excluded_error.value.status_code == 404

    with pytest.raises(APIError) as empty_error:
        await service.update("telegram", ChannelUpdate())
    assert empty_error.value.code == "empty_channel_update"


@pytest.mark.asyncio
async def test_channel_update_restart_failure_maps_to_502(monkeypatch):
    async def failing_restart(runner, health_check):
        raise RuntimeError("Hermes gateway did not become healthy after restart")

    import botterd.channels

    monkeypatch.setattr(botterd.channels, "restart_gateway", failing_restart)
    service = ChannelService(
        FakeServe([catalog_entry("telegram")]),
        RecordingEvents(),
        runner=RecordingRunner(),
        health_check=healthy,
    )
    with pytest.raises(APIError) as error:
        await service.update("telegram", ChannelUpdate(enabled=True))
    assert error.value.status_code == 502
    assert error.value.code == "gateway_restart_failed"


@pytest.mark.asyncio
async def test_hermes_serve_parses_ready_sentinel_and_authenticates_requests(tmp_path, monkeypatch):
    from botterd.config import Settings

    settings = Settings(
        state_dir=tmp_path / "state",
        hermes_home=tmp_path / "hermes",
        hermes_bin=tmp_path / "bin/hermes",
        token_override="token",
    )
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["token"] = request.headers.get(SESSION_HEADER, "")
        if request.url.path == "/api/messaging/platforms":
            return httpx.Response(200, json={"platforms": []})
        return httpx.Response(404, json={"detail": "Unknown messaging platform: nope"})

    serve = HermesServe(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def fake_spawn():
        return await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", "echo HERMES_BACKEND_READY port=45678; sleep 30",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(serve, "_spawn", fake_spawn)
    try:
        payload = await serve.request("GET", "/api/messaging/platforms")
        assert serve.base_url == "http://127.0.0.1:45678"
        assert payload == {"platforms": []}
        assert seen["token"] == serve._token

        with pytest.raises(APIError) as error:
            await serve.request("GET", "/api/messaging/platforms/nope")
        assert error.value.status_code == 404
        assert error.value.code == "channel_not_found"
        assert "nope" in error.value.message
    finally:
        await serve.close()


@pytest.mark.asyncio
async def test_hermes_serve_reports_child_that_dies_before_ready(tmp_path, monkeypatch):
    from botterd.config import Settings

    settings = Settings(
        state_dir=tmp_path / "state",
        hermes_home=tmp_path / "hermes",
        hermes_bin=tmp_path / "bin/hermes",
        token_override="token",
    )
    serve = HermesServe(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))))

    async def fake_spawn():
        return await asyncio.create_subprocess_exec(
            "/bin/sh", "-c", "echo boot failure; exit 3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    monkeypatch.setattr(serve, "_spawn", fake_spawn)
    try:
        with pytest.raises(APIError) as error:
            await serve.ensure_ready()
        assert error.value.status_code == 502
        assert error.value.code == "hermes_dashboard_unavailable"
        assert "boot failure" in error.value.message
    finally:
        await serve.close()
