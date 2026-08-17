from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from botterd.config import Settings
from botterd.errors import APIError
from botterd.global_auth import GlobalAuth
from botterd.mcp import PRESETS, McpService
from botterd.models import McpServerUpdate
from botterd.registry import CommandResult
from botterd.yaml_io import load_yaml, write_yaml_atomic


MAIN_CONFIG = """\
# ── Model ──────────────────────────────────────────────
model:
  default: provider/model
platforms:
  slack:
    enabled: true
# Keep this comment; botterd must never drop it.
gateway:
  multiplex_profiles: true
  toolsets:
    - terminal
    - web
"""


class RecordingEvents:
    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def publish(self, event: str, data: dict) -> None:
        self.published.append((event, data))


class RecordingRunner:
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    async def run(self, args, *, timeout=120, check=True):
        self.calls.append(tuple(str(item) for item in args))
        return CommandResult(0)


class FakeBotDatabase:
    def __init__(self, slugs: list[str]):
        self.slugs = slugs

    async def list_bots(self, *, include_archived: bool = True):
        return [{"slug": slug} for slug in self.slugs]


async def healthy() -> bool:
    return True


def settings_for(tmp_path: Path) -> Settings:
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles").mkdir(parents=True)
    (hermes_home / "config.yaml").write_text(MAIN_CONFIG, encoding="utf-8")
    return Settings(
        state_dir=tmp_path / "state",
        hermes_home=hermes_home,
        hermes_bin=tmp_path / "bin/hermes",
        token_override="mcp-token",
        api_server_key_override="api-server-key",
    )


def add_profile(settings: Settings, slug: str) -> Path:
    profile = settings.profiles_dir / slug
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text(MAIN_CONFIG, encoding="utf-8")
    (profile / ".env").touch()
    return profile


def service_for(
    tmp_path: Path, slugs: list[str] | None = None
) -> tuple[McpService, Settings, RecordingRunner, RecordingEvents]:
    settings = settings_for(tmp_path)
    for slug in slugs or []:
        add_profile(settings, slug)
    runner = RecordingRunner()
    events = RecordingEvents()
    global_auth = (
        GlobalAuth(settings, FakeBotDatabase(slugs), runner, healthy)  # type: ignore[arg-type]
        if slugs is not None
        else None
    )
    service = McpService(
        settings, events, runner=runner, health_check=healthy, global_auth=global_auth  # type: ignore[arg-type]
    )
    return service, settings, runner, events


COMPOSIO = McpServerUpdate(url="https://connect.composio.dev/mcp", auth="oauth")


@pytest.mark.asyncio
async def test_put_writes_every_profile_and_restarts_the_gateway(tmp_path):
    service, settings, runner, events = service_for(tmp_path, ["bot-one", "bot-two"])

    server, restarted = await service.put("composio", COMPOSIO)

    assert server.auth == "oauth"
    assert server.preset == "composio"
    assert server.label == "Composio"
    assert server.sync_status == "synced"
    # OAuth is not granted yet, so bots cannot use it.
    assert server.authorized is False
    assert server.status == "not_connected"
    for path in [
        settings.hermes_config_path,
        settings.profiles_dir / "bot-one/config.yaml",
        settings.profiles_dir / "bot-two/config.yaml",
    ]:
        entry = load_yaml(path)["mcp_servers"]["composio"]
        assert entry["url"] == "https://connect.composio.dev/mcp"
        assert entry["auth"] == "oauth"
    # The gateway reads mcp_servers once at startup, so a write must restart it.
    assert restarted is True
    assert [call for call in runner.calls if "kickstart" in call]
    assert events.published == [("mcp_updated", {"name": "composio", "status": "not_connected"})]


@pytest.mark.asyncio
async def test_write_preserves_comments_and_untouched_keys(tmp_path):
    service, settings, _, _ = service_for(tmp_path, ["bot-one"])

    await service.put("composio", COMPOSIO)

    text = settings.hermes_config_path.read_text(encoding="utf-8")
    assert "# ── Model ──────────────────────────────────────────────" in text
    assert "# Keep this comment; botterd must never drop it." in text
    document = load_yaml(settings.hermes_config_path)
    assert document["model"]["default"] == "provider/model"
    assert document["platforms"]["slack"]["enabled"] is True
    assert list(document["gateway"]["toolsets"]) == ["terminal", "web"]


@pytest.mark.asyncio
async def test_authorized_flag_follows_the_hermes_token_file(tmp_path):
    service, settings, _, _ = service_for(tmp_path, [])
    await service.put("composio", COMPOSIO)

    tokens = settings.hermes_home / "mcp-tokens"
    tokens.mkdir(parents=True)
    (tokens / "composio.json").write_text("{}", encoding="utf-8")

    server = await service.status("composio")
    assert server.authorized is True
    assert server.status == "connected"


@pytest.mark.asyncio
async def test_missing_entry_on_one_profile_reports_drift(tmp_path):
    service, settings, _, _ = service_for(tmp_path, ["bot-one", "bot-two"])
    await service.put("composio", COMPOSIO)

    # Simulate a profile that missed the write.
    stale = settings.profiles_dir / "bot-two/config.yaml"
    document = load_yaml(stale)
    del document["mcp_servers"]
    write_yaml_atomic(stale, document)

    server = await service.status("composio")
    assert server.sync_status == "out_of_sync"
    assert server.status == "error"
    assert "1 bot profile(s)" in (server.detail or "")


@pytest.mark.asyncio
async def test_delete_clears_every_profile_and_leaves_no_empty_mapping(tmp_path):
    service, settings, runner, _ = service_for(tmp_path, ["bot-one"])
    await service.put("composio", COMPOSIO)
    runner.calls.clear()

    removed, restarted = await service.delete("composio")

    assert removed.status == "not_connected"
    assert removed.detail == "Removed from every Botter bot."
    assert restarted is True
    for path in [settings.hermes_config_path, settings.profiles_dir / "bot-one/config.yaml"]:
        assert "mcp_servers" not in load_yaml(path)
    with pytest.raises(APIError) as missing:
        await service.status("composio")
    assert missing.value.status_code == 404


@pytest.mark.asyncio
async def test_repeat_write_is_a_no_op_and_skips_the_restart(tmp_path):
    service, _, runner, _ = service_for(tmp_path, ["bot-one"])
    await service.put("composio", COMPOSIO)
    runner.calls.clear()

    _, restarted = await service.put("composio", COMPOSIO)

    assert restarted is False
    assert [call for call in runner.calls if "kickstart" in call] == []


@pytest.mark.asyncio
async def test_list_offers_presets_only_until_they_are_installed(tmp_path):
    service, _, _, _ = service_for(tmp_path, [])

    servers, presets = await service.list()
    assert servers == []
    assert [item.name for item in presets] == [preset.name for preset in PRESETS]

    await service.put("composio", COMPOSIO)
    servers, presets = await service.list()
    assert [item.name for item in servers] == ["composio"]
    assert presets == []


@pytest.mark.asyncio
async def test_put_rejects_bad_names_urls_and_headers(tmp_path):
    service, _, _, _ = service_for(tmp_path, [])

    with pytest.raises(APIError) as bad_name:
        await service.put("Bad Name", COMPOSIO)
    assert bad_name.value.code == "invalid_mcp_name"

    with pytest.raises(APIError) as plain_http:
        await service.put("thing", McpServerUpdate(url="http://example.com/mcp"))
    assert plain_http.value.code == "invalid_mcp_url"

    with pytest.raises(APIError) as bad_header:
        await service.put(
            "thing",
            McpServerUpdate(url="https://example.com/mcp", auth="header", headers={"Bad Header": "x"}),
        )
    assert bad_header.value.code == "invalid_mcp_header"

    with pytest.raises(APIError) as newline:
        await service.put(
            "thing",
            McpServerUpdate(url="https://example.com/mcp", auth="header", headers={"X-Key": "a\nb"}),
        )
    assert newline.value.code == "invalid_mcp_header"

    with pytest.raises(APIError) as empty:
        await service.put("thing", McpServerUpdate(url="https://example.com/mcp", auth="header"))
    assert empty.value.code == "invalid_mcp_header"


@pytest.mark.asyncio
async def test_header_auth_keeps_the_env_reference_verbatim(tmp_path):
    """Hermes expands ${NAME} per profile, so botterd must not resolve it."""
    service, settings, _, _ = service_for(tmp_path, ["bot-one"])

    server, _ = await service.put(
        "vendor",
        McpServerUpdate(
            url="https://mcp.example.com/mcp", auth="header", headers={"X-Api-Key": "${VENDOR_KEY}"}
        ),
    )

    assert server.auth == "header"
    for path in [settings.hermes_config_path, settings.profiles_dir / "bot-one/config.yaml"]:
        assert load_yaml(path)["mcp_servers"]["vendor"]["headers"]["X-Api-Key"] == "${VENDOR_KEY}"


# ── OAuth: authorize once against main, then fan the grant out ───────────


class FakeServe:
    """Stands in for Hermes' dashboard MCP OAuth endpoints."""

    def __init__(self, home: Path, *, statuses: list[str] | None = None):
        self.home = home
        self.statuses = statuses or ["authorization_required", "approved"]
        self.requests: list[tuple[str, str]] = []

    def prewarm(self) -> None:
        self.requests.append(("PREWARM", ""))

    async def request(self, method, path, *, json=None, params=None, retry=True):
        self.requests.append((method, path))
        if method == "POST" and path.endswith("/auth"):
            return {
                "flow_id": "flow-abc",
                "server_name": path.split("/")[-2],
                "status": "authorization_required",
                "authorization_url": "https://login.composio.dev/authorize?x=1",
                "error": None,
            }
        if method == "GET" and path.startswith("/api/mcp/oauth/flows/"):
            status = self.statuses.pop(0) if self.statuses else "approved"
            if status == "approved":
                # Hermes writes the grant into main's profile on success.
                directory = self.home / "mcp-tokens"
                directory.mkdir(parents=True, exist_ok=True)
                (directory / "composio.json").write_text(
                    '{"access_token":"at-1","refresh_token":"rt-1","token_type":"Bearer","scope":"all"}',
                    encoding="utf-8",
                )
                (directory / "composio.client.json").write_text('{"client_id":"cid"}', encoding="utf-8")
                (directory / "composio.meta.json").write_text('{"issuer":"login"}', encoding="utf-8")
            return {
                "flow_id": "flow-abc",
                "server_name": "composio",
                "status": status,
                "authorization_url": "https://login.composio.dev/authorize?x=1",
                "error": None,
            }
        raise AssertionError(f"unexpected {method} {path}")


def with_serve(service: McpService, serve: FakeServe) -> McpService:
    service.serve = serve  # type: ignore[assignment]
    return service


async def settle(service: McpService, flow_id: str = "flow-abc"):
    """Poll a flow to its terminal state, the way the app does.

    Approval hands the fan-out and the gateway restart to a background task, so
    `approved` only arrives on a later poll.
    """
    for _ in range(400):
        flow, server = await service.authorization_status(flow_id)
        if flow.status in ("approved", "error"):
            return flow, server
        await asyncio.sleep(0.01)
    raise AssertionError(f"flow {flow_id} never settled")


@pytest.mark.asyncio
async def test_authorize_returns_the_url_and_approval_reaches_every_bot(tmp_path):
    service, settings, runner, events = service_for(tmp_path, ["bot-one", "bot-two"])
    serve = FakeServe(settings.hermes_home)
    with_serve(service, serve)
    await service.put("composio", COMPOSIO)
    runner.calls.clear()

    flow = await service.authorize("composio")
    assert flow.status == "authorization_required"
    assert flow.url == "https://login.composio.dev/authorize?x=1"
    assert "browser" in flow.instructions
    assert ("POST", "/api/mcp/servers/composio/auth") in serve.requests

    pending, server = await service.authorization_status("flow-abc")
    assert pending.status == "authorization_required"
    assert server is None

    approved, server = await settle(service)
    assert approved.status == "approved"
    assert server is not None and server.authorized is True
    assert server.status == "connected"
    assert server.sync_status == "synced"
    # The grant, its client registration, and its metadata all travel together.
    for slug in ("bot-one", "bot-two"):
        directory = settings.profiles_dir / slug / "mcp-tokens"
        for suffix in (".json", ".client.json", ".meta.json"):
            path = directory / f"composio{suffix}"
            assert path.read_bytes() == (settings.hermes_home / "mcp-tokens" / f"composio{suffix}").read_bytes()
        assert oct(path.stat().st_mode)[-3:] == "600"
    # The gateway must restart to reconnect holding the grant.
    assert [call for call in runner.calls if "kickstart" in call]


@pytest.mark.asyncio
async def test_approval_reports_finishing_instead_of_waiting_for_the_restart(tmp_path):
    """The poll that sees the approval must return at once.

    The fan-out and the gateway restart take tens of seconds. Running them
    inside the poll made the app look like it had missed the approval.
    """
    service, settings, runner, events = service_for(tmp_path, ["bot-one"])
    with_serve(service, FakeServe(settings.hermes_home, statuses=["approved"]))
    await service.put("composio", COMPOSIO)
    runner.calls.clear()
    await service.authorize("composio")

    # Hold the restart open, as a real gateway health poll would.
    restarted = asyncio.Event()

    async def blocked_health() -> bool:
        return restarted.is_set()

    service.health_check = blocked_health  # type: ignore[assignment]

    flow, server = await service.authorization_status("flow-abc")
    assert flow.status == "finishing"
    assert "restarting" in flow.instructions
    assert flow.server == "composio"
    # The browser step is done; the page must not be offered again.
    assert flow.url is None
    # Mid-fan-out the bots really are out of sync, which is not worth flashing
    # at anyone, so no row is reported until it is done.
    assert server is None
    # Let the background task reach the restart it cannot complete yet.
    await asyncio.sleep(0.05)
    assert [call for call in runner.calls if "kickstart" in call]
    again, _ = await service.authorization_status("flow-abc")
    assert again.status == "finishing"

    restarted.set()
    settled, server = await settle(service)
    assert settled.status == "approved"
    assert server is not None and server.authorized is True
    assert (settings.profiles_dir / "bot-one/mcp-tokens/composio.json").exists()
    assert [call for call in runner.calls if "kickstart" in call]
    # The app learns it is done from the event, without polling at all.
    assert ("mcp_updated", {"name": "composio", "status": "connected"}) in events.published


@pytest.mark.asyncio
async def test_the_finish_is_reported_without_asking_hermes_again(tmp_path):
    """Hermes expires its own flows, and botterd owns the ending after approval."""
    service, settings, _, _ = service_for(tmp_path, ["bot-one"])
    serve = FakeServe(settings.hermes_home, statuses=["approved"])
    with_serve(service, serve)
    await service.put("composio", COMPOSIO)
    await service.authorize("composio")

    flow, _ = await settle(service)

    assert flow.status == "approved"
    flow_reads = [call for call in serve.requests if call[1].startswith("/api/mcp/oauth/flows/")]
    assert len(flow_reads) == 1


@pytest.mark.asyncio
async def test_a_failed_restart_surfaces_on_the_next_poll(tmp_path):
    class FailingRunner(RecordingRunner):
        async def run(self, args, *, timeout=120, check=True):
            await super().run(args, timeout=timeout, check=check)
            raise RuntimeError("launchctl unavailable")

    service, settings, _, _ = service_for(tmp_path, ["bot-one"])
    with_serve(service, FakeServe(settings.hermes_home, statuses=["approved"]))
    await service.put("composio", COMPOSIO)
    await service.authorize("composio")
    service.runner = FailingRunner()  # type: ignore[assignment]

    flow, server = await settle(service)

    assert flow.status == "error"
    assert "restart" in (flow.error or "")
    assert server is None


@pytest.mark.asyncio
async def test_listing_servers_prewarms_the_dashboard_child(tmp_path):
    """Spawning `hermes serve` inside the authorize click is what made it slow."""
    service, settings, _, _ = service_for(tmp_path, [])
    serve = FakeServe(settings.hermes_home)
    with_serve(service, serve)

    await service.list()

    assert ("PREWARM", "") in serve.requests


@pytest.mark.asyncio
async def test_authorize_rejects_an_unknown_server_before_starting_a_flow(tmp_path):
    service, settings, _, _ = service_for(tmp_path, [])
    serve = FakeServe(settings.hermes_home)
    with_serve(service, serve)

    with pytest.raises(APIError) as missing:
        await service.authorize("nope")

    assert missing.value.status_code == 404
    assert serve.requests == []


@pytest.mark.asyncio
async def test_authorization_status_rejects_a_malformed_flow_id(tmp_path):
    service, settings, _, _ = service_for(tmp_path, [])
    with_serve(service, FakeServe(settings.hermes_home))

    with pytest.raises(APIError) as bad:
        await service.authorization_status("../etc/passwd")
    assert bad.value.code == "invalid_flow_id"


@pytest.mark.asyncio
async def test_a_bot_missing_the_grant_reports_drift(tmp_path):
    service, settings, _, _ = service_for(tmp_path, ["bot-one", "bot-two"])
    with_serve(service, FakeServe(settings.hermes_home, statuses=["approved"]))
    await service.put("composio", COMPOSIO)
    await service.authorize("composio")
    await settle(service)

    # A bot that lost its grant cannot use the server, even with the config entry.
    (settings.profiles_dir / "bot-two/mcp-tokens/composio.json").unlink()

    server = await service.status("composio")
    assert server.sync_status == "out_of_sync"
    assert "1 bot profile(s)" in (server.detail or "")


@pytest.mark.asyncio
async def test_a_refreshed_access_token_is_not_treated_as_drift(tmp_path):
    """An access token rotates on refresh; only the stable fields identify a grant."""
    service, settings, _, _ = service_for(tmp_path, ["bot-one"])
    with_serve(service, FakeServe(settings.hermes_home, statuses=["approved"]))
    await service.put("composio", COMPOSIO)
    await service.authorize("composio")
    await settle(service)

    (settings.profiles_dir / "bot-one/mcp-tokens/composio.json").write_text(
        '{"access_token":"at-ROTATED","refresh_token":"rt-1","token_type":"Bearer","scope":"all"}',
        encoding="utf-8",
    )

    server = await service.status("composio")
    assert server.sync_status == "synced"


@pytest.mark.asyncio
async def test_delete_removes_the_grant_everywhere(tmp_path):
    service, settings, _, _ = service_for(tmp_path, ["bot-one"])
    with_serve(service, FakeServe(settings.hermes_home, statuses=["approved"]))
    await service.put("composio", COMPOSIO)
    await service.authorize("composio")
    await settle(service)

    await service.delete("composio")

    # An orphaned grant would silently re-authorize a re-added server.
    for home in (settings.hermes_home, settings.profiles_dir / "bot-one"):
        for suffix in (".json", ".client.json", ".meta.json"):
            assert not (home / "mcp-tokens" / f"composio{suffix}").exists()


@pytest.mark.asyncio
async def test_authorize_without_the_dashboard_child_fails_cleanly(tmp_path):
    service, _, _, _ = service_for(tmp_path, [])
    await service.put("composio", COMPOSIO)

    with pytest.raises(APIError) as unavailable:
        await service.authorize("composio")
    assert unavailable.value.code == "hermes_dashboard_unavailable"


@pytest.mark.asyncio
async def test_a_failed_profile_write_rolls_every_file_back(tmp_path):
    service, settings, _, _ = service_for(tmp_path, ["bot-one", "bot-two"])
    originals = {
        path: path.read_bytes()
        for path in [
            settings.hermes_config_path,
            settings.profiles_dir / "bot-one/config.yaml",
            settings.profiles_dir / "bot-two/config.yaml",
        ]
    }
    # A profile whose config is not a mapping fails mid-fan-out.
    (settings.profiles_dir / "bot-two/config.yaml").write_text("- not a mapping\n", encoding="utf-8")

    with pytest.raises(APIError) as error:
        await service.put("composio", COMPOSIO)

    assert error.value.code == "global_config_sync_failed"
    # bot-one is written before bot-two fails; it must be rolled back.
    assert (settings.profiles_dir / "bot-one/config.yaml").read_bytes() == originals[
        settings.profiles_dir / "bot-one/config.yaml"
    ]
    assert settings.hermes_config_path.read_bytes() == originals[settings.hermes_config_path]
