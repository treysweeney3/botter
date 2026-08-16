"""Tests for the single credential surface.

This file replaces test_connections.py and test_integrations.py. The two
services they covered are now one `CredentialService`, plus `GoogleAuthService`
for the one row that is an OAuth token file rather than an env value.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from botterd.config import Settings
from botterd.credentials import CURATED, PROTECTED_KEYS, SLACK_KEY, CredentialService
from botterd.errors import APIError
from botterd.global_auth import GlobalAuth
from botterd.google_auth import GOOGLE_KEY, GoogleAuthService
from botterd.models import GoogleConnect
from botterd.registry import CommandResult


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


class ScriptedRunner:
    """Maps a matched CLI flag to a scripted result, with optional side effects."""

    def __init__(self, scripts):
        self.scripts = scripts
        self.calls: list[tuple[str, ...]] = []

    async def run(self, args, *, timeout=120, check=True):
        call = tuple(str(item) for item in args)
        self.calls.append(call)
        for flag, result in self.scripts.items():
            if flag in call:
                if callable(result):
                    return result()
                return result
        return CommandResult(0)


class FakeBotDatabase:
    def __init__(self, slugs: list[str]):
        self.slugs = slugs

    async def list_bots(self, *, include_archived: bool = True):
        return [{"slug": slug} for slug in self.slugs]


class FakeServe:
    def __init__(self, catalog: dict[str, dict]):
        self.catalog = catalog
        self.requests: list[tuple[str, str, dict | None]] = []

    async def request(self, method, path, *, json=None, params=None, retry=True):
        self.requests.append((method, path, json))
        if method == "GET" and path == "/api/env":
            return self.catalog
        if method == "PUT" and path == "/api/env":
            key = json["key"]
            if key == "PATH":
                raise APIError(400, "invalid_channel_update", "PATH is not an allowed key")
            row = self.catalog.setdefault(key, {"category": "custom", "custom": True, "is_password": True})
            row["is_set"] = True
            row["redacted_value"] = "••••9999"
            return {"ok": True}
        if method == "DELETE" and path == "/api/env":
            key = json["key"]
            row = self.catalog.get(key)
            if not row or not row.get("is_set"):
                raise APIError(404, "channel_not_found", f"{key} not found in .env")
            if row.get("custom"):
                self.catalog.pop(key)
            else:
                row["is_set"] = False
                row["redacted_value"] = None
            return {"found": True}
        raise AssertionError(f"Unexpected request {method} {path}")


class FakeGlobalAuth:
    def __init__(self):
        self.mutations: list[tuple[str, str | None]] = []
        self.lock = asyncio.Lock()

    async def mutate_dashboard_env(self, serve, key, value):
        self.mutations.append((key, value))
        if value is None:
            await serve.request("DELETE", "/api/env", json={"key": key})
        else:
            await serve.request("PUT", "/api/env", json={"key": key, "value": value})
        return True

    async def env_mismatches(self, keys):
        return ({key: None for key in keys}, {key: () for key in keys})

    async def _env_mismatches_locked(self, keys):
        return await self.env_mismatches(keys)


def env_row(**overrides) -> dict:
    row = {
        "is_set": False,
        "redacted_value": None,
        "description": "",
        "url": None,
        "category": "tool",
        "is_password": True,
        "tools": [],
        "advanced": False,
        "channel_managed": False,
        "provider": "",
        "provider_label": "",
        "custom": False,
    }
    row.update(overrides)
    return row


def catalog() -> dict[str, dict]:
    return {
        "BRAVE_API_KEY": env_row(description="Brave Search key", url="https://brave.com"),
        "NOTION_API_KEY": env_row(is_set=True, redacted_value="••••1111"),
        "ANTHROPIC_API_KEY": env_row(category="provider", provider_label="Anthropic", advanced=True),
        "TELEGRAM_BOT_TOKEN": env_row(category="messaging", channel_managed=True),
        "SLACK_HOME_CHANNEL": env_row(category="messaging", channel_managed=False, is_set=True),
        "GITHUB_TOKEN": env_row(is_set=True),
        "VERCEL_TEAM_ID": env_row(),
        "EXA_API_KEY": env_row(),
        "API_SERVER_KEY": env_row(is_set=True),
        "MY_CUSTOM_THING": env_row(category="custom", custom=True, is_set=True, redacted_value="••••2222"),
    }


def credential_settings(tmp_path: Path, *, slack_enabled: bool = False) -> Settings:
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles").mkdir(parents=True)
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": {"default": "provider/model"},
                "platforms": {"slack": {"enabled": slack_enabled}},
            }
        ),
        encoding="utf-8",
    )
    return Settings(
        state_dir=tmp_path / "state",
        hermes_home=hermes_home,
        hermes_bin=tmp_path / "bin/hermes",
        token_override="credentials-token",
        api_server_key_override="api-server-key",
    )


async def healthy() -> bool:
    return True


def make_service(
    tmp_path: Path,
    rows: dict[str, dict] | None = None,
    *,
    events=None,
    global_auth=None,
    google=None,
    runner=None,
    slack_enabled: bool = False,
) -> tuple[CredentialService, FakeServe, RecordingEvents]:
    serve = FakeServe(rows if rows is not None else catalog())
    recorded = events or RecordingEvents()
    service = CredentialService(
        credential_settings(tmp_path, slack_enabled=slack_enabled),
        serve,  # type: ignore[arg-type]
        recorded,  # type: ignore[arg-type]
        runner=runner or RecordingRunner(),  # type: ignore[arg-type]
        health_check=healthy,
        google=google,
        global_auth=global_auth,
    )
    return service, serve, recorded


# ── catalog shape ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_hides_channel_and_protected_keys_but_now_shows_curated(tmp_path):
    service, _, _ = make_service(tmp_path)

    rows = await service.list()

    keys = [item.key for item in rows]
    assert "TELEGRAM_BOT_TOKEN" not in keys
    assert "SLACK_HOME_CHANNEL" not in keys
    assert "API_SERVER_KEY" not in keys
    # The curated apps used to live behind /v1/connections. They are ordinary
    # rows in the one list now.
    assert "GITHUB_TOKEN" in keys
    assert "VERCEL_TEAM_ID" in keys
    # A curated key Hermes has never seen still gets a card.
    assert "XAI_API_KEY" in keys
    assert SLACK_KEY in keys


@pytest.mark.asyncio
async def test_curated_rows_rank_first_and_carry_their_overlay(tmp_path):
    service, _, _ = make_service(tmp_path)

    rows = await service.list()
    by_key = {item.key: item for item in rows}

    assert [item.key for item in rows][:3] == [
        "GITHUB_TOKEN",
        "VERCEL_TOKEN",
        "VERCEL_TEAM_ID",
    ]
    assert by_key["VERCEL_TOKEN"].group == "vercel"
    assert by_key["VERCEL_TEAM_ID"].group == "vercel"
    assert by_key["VERCEL_TOKEN"].group_label == "Vercel"
    assert by_key["VERCEL_TEAM_ID"].group_label == "Vercel"
    assert by_key["VERCEL_TOKEN"].required is True
    assert by_key["VERCEL_TEAM_ID"].required is False
    assert by_key["EXA_API_KEY"].restart_after_write is True
    assert by_key["GITHUB_TOKEN"].restart_after_write is False
    assert by_key["GITHUB_TOKEN"].status == "connected"
    assert by_key["XAI_API_KEY"].status == "not_connected"
    # Every generic row keeps its default.
    assert by_key["BRAVE_API_KEY"].group is None
    assert by_key["BRAVE_API_KEY"].auth == "value"


@pytest.mark.asyncio
async def test_kind_splits_service_keys_from_plain_config(tmp_path):
    rows = catalog()
    # Hermes returns unknown .env keys as custom password fields, even when
    # they are ordinary settings. These fixtures intentionally match live.
    rows["BROWSER_INACTIVITY_TIMEOUT"] = env_row(category="custom", custom=True, is_password=True, is_set=True)
    rows["IMAGE_TOOLS_DEBUG"] = env_row(category="custom", custom=True, is_password=True, is_set=True)
    rows["WEATHER_UNITS"] = env_row(category="setting", is_password=False)
    rows["OBSIDIAN_VAULT_PATH"] = env_row(category="skill", is_password=False, is_set=True)
    rows["AGENT_BROWSER_ENGINE"] = env_row(is_password=False, url="https://example.com/browser", advanced=True)
    rows["BROWSERBASE_PROJECT_ID"] = env_row(is_password=False, url="https://example.com/account", advanced=False)
    rows["VERTEX_CREDENTIALS_PATH"] = env_row(
        category="provider", is_password=False, url="https://example.com/iam", advanced=True
    )
    rows["MY_NEW_SERVICE_KEY"] = env_row(category="custom", custom=True, is_password=True)
    rows["MY_FEATURE_FLAG"] = env_row(category="custom", custom=True, is_password=True)
    rows["SENTRY_DSN"] = env_row(category="custom", custom=True, is_password=True)
    service, _, _ = make_service(tmp_path, rows)

    kinds = {item.key: item.kind for item in await service.list()}

    assert kinds["BROWSER_INACTIVITY_TIMEOUT"] == "config"
    assert kinds["IMAGE_TOOLS_DEBUG"] == "config"
    assert kinds["WEATHER_UNITS"] == "config"
    assert kinds["OBSIDIAN_VAULT_PATH"] == "config"
    assert kinds["AGENT_BROWSER_ENGINE"] == "config"
    assert kinds["BRAVE_API_KEY"] == "integration"           # secret
    assert kinds["ANTHROPIC_API_KEY"] == "integration"       # secret provider key
    assert kinds["BROWSERBASE_PROJECT_ID"] == "integration"  # service identifier
    assert kinds["VERTEX_CREDENTIALS_PATH"] == "integration" # credential file
    assert kinds["MY_NEW_SERVICE_KEY"] == "integration"      # custom credential
    assert kinds["MY_FEATURE_FLAG"] == "config"              # custom setting
    assert kinds["SENTRY_DSN"] == "integration"              # custom credential without _KEY
    # A curated key is always a credential, whatever the catalog says.
    assert kinds["VERCEL_TEAM_ID"] == "integration"


@pytest.mark.asyncio
async def test_labels_prefer_provider_then_curated_then_key(tmp_path):
    service, _, _ = make_service(tmp_path)

    by_key = {item.key: item for item in await service.list()}

    assert by_key["ANTHROPIC_API_KEY"].label == "Anthropic API key"
    assert by_key["BRAVE_API_KEY"].label == "Brave API Key"
    assert by_key["GITHUB_TOKEN"].label == "GitHub"
    assert by_key["VERCEL_TEAM_ID"].label == "Vercel Team ID"


# ── writes ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_writes_through_dashboard_and_publishes(tmp_path):
    service, serve, events = make_service(tmp_path)

    integration = await service.put("BRAVE_API_KEY", "  key-value  ")

    assert ("PUT", "/api/env", {"key": "BRAVE_API_KEY", "value": "key-value"}) in serve.requests
    assert integration.is_set is True
    assert integration.status == "connected"
    assert integration.redacted_value == "••••9999"
    assert events.published == [("integration_updated", {"key": "BRAVE_API_KEY", "is_set": True})]


@pytest.mark.asyncio
async def test_curated_keys_take_the_same_write_path_as_the_rest(tmp_path):
    global_auth = FakeGlobalAuth()
    service, serve, _ = make_service(tmp_path, global_auth=global_auth)

    github = await service.put("GITHUB_TOKEN", "curated-token")

    # The point of the merge: no raw .env edit, same lifecycle as every key.
    assert global_auth.mutations == [("GITHUB_TOKEN", "curated-token")]
    assert ("PUT", "/api/env", {"key": "GITHUB_TOKEN", "value": "curated-token"}) in serve.requests
    assert github.group == "github"
    assert github.sync_status == "synced"


@pytest.mark.asyncio
async def test_credential_uses_global_lifecycle_but_config_stays_main_only(tmp_path):
    rows = catalog()
    rows["IMAGE_TOOLS_DEBUG"] = env_row(category="custom", custom=True, is_password=True, is_set=True)
    global_auth = FakeGlobalAuth()
    service, _, _ = make_service(tmp_path, rows, global_auth=global_auth)

    credential = await service.put("BRAVE_API_KEY", "global-value")
    config_value = await service.put("IMAGE_TOOLS_DEBUG", "true")
    removed = await service.delete("BRAVE_API_KEY")

    assert global_auth.mutations == [("BRAVE_API_KEY", "global-value"), ("BRAVE_API_KEY", None)]
    assert credential.sync_status == "synced"
    assert credential.sync_detail == "Available to every Botter bot."
    assert config_value.kind == "config"
    assert config_value.sync_status is None
    assert removed.is_set is False
    assert removed.status == "not_connected"


@pytest.mark.asyncio
async def test_exa_write_restarts_the_gateway_and_others_do_not(tmp_path):
    runner = RecordingRunner()
    service, _, _ = make_service(tmp_path, runner=runner)

    exa = await service.put("EXA_API_KEY", "exa-value")

    kickstarts = [call for call in runner.calls if "kickstart" in call]
    assert len(kickstarts) == 1
    assert "Hermes gateway restarted for Exa." in (exa.detail or "")

    runner.calls.clear()
    await service.put("BRAVE_API_KEY", "brave-value")
    assert [call for call in runner.calls if "kickstart" in call] == []


@pytest.mark.asyncio
async def test_delete_maps_missing_and_clears_custom_rows(tmp_path):
    rows = catalog()
    rows["IMAGE_TOOLS_DEBUG"] = env_row(category="custom", custom=True, is_password=True, is_set=True)
    service, _, _ = make_service(tmp_path, rows)

    removed = await service.delete("NOTION_API_KEY")
    assert removed.is_set is False

    gone = await service.delete("IMAGE_TOOLS_DEBUG")
    assert gone.is_set is False
    assert gone.category == "custom"
    assert gone.custom is True
    assert gone.kind == "config"

    with pytest.raises(APIError) as missing:
        await service.delete("BRAVE_API_KEY")
    assert missing.value.status_code == 404
    assert missing.value.code == "integration_not_found"


@pytest.mark.asyncio
async def test_deleted_curated_key_keeps_its_card(tmp_path):
    service, _, _ = make_service(tmp_path)

    removed = await service.delete("GITHUB_TOKEN")

    assert removed.is_set is False
    assert removed.group == "github"
    assert removed.custom is False


@pytest.mark.asyncio
async def test_global_delete_preserves_not_found_contract(tmp_path):
    global_auth = FakeGlobalAuth()

    async def unchanged(serve, key, value):
        return False

    global_auth.mutate_dashboard_env = unchanged  # type: ignore[method-assign]
    service, _, events = make_service(tmp_path, global_auth=global_auth)

    with pytest.raises(APIError) as missing:
        await service.delete("BRAVE_API_KEY")

    assert missing.value.status_code == 404
    assert missing.value.code == "integration_not_found"
    assert events.published == []


@pytest.mark.asyncio
async def test_put_accepts_custom_keys_and_rejects_bad_input(tmp_path):
    service, _, _ = make_service(tmp_path)

    created = await service.put("MY_NEW_SERVICE_KEY", "value")
    assert created.custom is True
    assert created.is_set is True

    with pytest.raises(APIError) as bad_key:
        await service.put("lower-case", "value")
    assert bad_key.value.code == "invalid_integration_key"

    with pytest.raises(APIError) as empty:
        await service.put("BRAVE_API_KEY", "   ")
    assert empty.value.code == "integration_value_required"

    with pytest.raises(APIError) as newline:
        await service.put("BRAVE_API_KEY", "a\nb")
    assert newline.value.code == "invalid_integration_value"

    with pytest.raises(APIError) as denylisted:
        await service.put("PATH", "evil")
    assert denylisted.value.status_code == 422
    assert "not an allowed key" in denylisted.value.message

    # Infrastructure, channel-owned, and the two Hermes-owned rows stay closed.
    for protected in ("API_SERVER_KEY", "TELEGRAM_BOT_TOKEN", SLACK_KEY, GOOGLE_KEY):
        with pytest.raises(APIError) as blocked:
            await service.put(protected, "value")
        assert blocked.value.status_code == 403
        assert blocked.value.code == "integration_not_managed"


@pytest.mark.asyncio
async def test_out_of_sync_profiles_surface_as_an_error_row(tmp_path):
    global_auth = FakeGlobalAuth()

    async def drifted(keys):
        return ({key: None for key in keys}, {key: ("bot-one", "bot-two") for key in keys})

    global_auth.env_mismatches = drifted  # type: ignore[method-assign]
    global_auth._env_mismatches_locked = drifted  # type: ignore[method-assign]
    service, _, _ = make_service(tmp_path, global_auth=global_auth)

    by_key = {item.key: item for item in await service.list()}

    github = by_key["GITHUB_TOKEN"]
    assert github.status == "error"
    assert github.sync_status == "out_of_sync"
    assert "2 bot profile(s)" in (github.detail or "")


# ── Slack: read-only ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_slack_row_reflects_main_config_and_never_writes(tmp_path):
    service, _, _ = make_service(tmp_path, slack_enabled=True)
    env_path = service.settings.hermes_home / ".env"
    env_path.write_text("SLACK_BOT_TOKEN=bot\nSLACK_APP_TOKEN=app\n", encoding="utf-8")

    row = next(item for item in await service.list() if item.key == SLACK_KEY)

    assert row.status == "connected"
    assert row.auth == "external"
    assert "managed by Hermes" in (row.detail or "")

    before = env_path.read_bytes()
    with pytest.raises(APIError) as blocked:
        await service.delete(SLACK_KEY)
    assert blocked.value.status_code == 403
    assert env_path.read_bytes() == before


@pytest.mark.asyncio
async def test_slack_incomplete_credentials_report_an_error(tmp_path):
    service, _, _ = make_service(tmp_path, slack_enabled=True)
    (service.settings.hermes_home / ".env").write_text("SLACK_BOT_TOKEN=bot\n", encoding="utf-8")

    row = next(item for item in await service.list() if item.key == SLACK_KEY)

    assert row.status == "error"
    assert "incomplete" in (row.detail or "")


# ── Google: OAuth token file ─────────────────────────────────────────────


def google_service(settings, runner, **kwargs) -> GoogleAuthService:
    return GoogleAuthService(settings, RecordingEvents(), runner=runner, **kwargs)


@pytest.mark.asyncio
async def test_google_connect_starts_flow_with_auth_url_and_code_entry(tmp_path):
    settings = credential_settings(tmp_path)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    runner = ScriptedRunner(
        {"--auth-url": CommandResult(0, stdout="Visit this URL:\nhttps://accounts.google.com/o/oauth2/auth?x=1\n")}
    )
    service = google_service(settings, runner)

    result = await service.connect(GoogleConnect())

    assert result.url == "https://accounts.google.com/o/oauth2/auth?x=1"
    assert result.code_entry is True
    assert result.needs_client_secret is False
    command = runner.calls[0]
    assert command[0] == "/usr/bin/env"
    assert command[1] == f"HERMES_HOME={settings.hermes_home}"
    assert command[-1] == "--auth-url"


@pytest.mark.asyncio
async def test_google_connect_without_client_secret_requests_it(tmp_path):
    settings = credential_settings(tmp_path)
    runner = ScriptedRunner({})
    service = google_service(settings, runner)

    result = await service.connect(GoogleConnect())

    assert result.needs_client_secret is True
    assert result.url is None
    assert runner.calls == []


@pytest.mark.asyncio
async def test_google_connect_exchanges_pasted_redirect_url(tmp_path):
    settings = credential_settings(tmp_path)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    token_path = settings.hermes_home / "google_token.json"

    def exchange():
        token_path.write_text(
            json.dumps({"token": "test-access", "expiry": "2999-01-01T00:00:00Z"}),
            encoding="utf-8",
        )
        return CommandResult(0, stdout="OK: Authenticated. Token saved.\n")

    runner = ScriptedRunner({"--auth-code": exchange})
    service = google_service(settings, runner)

    pasted = "http://localhost:1/?state=abc&code=4/secret-code"
    row = await service.connect(GoogleConnect(code=pasted))

    assert row.status == "connected"
    assert row.key == GOOGLE_KEY
    assert row.auth == "oauth"
    assert ("--auth-code", pasted) == runner.calls[0][-2:]
    assert service.events.published == [
        ("integration_updated", {"key": GOOGLE_KEY, "is_set": True})
    ]


@pytest.mark.asyncio
async def test_google_connect_surfaces_script_errors_without_echoing_code(tmp_path):
    settings = credential_settings(tmp_path)
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    runner = ScriptedRunner(
        {"--auth-code": CommandResult(1, stdout="ERROR: OAuth state mismatch. Run --auth-url again to start a fresh session.\n")}
    )
    service = google_service(settings, runner)

    with pytest.raises(APIError) as error:
        await service.connect(GoogleConnect(code="http://localhost:1/?code=4/secret"))

    assert error.value.status_code == 422
    assert error.value.code == "google_auth_failed"
    assert "state mismatch" in error.value.message.lower()
    assert "4/secret" not in error.value.message


@pytest.mark.asyncio
async def test_google_invalid_token_file_reports_error_without_leaking_it(tmp_path):
    settings = credential_settings(tmp_path)
    profile = settings.profiles_dir / "existing-bot"
    profile.mkdir()
    (profile / ".env").touch()
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    (settings.hermes_home / "google_token.json").write_text("not-json", encoding="utf-8")
    global_auth = GlobalAuth(
        settings,
        FakeBotDatabase(["existing-bot"]),  # type: ignore[arg-type]
        RecordingRunner(),  # type: ignore[arg-type]
        healthy,
    )
    service = google_service(settings, ScriptedRunner({}), global_auth=global_auth)

    row = await service.status()

    assert row.status == "error"
    assert "not-json" not in row.model_dump_json()


@pytest.mark.asyncio
async def test_google_client_secret_json_is_validated_and_stored_via_script(tmp_path):
    settings = credential_settings(tmp_path)
    runner = ScriptedRunner(
        {
            "--client-secret": CommandResult(0, stdout="OK: Client secret saved\n"),
            "--auth-url": CommandResult(0, stdout="https://accounts.google.com/o/oauth2/auth?x=2\n"),
        }
    )
    service = google_service(settings, runner)

    with pytest.raises(APIError) as invalid:
        await service.connect(GoogleConnect(client_secret_json="not json"))
    assert invalid.value.code == "invalid_client_secret"

    with pytest.raises(APIError) as wrong_shape:
        await service.connect(GoogleConnect(client_secret_json='{"other": 1}'))
    assert wrong_shape.value.code == "invalid_client_secret"

    payload = '{"installed": {"client_id": "id", "client_secret": "cs"}}'
    settings.google_client_secret_path.write_text("{}", encoding="utf-8")
    result = await service.connect(GoogleConnect(client_secret_json=payload))
    assert result.code_entry is True
    assert any("--client-secret" in call for call in runner.calls)
    leftovers = [p for p in settings.hermes_home.iterdir() if p.name.startswith(".google-client.botter-")]
    assert leftovers == []


@pytest.mark.asyncio
async def test_google_client_secret_is_global_before_authorization_url_returns(tmp_path):
    settings = credential_settings(tmp_path)
    profile = settings.profiles_dir / "existing-bot"
    profile.mkdir()
    (profile / ".env").touch()
    new_client = b'{"installed":{"client_id":"new-global-client"}}'

    def store_client():
        settings.google_client_secret_path.write_bytes(new_client)
        return CommandResult(0, stdout="OK: Client secret saved\n")

    runner = ScriptedRunner(
        {
            "--client-secret": store_client,
            "--auth-url": CommandResult(0, stdout="https://accounts.google.com/o/oauth2/auth?global=1\n"),
        }
    )
    global_auth = GlobalAuth(
        settings,
        FakeBotDatabase(["existing-bot"]),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        healthy,
    )
    service = google_service(settings, runner, global_auth=global_auth)

    authorization = await service.connect(
        GoogleConnect(client_secret_json=new_client.decode("utf-8"))
    )

    assert authorization.code_entry is True
    assert (profile / "google_client_secret.json").read_bytes() == new_client
    assert stat.S_IMODE(settings.google_client_secret_path.stat().st_mode) == 0o600
    assert stat.S_IMODE((profile / "google_client_secret.json").stat().st_mode) == 0o600


@pytest.mark.asyncio
async def test_google_client_secret_rejects_symlink_destination_before_script(tmp_path):
    settings = credential_settings(tmp_path)
    outside = tmp_path / "outside-client.json"
    outside.write_text("outside-sentinel", encoding="utf-8")
    settings.google_client_secret_path.symlink_to(outside)
    runner = ScriptedRunner({"--client-secret": CommandResult(0)})
    service = google_service(settings, runner)

    with pytest.raises(APIError) as error:
        await service.connect(GoogleConnect(client_secret_json='{"installed":{"client_id":"test"}}'))

    assert error.value.code == "unsafe_credential_path"
    assert outside.read_text(encoding="utf-8") == "outside-sentinel"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_google_client_secret_restores_main_when_profile_propagation_fails(tmp_path):
    settings = credential_settings(tmp_path)
    old_client = b'{"installed":{"client_id":"old-client"}}'
    new_client = b'{"installed":{"client_id":"new-client"}}'
    settings.google_client_secret_path.write_bytes(old_client)
    os.chmod(settings.google_client_secret_path, 0o644)
    outside_profiles = tmp_path / "outside-profiles"
    (outside_profiles / "existing-bot").mkdir(parents=True)
    settings.profiles_dir.rmdir()
    settings.profiles_dir.symlink_to(outside_profiles, target_is_directory=True)

    def store_client():
        settings.google_client_secret_path.write_bytes(new_client)
        return CommandResult(0, stdout="OK: Client secret saved\n")

    runner = ScriptedRunner({"--client-secret": store_client})
    global_auth = GlobalAuth(
        settings,
        FakeBotDatabase(["existing-bot"]),  # type: ignore[arg-type]
        runner,  # type: ignore[arg-type]
        healthy,
    )
    service = google_service(settings, runner, global_auth=global_auth)

    with pytest.raises(OSError, match="profiles root"):
        await service.connect(GoogleConnect(client_secret_json=new_client.decode("utf-8")))

    assert settings.google_client_secret_path.read_bytes() == old_client
    assert stat.S_IMODE(settings.google_client_secret_path.stat().st_mode) == 0o600
    assert not (outside_profiles / "existing-bot" / "google_client_secret.json").exists()


@pytest.mark.asyncio
async def test_google_disconnect_revokes_then_removes_token(tmp_path):
    settings = credential_settings(tmp_path)
    token_path = settings.hermes_home / "google_token.json"
    token_path.write_text(json.dumps({"expiry": "2999-01-01T00:00:00Z"}), encoding="utf-8")
    runner = ScriptedRunner({"--revoke": CommandResult(0, stdout="OK: revoked\n")})
    service = google_service(settings, runner)

    row = await service.disconnect()

    assert row.status == "not_connected"
    assert not token_path.exists()
    assert any("--revoke" in call for call in runner.calls)


@pytest.mark.asyncio
async def test_google_row_joins_the_one_list(tmp_path):
    settings = credential_settings(tmp_path)
    google = google_service(settings, ScriptedRunner({}))
    serve = FakeServe(catalog())
    service = CredentialService(
        settings,
        serve,  # type: ignore[arg-type]
        RecordingEvents(),  # type: ignore[arg-type]
        runner=RecordingRunner(),  # type: ignore[arg-type]
        health_check=healthy,
        google=google,
    )

    rows = await service.list()

    row = next(item for item in rows if item.key == GOOGLE_KEY)
    assert row.auth == "oauth"
    assert row.status == "not_connected"


# ── registry invariants ──────────────────────────────────────────────────


def test_curated_overlay_covers_the_v1_registry_and_protects_infrastructure():
    assert set(CURATED) == {
        "GITHUB_TOKEN",
        "VERCEL_TOKEN",
        "VERCEL_TEAM_ID",
        "SUPABASE_ACCESS_TOKEN",
        "SUPABASE_PROJECT_REF",
        "OPENROUTER_API_KEY",
        "EXA_API_KEY",
        "XAI_API_KEY",
    }
    # Multi-key cards keep exactly one required primary.
    for group in ("vercel", "supabase"):
        members = [item for item in CURATED.values() if item.group == group]
        assert len(members) == 2
        assert sum(1 for item in members if item.required) == 1
    assert "API_SERVER_KEY" in PROTECTED_KEYS
    assert {"SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"} <= PROTECTED_KEYS
