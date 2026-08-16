from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

import botterd.global_auth as global_auth_module
from botterd.config import Settings, read_env_value
from botterd.errors import APIError
from botterd.global_auth import GOOGLE_CLIENT_SECRET, GOOGLE_TOKEN, GlobalAuth
from botterd.registry import CommandResult


def write_env_value(path: Path, key: str, value: str | None) -> None:
    """Stand in for Hermes' own .env write inside `FileBackedServe`.

    botterd no longer edits .env itself — Hermes' credential lifecycle owns
    every write. The double stays deliberately naive so it never shares an
    implementation with the code under test.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    kept = [line for line in lines if line.split("=", 1)[0].strip() != key]
    if value is not None:
        kept.append(f"{key}={value}")
    path.write_text("".join(f"{line}\n" for line in kept), encoding="utf-8")


class FakeDatabase:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.include_archived_calls: list[bool] = []

    async def list_bots(self, *, include_archived: bool = True) -> list[dict]:
        self.include_archived_calls.append(include_archived)
        if include_archived:
            return list(self.rows)
        return [row for row in self.rows if not row.get("archived")]


class RecordingRunner:
    def __init__(self):
        self.calls: list[tuple[str, ...]] = []

    async def run(self, args, *, timeout=120, check=True):
        call = tuple(str(item) for item in args)
        self.calls.append(call)
        return CommandResult(0)


class RecreatedContainerRunner(RecordingRunner):
    """Expose one stale container in each pre/post-restart sweep."""

    def __init__(self):
        super().__init__()
        self.list_count = 0

    async def run(self, args, *, timeout=120, check=True):
        call = tuple(str(item) for item in args)
        self.calls.append(call)
        if call[:3] == ("docker", "ps", "-a"):
            outputs = ("aaaaaaaaaaaa\n", "", "bbbbbbbbbbbb\n", "")
            output = outputs[self.list_count]
            self.list_count += 1
            return CommandResult(0, stdout=output)
        return CommandResult(0)


class FileBackedServe:
    def __init__(self, settings: Settings, *, fail_profile: str = "__never__"):
        self.settings = settings
        self.fail_profile = fail_profile
        self.requests: list[tuple[str, str | None, str]] = []

    async def request(self, method, path, *, json=None, params=None, retry=True):
        assert path == "/api/env"
        profile = params.get("profile") if params else None
        key = json["key"]
        self.requests.append((method, profile, key))
        if profile == self.fail_profile:
            raise APIError(502, "hermes_dashboard_error", "simulated lifecycle failure")
        env_path = (
            self.settings.hermes_home / ".env"
            if profile is None
            else self.settings.profiles_dir / profile / ".env"
        )
        if method == "PUT":
            write_env_value(env_path, key, json["value"])
            return {"ok": True}
        if method == "DELETE":
            found = read_env_value(env_path, key) is not None
            write_env_value(env_path, key, None)
            if not found:
                raise APIError(404, "channel_not_found", "not found")
            return {"found": True}
        raise AssertionError(method)


class CancellingServe(FileBackedServe):
    async def request(self, method, path, *, json=None, params=None, retry=True):
        result = await super().request(
            method,
            path,
            json=json,
            params=params,
            retry=retry,
        )
        if params == {"profile": "active"} and json.get("value") == "cancelled-new-value":
            raise asyncio.CancelledError
        return result


def settings_for(tmp_path: Path) -> Settings:
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles").mkdir(parents=True)
    return Settings(
        state_dir=tmp_path / "state",
        hermes_home=hermes_home,
        hermes_bin=tmp_path / "bin/hermes",
        token_override="token",
        api_server_key_override="api-key",
    )


def add_profile(settings: Settings, slug: str, env: bytes = b"") -> Path:
    profile = settings.profiles_dir / slug
    profile.mkdir()
    (profile / ".env").write_bytes(env)
    return profile


def regular_mode(path: Path) -> int:
    metadata = path.lstat()
    assert stat.S_ISREG(metadata.st_mode)
    assert not stat.S_ISLNK(metadata.st_mode)
    return stat.S_IMODE(metadata.st_mode)


async def healthy() -> bool:
    return True


def auth_for(
    settings: Settings,
    database: FakeDatabase,
    runner: RecordingRunner | None = None,
    *,
    busy_check=None,
) -> GlobalAuth:
    return GlobalAuth(
        settings,
        database,  # type: ignore[arg-type]
        runner or RecordingRunner(),
        healthy,
        busy_check=busy_check,
    )


@pytest.mark.asyncio
async def test_sync_includes_registered_archived_profiles_but_not_orphans(tmp_path):
    settings = settings_for(tmp_path)
    main_env = settings.hermes_home / ".env"
    main_env.write_bytes(b"GLOBAL_KEY=main\nMAIN_ONLY=keep\n")
    active = add_profile(settings, "active", b"GLOBAL_KEY=main\nLOCAL=active\n")
    archived = add_profile(settings, "archived", b"GLOBAL_KEY=stale\nLOCAL=archived\n")
    orphan = add_profile(settings, "orphan", b"GLOBAL_KEY=orphan\nLOCAL=orphan\n")
    database = FakeDatabase(
        [
            {"slug": "active", "archived": 0},
            {"slug": "archived", "archived": 1},
        ]
    )
    auth = auth_for(settings, database)

    main_values, mismatches = await auth.env_mismatches(["GLOBAL_KEY"])
    assert main_values == {"GLOBAL_KEY": "main"}
    assert mismatches["GLOBAL_KEY"] == ("archived",)

    changed = await auth.mutate_dashboard_env(
        FileBackedServe(settings), "GLOBAL_KEY", "new-global-secret"
    )

    assert changed is True
    assert read_env_value(main_env, "GLOBAL_KEY") == "new-global-secret"
    assert read_env_value(active / ".env", "GLOBAL_KEY") == "new-global-secret"
    assert read_env_value(archived / ".env", "GLOBAL_KEY") == "new-global-secret"
    # An unregistered profile directory is never touched.
    assert read_env_value(orphan / ".env", "GLOBAL_KEY") == "orphan"
    assert read_env_value(active / ".env", "LOCAL") == "active"
    assert read_env_value(archived / ".env", "LOCAL") == "archived"
    assert database.include_archived_calls and all(database.include_archived_calls)


@pytest.mark.asyncio
async def test_global_auth_rejects_symlinked_profiles_root(tmp_path):
    settings = settings_for(tmp_path)
    outside = tmp_path / "outside-profiles"
    (outside / "active").mkdir(parents=True)
    settings.profiles_dir.rmdir()
    settings.profiles_dir.symlink_to(outside, target_is_directory=True)
    auth = auth_for(settings, FakeDatabase([{"slug": "active"}]))

    _, mismatches = await auth.env_mismatches(["GLOBAL_KEY"])
    assert mismatches["GLOBAL_KEY"] == ("active",)
    with pytest.raises(OSError, match="profiles root"):
        await auth.mutate_dashboard_env(FileBackedServe(settings), "GLOBAL_KEY", "new-value")


@pytest.mark.asyncio
async def test_dashboard_lifecycle_commits_profiles_then_main_and_rolls_back_failure(tmp_path):
    settings = settings_for(tmp_path)
    main_env = settings.hermes_home / ".env"
    main_env.write_text("SERVICE_KEY=main-old\n", encoding="utf-8")
    active = add_profile(settings, "active", b"SERVICE_KEY=active-old\n")
    archived = add_profile(settings, "archived", b"SERVICE_KEY=archived-old\n")
    auth = auth_for(settings, FakeDatabase([{"slug": "active"}, {"slug": "archived"}]))
    serve = FileBackedServe(settings)

    assert await auth.mutate_dashboard_env(serve, "SERVICE_KEY", "global-new") is True
    assert serve.requests == [
        ("PUT", "active", "SERVICE_KEY"),
        ("PUT", "archived", "SERVICE_KEY"),
        ("PUT", None, "SERVICE_KEY"),
    ]
    assert read_env_value(main_env, "SERVICE_KEY") == "global-new"
    assert read_env_value(active / ".env", "SERVICE_KEY") == "global-new"
    assert read_env_value(archived / ".env", "SERVICE_KEY") == "global-new"
    assert not auth.dashboard_pending_path("SERVICE_KEY").exists()

    failing = FileBackedServe(settings, fail_profile="archived")
    secret = "second-secret-must-not-leak"
    with pytest.raises(APIError) as error:
        await auth.mutate_dashboard_env(failing, "SERVICE_KEY", secret)
    assert error.value.code == "global_auth_sync_failed"
    assert secret not in str(error.value)
    assert read_env_value(main_env, "SERVICE_KEY") == "global-new"
    assert read_env_value(active / ".env", "SERVICE_KEY") == "global-new"
    assert read_env_value(archived / ".env", "SERVICE_KEY") == "global-new"
    assert auth.dashboard_pending_path("SERVICE_KEY").exists()

    repaired = FileBackedServe(settings)
    assert await auth.mutate_dashboard_env(repaired, "SERVICE_KEY", secret) is True
    assert repaired.requests == [
        ("PUT", "active", "SERVICE_KEY"),
        ("PUT", "archived", "SERVICE_KEY"),
        ("PUT", None, "SERVICE_KEY"),
    ]
    assert not auth.dashboard_pending_path("SERVICE_KEY").exists()


@pytest.mark.asyncio
async def test_dashboard_lifecycle_rolls_back_an_interrupted_target(tmp_path):
    settings = settings_for(tmp_path)
    main_env = settings.hermes_home / ".env"
    main_env.write_text("SERVICE_KEY=main-old\n", encoding="utf-8")
    active = add_profile(settings, "active", b"SERVICE_KEY=active-old\n")
    auth = auth_for(settings, FakeDatabase([{"slug": "active"}]))

    with pytest.raises(asyncio.CancelledError):
        await auth.mutate_dashboard_env(
            CancellingServe(settings),
            "SERVICE_KEY",
            "cancelled-new-value",
        )

    assert read_env_value(main_env, "SERVICE_KEY") == "main-old"
    assert read_env_value(active / ".env", "SERVICE_KEY") == "active-old"
    assert auth.dashboard_pending_path("SERVICE_KEY").exists()


@pytest.mark.asyncio
async def test_google_sync_copies_token_and_client_as_0600_regular_files(tmp_path):
    settings = settings_for(tmp_path)
    active = add_profile(settings, "active")
    archived = add_profile(settings, "archived")
    orphan = add_profile(settings, "orphan")
    token = settings.hermes_home / GOOGLE_TOKEN
    client = settings.hermes_home / GOOGLE_CLIENT_SECRET
    token.write_bytes(b'{"token":"global-google-token"}')
    client.write_bytes(b'{"installed":{"client_id":"global-client"}}')
    os.chmod(token, 0o644)
    os.chmod(client, 0o640)
    (active / GOOGLE_TOKEN).write_bytes(b"stale-token")
    os.chmod(active / GOOGLE_TOKEN, 0o644)
    database = FakeDatabase([{"slug": "active"}, {"slug": "archived", "archived": 1}])
    auth = auth_for(settings, database)

    async with auth.lock:
        changed = await auth.sync_google_locked()

    assert changed is True
    for profile in (active, archived):
        assert (profile / GOOGLE_TOKEN).read_bytes() == token.read_bytes()
        assert (profile / GOOGLE_CLIENT_SECRET).read_bytes() == client.read_bytes()
        assert regular_mode(profile / GOOGLE_TOKEN) == 0o600
        assert regular_mode(profile / GOOGLE_CLIENT_SECRET) == 0o600
    assert regular_mode(token) == 0o600
    assert regular_mode(client) == 0o600
    assert not (orphan / GOOGLE_TOKEN).exists()
    assert not (orphan / GOOGLE_CLIENT_SECRET).exists()
    assert not auth.google_pending_path.exists()


@pytest.mark.asyncio
async def test_google_consistency_allows_independent_access_token_refresh(tmp_path):
    settings = settings_for(tmp_path)
    active = add_profile(settings, "active")
    main_token = {
        "token": "main-access",
        "refresh_token": "shared-refresh",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "client-id",
        "client_secret": "client-secret",
        "scopes": ["scope-b", "scope-a"],
        "expiry": "2026-08-14T12:00:00Z",
    }
    profile_token = dict(main_token)
    profile_token.update(token="profile-refreshed-access", expiry="2026-08-14T13:00:00Z")
    profile_token["scopes"] = ["scope-a", "scope-b"]
    client = b'{"installed":{"client_id":"client-id"}}'
    (settings.hermes_home / GOOGLE_TOKEN).write_text(json.dumps(main_token), encoding="utf-8")
    (settings.hermes_home / GOOGLE_CLIENT_SECRET).write_bytes(client)
    (active / GOOGLE_TOKEN).write_text(json.dumps(profile_token), encoding="utf-8")
    (active / GOOGLE_CLIENT_SECRET).write_bytes(client)
    for home in (settings.hermes_home, active):
        os.chmod(home / GOOGLE_TOKEN, 0o600)
        os.chmod(home / GOOGLE_CLIENT_SECRET, 0o600)
    auth = auth_for(settings, FakeDatabase([{"slug": "active"}]))

    assert await auth.google_consistency() == ()


@pytest.mark.asyncio
async def test_google_sync_rolls_back_partial_profile_copy_and_redacts_token(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    active = add_profile(settings, "active")
    archived = add_profile(settings, "archived")
    token = settings.hermes_home / GOOGLE_TOKEN
    client = settings.hermes_home / GOOGLE_CLIENT_SECRET
    secret = "global-google-token-must-stay-redacted"
    token.write_text(secret, encoding="utf-8")
    client.write_bytes(b"global-client")
    for profile, prefix in ((active, b"active"), (archived, b"archived")):
        (profile / GOOGLE_TOKEN).write_bytes(prefix + b"-token")
        (profile / GOOGLE_CLIENT_SECRET).write_bytes(prefix + b"-client")
    destinations = [
        profile / name
        for profile in (active, archived)
        for name in (GOOGLE_TOKEN, GOOGLE_CLIENT_SECRET)
    ]
    originals = {path: path.read_bytes() for path in destinations}
    real_write = global_auth_module._atomic_write
    failed = False

    def fail_once(path: Path, contents: bytes, *, mode: int = 0o600):
        nonlocal failed
        if path == archived / GOOGLE_TOKEN and not failed:
            failed = True
            raise OSError("simulated Google copy failure")
        return real_write(path, contents, mode=mode)

    monkeypatch.setattr(global_auth_module, "_atomic_write", fail_once)
    auth = auth_for(settings, FakeDatabase([{"slug": "active"}, {"slug": "archived"}]))

    with pytest.raises(APIError) as error:
        async with auth.lock:
            await auth.sync_google_locked()

    assert error.value.code == "global_auth_sync_failed"
    assert secret not in str(error.value)
    assert {path: path.read_bytes() for path in destinations} == originals
    assert auth.google_pending_path.exists()


@pytest.mark.asyncio
async def test_google_sync_sweeps_exact_profile_labels_around_gateway_restart(tmp_path):
    settings = settings_for(tmp_path)
    add_profile(settings, "active")
    (settings.hermes_home / GOOGLE_TOKEN).write_bytes(b"token")
    (settings.hermes_home / GOOGLE_CLIENT_SECRET).write_bytes(b"client")
    runner = RecreatedContainerRunner()
    auth = auth_for(settings, FakeDatabase([{"slug": "active"}]), runner)

    async with auth.lock:
        await auth.sync_google_locked()

    listing = (
        "docker",
        "ps",
        "-a",
        "--filter",
        "label=hermes-agent=1",
        "--filter",
        "label=hermes-profile=active",
        "--format",
        "{{.ID}}",
    )
    assert runner.calls == [
        listing,
        ("docker", "stop", "-t", "10", "aaaaaaaaaaaa"),
        ("docker", "rm", "-f", "aaaaaaaaaaaa"),
        listing,
        ("launchctl", "kickstart", "-k", f"gui/{os.getuid()}/ai.hermes.gateway"),
        listing,
        ("docker", "stop", "-t", "10", "bbbbbbbbbbbb"),
        ("docker", "rm", "-f", "bbbbbbbbbbbb"),
        listing,
    ]


@pytest.mark.asyncio
async def test_google_disconnect_removes_registered_tokens_but_retains_clients(tmp_path):
    settings = settings_for(tmp_path)
    active = add_profile(settings, "active")
    archived = add_profile(settings, "archived")
    orphan = add_profile(settings, "orphan")
    homes = (settings.hermes_home, active, archived, orphan)
    for index, home in enumerate(homes):
        (home / GOOGLE_TOKEN).write_bytes(f"token-{index}".encode())
        (home / GOOGLE_CLIENT_SECRET).write_bytes(f"client-{index}".encode())
    expected_clients = {home: (home / GOOGLE_CLIENT_SECRET).read_bytes() for home in homes}
    auth = auth_for(
        settings,
        FakeDatabase([{"slug": "active"}, {"slug": "archived", "archived": 1}]),
    )

    async with auth.lock:
        changed = await auth.remove_google_tokens_locked()

    assert changed is True
    for home in (settings.hermes_home, active, archived):
        assert not (home / GOOGLE_TOKEN).exists()
        assert (home / GOOGLE_CLIENT_SECRET).read_bytes() == expected_clients[home]
    assert (orphan / GOOGLE_TOKEN).read_bytes() == b"token-3"
    assert (orphan / GOOGLE_CLIENT_SECRET).read_bytes() == expected_clients[orphan]
    assert not auth.google_pending_path.exists()


@pytest.mark.asyncio
async def test_google_disconnect_retry_completes_pending_sandbox_rebuild(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    active = add_profile(settings, "active")
    (settings.hermes_home / GOOGLE_TOKEN).write_bytes(b"main-token")
    (active / GOOGLE_TOKEN).write_bytes(b"profile-token")
    auth = auth_for(settings, FakeDatabase([{"slug": "active"}]))
    rebuilds: list[tuple[str, ...]] = []

    async def fail_rebuild(slugs):
        rebuilds.append(tuple(slugs))
        raise RuntimeError("simulated rebuild failure")

    monkeypatch.setattr(auth, "_rebuild_google_sandboxes", fail_rebuild)
    with pytest.raises(APIError) as first_error:
        async with auth.lock:
            await auth.remove_google_tokens_locked()
    assert first_error.value.code == "sandbox_rebuild_failed"
    assert auth.google_pending_path.exists()
    assert not (settings.hermes_home / GOOGLE_TOKEN).exists()
    assert not (active / GOOGLE_TOKEN).exists()

    async def successful_rebuild(slugs):
        rebuilds.append(tuple(slugs))

    monkeypatch.setattr(auth, "_rebuild_google_sandboxes", successful_rebuild)
    async with auth.lock:
        changed = await auth.remove_google_tokens_locked()

    assert changed is False
    assert rebuilds == [("active",), ("active",)]
    assert not auth.google_pending_path.exists()


@pytest.mark.asyncio
async def test_google_mutation_busy_check_raises_409_without_exposing_credentials(tmp_path):
    settings = settings_for(tmp_path)
    secret = "busy-google-secret"
    (settings.hermes_home / GOOGLE_TOKEN).write_text(secret, encoding="utf-8")
    auth = auth_for(settings, FakeDatabase([]), busy_check=lambda: True)

    with pytest.raises(APIError) as error:
        await auth.assert_google_mutation_idle()

    assert error.value.status_code == 409
    assert error.value.code == "connection_busy"
    assert secret not in str(error.value)
