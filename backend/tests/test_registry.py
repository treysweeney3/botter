from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from botterd.config import Settings
from botterd.db import Database
from botterd.errors import APIError
from botterd.events import EventBus
from botterd.global_auth import GlobalAuth
from botterd.models import Bot, BotCreate, BotPatch
from botterd.registry import CommandResult, Registry, provision_profile_egress, validate_slug


class FakeRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.calls: list[tuple[str, ...]] = []

    async def run(self, args, *, timeout=120, check=True):
        call = tuple(str(item) for item in args)
        self.calls.append(call)
        if call[1:4] == ("profile", "create", "sales-bot"):
            profile = self.settings.profiles_dir / "sales-bot"
            profile.mkdir(parents=True)
            (profile / "config.yaml").write_text(
                yaml.safe_dump(
                    {
                        "terminal": {"docker_volumes": '["/private/company:/mnt/company"]'},
                        "platforms": {
                            "slack": {"enabled": True},
                            # A clone inherits main's listener config. Leaving it
                            # on makes the gateway skip the whole profile.
                            "api_server": {"enabled": True, "port": 8642},
                            "webhook": {"enabled": True},
                        },
                        # A clone inherits main's MCP servers. That is wanted —
                        # a new bot starts with the same tools as the rest.
                        "mcp_servers": {
                            "composio": {
                                "url": "https://connect.composio.dev/mcp",
                                "auth": "oauth",
                            }
                        },
                        "proxy": {"enabled": True},
                    }
                ),
                encoding="utf-8",
            )
        if call[1:] == ("-p", "default", "egress", "status"):
            return CommandResult(0, "Listening  yes\n")
        if call[:4] == ("docker", "ps", "-q", "--filter"):
            return CommandResult(0, "container-a\n")
        if call[:2] == ("docker", "inspect"):
            return CommandResult(0, f"{self.settings.profiles_dir}/sales-bot/cache ")
        return CommandResult(0)


class FakeHermes:
    default_model = "provider/model"

    def __init__(self, fail=False):
        self.fail = fail
        self.created = []

    async def create_session(self, slug, *, title=None, model=None):
        self.created.append((slug, title, model))
        if self.fail:
            raise RuntimeError("session failed")
        return {"id": "session-1"}

    async def health(self):
        return {"status": "ok"}


class CredentialCheckingHermes(FakeHermes):
    def __init__(self, settings: Settings, token: bytes, client: bytes):
        super().__init__()
        self.settings = settings
        self.token = token
        self.client = client

    async def create_session(self, slug, *, title=None, model=None):
        profile = self.settings.profiles_dir / slug
        assert (profile / "google_token.json").read_bytes() == self.token
        assert (profile / "google_client_secret.json").read_bytes() == self.client
        return await super().create_session(slug, title=title, model=model)


def settings_for(tmp_path: Path) -> Settings:
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles").mkdir(parents=True)
    proxy = hermes_home / "proxy"
    proxy.mkdir(mode=0o700)
    (proxy / "proxy.yaml").write_text("proxy:\n  tunnel_listen: 127.0.0.1:9090\n", encoding="utf-8")
    (proxy / "ca.crt").write_text("test certificate", encoding="utf-8")
    (proxy / "mappings.json").write_text(
        '{"version":1,"tokens":[{"proxy_token":"test-only","env_name":"TEST_API_KEY",'
        '"upstream_hosts":["example.test"]}]}',
        encoding="utf-8",
    )
    (proxy / "iron-proxy.pid").write_text("12345\n", encoding="utf-8")
    wrapper = tmp_path / "bin"
    wrapper.mkdir()
    return Settings(
        state_dir=tmp_path / "state",
        hermes_home=hermes_home,
        hermes_bin=wrapper / "hermes",
        token_override="token",
        api_server_key_override="key",
    )


def request() -> BotCreate:
    return BotCreate(
        slug="sales-bot",
        display_name="Sales Bot",
        title="Sales Outbound",
        description="Build qualified pipeline.",
        avatar_color="#2EC7A6",
        avatar_glyph="paperplane",
        approval_boundary="Ask before sending messages.",
    )


@pytest.mark.asyncio
async def test_registry_create_applies_clone_hygiene_and_explicit_model(tmp_path):
    settings = settings_for(tmp_path)
    db = Database(settings.db_path)
    await db.connect()
    runner = FakeRunner(settings)
    hermes = FakeHermes()
    registry = Registry(settings, db, hermes, EventBus(), runner=runner, health_check=lambda: _true())
    try:
        bot = await registry.create(request())
        assert bot.default_session_id == "session-1"
        assert hermes.created == [("sales-bot", "Sales Bot main", "provider/model")]
        config = yaml.safe_load((settings.profiles_dir / "sales-bot/config.yaml").read_text())
        assert config["platforms"]["slack"]["enabled"] is False
        # Main owns the single shared listener; a bot that keeps these is
        # skipped by the gateway and never answers.
        assert config["platforms"]["api_server"]["enabled"] is False
        assert config["platforms"]["webhook"]["enabled"] is False
        # Non-enabled keys survive so Hermes keeps its own defaults.
        assert config["platforms"]["api_server"]["port"] == 8642
        # A new bot inherits main's MCP tools; provisioning must not strip them.
        assert config["mcp_servers"]["composio"]["url"] == "https://connect.composio.dev/mcp"
        assert config["mcp_servers"]["composio"]["auth"] == "oauth"
        assert config["terminal"]["docker_volumes"] == []
        assert config["proxy"]["enabled"] is True
        assert config["proxy"]["enforce_on_docker"] is True
        profile_proxy = settings.profiles_dir / "sales-bot" / "proxy"
        assert not profile_proxy.is_symlink()
        for name in ("proxy.yaml", "ca.crt", "mappings.json", "iron-proxy.pid"):
            assert (profile_proxy / name).is_symlink()
            assert (profile_proxy / name).resolve() == (settings.hermes_home / "proxy" / name).resolve()
        soul = (settings.profiles_dir / "sales-bot/SOUL.md").read_text()
        assert "# Sales Bot — Sales Outbound" in soul
        assert "Ask before sending messages." in soul
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_registry_gives_new_profile_global_google_auth_before_first_session(tmp_path):
    settings = settings_for(tmp_path)
    token = b'{"token":"global-test-token"}'
    client = b'{"installed":{"client_id":"global-test-client"}}'
    (settings.hermes_home / "google_token.json").write_bytes(token)
    (settings.hermes_home / "google_client_secret.json").write_bytes(client)
    db = Database(settings.db_path)
    await db.connect()
    runner = FakeRunner(settings)
    hermes = CredentialCheckingHermes(settings, token, client)
    global_auth = GlobalAuth(settings, db, runner, _true)
    registry = Registry(
        settings,
        db,
        hermes,
        EventBus(),
        runner=runner,
        health_check=_true,
        global_auth=global_auth,
    )
    try:
        bot = await registry.create(request())
        assert bot.slug == "sales-bot"
        assert hermes.created == [("sales-bot", "Sales Bot main", "provider/model")]
    finally:
        await db.close()


async def _true():
    return True


@pytest.mark.asyncio
async def test_registry_partial_create_rolls_back_through_full_purge(tmp_path):
    settings = settings_for(tmp_path)
    db = Database(settings.db_path)
    await db.connect()
    runner = FakeRunner(settings)
    registry = Registry(settings, db, FakeHermes(fail=True), EventBus(), runner=runner, health_check=_true)
    try:
        with pytest.raises(Exception):
            await registry.create(request())
        calls = runner.calls
        assert any(call[:3] == (str(settings.hermes_bin), "profile", "delete") for call in calls)
        assert any(call[:2] == ("docker", "stop") for call in calls)
        assert any(call[:3] == ("launchctl", "kickstart", "-k") for call in calls)
        assert any(call[:3] == ("rm", "-f", str(settings.wrapper_dir / "sales-bot")) for call in calls)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_registry_patch_syncs_description_to_hermes_profile(tmp_path):
    settings = settings_for(tmp_path)
    db = Database(settings.db_path)
    await db.connect()
    runner = FakeRunner(settings)
    registry = Registry(settings, db, FakeHermes(), EventBus(), runner=runner, health_check=_true)
    try:
        bot = await registry.create(request())
        runner.calls.clear()

        await registry.patch(bot.id, BotPatch(title="Head of Pipeline"))
        assert not [call for call in runner.calls if call[1:3] == ("profile", "describe")]

        updated = await registry.patch(bot.id, BotPatch(description="Book qualified demos."))
        assert updated.description == "Book qualified demos."
        assert runner.calls[-1] == (
            str(settings.hermes_bin),
            "profile",
            "describe",
            "sales-bot",
            "--text",
            "Book qualified demos.",
        )
        assert "Book qualified demos." in (settings.profiles_dir / "sales-bot/SOUL.md").read_text()

        # An unchanged description is not re-pushed.
        runner.calls.clear()
        await registry.patch(bot.id, BotPatch(description="Book qualified demos."))
        assert not [call for call in runner.calls if call[1:3] == ("profile", "describe")]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_registry_patch_leaves_state_untouched_when_describe_fails(tmp_path):
    settings = settings_for(tmp_path)
    db = Database(settings.db_path)
    await db.connect()

    class DescribeFailsRunner(FakeRunner):
        async def run(self, args, *, timeout=120, check=True):
            call = tuple(str(item) for item in args)
            if call[1:3] == ("profile", "describe"):
                raise RuntimeError("hermes exploded")
            return await super().run(args, timeout=timeout, check=check)

    runner = DescribeFailsRunner(settings)
    registry = Registry(settings, db, FakeHermes(), EventBus(), runner=runner, health_check=_true)
    try:
        bot = await registry.create(request())
        soul_before = (settings.profiles_dir / "sales-bot/SOUL.md").read_text()

        with pytest.raises(APIError) as caught:
            await registry.patch(bot.id, BotPatch(description="Book qualified demos."))
        assert caught.value.status_code == 502

        assert (settings.profiles_dir / "sales-bot/SOUL.md").read_text() == soul_before
        assert (await registry.get(bot.id)).description == request().description
    finally:
        await db.close()


@pytest.mark.parametrize("description", ["", "   ", "\n\t "])
def test_bot_writes_reject_a_blank_description(description):
    with pytest.raises(ValidationError):
        BotCreate(**(request().model_dump() | {"description": description}))
    with pytest.raises(ValidationError):
        BotPatch(description=description)


def test_bot_reads_tolerate_legacy_blank_descriptions():
    """Rows written before the rule still load — only writes are rejected."""
    legacy = Bot(
        **request().model_dump(exclude={"model"}) | {"description": ""},
        id="bot-1",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert legacy.description == ""


@pytest.mark.parametrize("slug", ["main", "default", "Upper", "under_score", "a" * 33])
def test_registry_rejects_reserved_or_invalid_slugs(slug):
    with pytest.raises(Exception):
        validate_slug(slug)


@pytest.mark.asyncio
async def test_provision_profile_egress_is_idempotent_and_fails_closed(tmp_path):
    settings = settings_for(tmp_path)
    profile = settings.profiles_dir / "sales-bot"
    profile.mkdir()
    runner = FakeRunner(settings)

    await provision_profile_egress(
        "sales-bot",
        runner,
        hermes_home=settings.hermes_home,
        hermes_bin=settings.hermes_bin,
    )
    await provision_profile_egress(
        "sales-bot",
        runner,
        hermes_home=settings.hermes_home,
        hermes_bin=settings.hermes_bin,
    )
    assert len([call for call in runner.calls if call[1:] == ("-p", "default", "egress", "status")]) == 2

    class StoppedRunner(FakeRunner):
        async def run(self, args, *, timeout=120, check=True):
            return CommandResult(0, "Listening  no\n")

    other_profile = settings.profiles_dir / "other-bot"
    other_profile.mkdir()
    with pytest.raises(RuntimeError, match="refusing direct bot egress"):
        await provision_profile_egress(
            "other-bot",
            StoppedRunner(settings),
            hermes_home=settings.hermes_home,
            hermes_bin=settings.hermes_bin,
        )
    assert not (other_profile / "proxy").exists()
