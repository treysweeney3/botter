"""Machine-global authentication shared by every Botter-managed profile."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import stat
import tempfile
from inspect import isawaitable
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import Settings, read_env_value
from .db import Database
from .errors import APIError
from .registry import CommandRunner, restart_gateway

if TYPE_CHECKING:
    from .hermes_serve import HermesServe


logger = logging.getLogger(__name__)
SLUG_PATTERN = re.compile(r"^[a-z0-9-]{1,32}$")
CONTAINER_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")
GOOGLE_TOKEN = "google_token.json"
GOOGLE_CLIENT_SECRET = "google_client_secret.json"
MCP_TOKEN_DIR = "mcp-tokens"
# `<name>.json` holds the grant; `.client.json` the dynamic client registration;
# `.meta.json` the discovered authorization-server metadata. All three travel
# together — a profile with the grant but no client info re-registers and fails.
MCP_TOKEN_SUFFIXES = (".json", ".client.json", ".meta.json")
MCP_STABLE_TOKEN_FIELDS = ("refresh_token", "token_type", "scope")
GOOGLE_STABLE_TOKEN_FIELDS = (
    "refresh_token",
    "token_uri",
    "client_id",
    "client_secret",
    "scopes",
    "quota_project_id",
    "universe_domain",
    "account",
)


@dataclass(frozen=True, slots=True)
class _FileSnapshot:
    contents: bytes | None
    mode: int | None


def _safe_regular_file(path: Path, *, allow_missing: bool = True) -> None:
    if not path.exists() and not path.is_symlink():
        if allow_missing:
            return
        raise OSError(f"Required authentication file is missing: {path.name}")
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"Authentication path is not a regular file: {path.name}")


def _snapshot(path: Path) -> _FileSnapshot:
    _safe_regular_file(path)
    if not path.exists():
        return _FileSnapshot(None, None)
    metadata = path.stat()
    return _FileSnapshot(path.read_bytes(), stat.S_IMODE(metadata.st_mode))


def _atomic_write(path: Path, contents: bytes, *, mode: int = 0o600) -> None:
    _safe_regular_file(path)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.botter-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _restore(path: Path, snapshot: _FileSnapshot) -> None:
    if snapshot.contents is None:
        if path.exists() or path.is_symlink():
            _safe_regular_file(path, allow_missing=False)
            path.unlink()
        return
    _atomic_write(path, snapshot.contents, mode=snapshot.mode or 0o600)


def _google_token_identity(contents: bytes) -> tuple[tuple[str, str], ...]:
    payload = json.loads(contents.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Google token must be an object")
    refresh_token = payload.get("refresh_token")
    if isinstance(refresh_token, str) and refresh_token.strip():
        identity: list[tuple[str, str]] = []
        for field in GOOGLE_STABLE_TOKEN_FIELDS:
            value = payload.get(field)
            if field == "scopes" and isinstance(value, list):
                value = sorted(str(item) for item in value)
            identity.append((field, json.dumps(value, sort_keys=True, separators=(",", ":"))))
        return tuple(identity)
    # Tokens without a refresh grant cannot legitimately rotate in-place.
    return (("token_document", contents.decode("utf-8")),)


class GlobalAuth:
    """Coordinates global auth mutations across main and Botter bot profiles."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        runner: CommandRunner,
        health_check: Callable[[], Awaitable[bool]],
        *,
        busy_check: Callable[[], bool | Awaitable[bool]] | None = None,
    ):
        self.settings = settings
        self.database = database
        self.runner = runner
        self.health_check = health_check
        self.busy_check = busy_check or (lambda: False)
        self.lock = asyncio.Lock()

    @property
    def google_pending_path(self) -> Path:
        return self.settings.state_dir / "google-auth-reconcile.pending"

    def dashboard_pending_path(self, key: str) -> Path:
        if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", key):
            raise ValueError("Invalid authentication key")
        return self.settings.state_dir / "auth-reconcile" / f"{key}.pending"

    async def registered_slugs(self) -> list[str]:
        rows = await self.database.list_bots(include_archived=True)
        slugs: list[str] = []
        for row in rows:
            slug = str(row.get("slug") or "")
            if not SLUG_PATTERN.fullmatch(slug):
                raise OSError("Bot registry contains an invalid profile slug")
            slugs.append(slug)
        return slugs

    def profile_home(self, slug: str) -> Path:
        if not SLUG_PATTERN.fullmatch(slug):
            raise OSError("Invalid Botter profile slug")
        profiles_root = self.settings.profiles_dir
        if profiles_root.is_symlink() or not profiles_root.is_dir():
            raise OSError("Hermes profiles root is unavailable")
        profile = profiles_root / slug
        if not profile.exists() or profile.is_symlink() or not profile.is_dir():
            raise OSError(f"Bot profile is unavailable: {slug}")
        if not profile.resolve(strict=True).is_relative_to(profiles_root.resolve(strict=True)):
            raise OSError("Bot profile escapes the Hermes profiles root")
        return profile

    async def env_mismatches(
        self, keys: list[str]
    ) -> tuple[dict[str, str | None], dict[str, tuple[str, ...]]]:
        async with self.lock:
            return await self._env_mismatches_locked(keys)

    async def _env_mismatches_locked(
        self, keys: list[str]
    ) -> tuple[dict[str, str | None], dict[str, tuple[str, ...]]]:
        main_path = self.settings.hermes_home / ".env"
        _safe_regular_file(main_path)
        main_values = {key: read_env_value(main_path, key) for key in keys}
        mismatches: dict[str, list[str]] = {key: [] for key in keys}
        for slug in await self.registered_slugs():
            try:
                env_path = self.profile_home(slug) / ".env"
                _safe_regular_file(env_path)
                values = {key: read_env_value(env_path, key) for key in keys}
            except (OSError, UnicodeError):
                for key in keys:
                    mismatches[key].append(slug)
                continue
            for key in keys:
                if values[key] != main_values[key]:
                    mismatches[key].append(slug)
        for key in keys:
            if self.dashboard_pending_path(key).exists():
                mismatches[key].append("pending")
        return main_values, {key: tuple(slugs) for key, slugs in mismatches.items()}

    async def mutate_dashboard_env(
        self,
        serve: HermesServe,
        key: str,
        value: str | None,
    ) -> bool:
        """Run Hermes' credential lifecycle for main and every Botter profile."""
        async with self.lock:
            # Profiles first; main is the canonical commit point.
            targets: list[str | None] = [*(await self.registered_slugs()), None]
            snapshots: dict[str | None, str | None] = {}
            for target in targets:
                env_path = (
                    self.settings.hermes_home / ".env"
                    if target is None
                    else self.profile_home(target) / ".env"
                )
                _safe_regular_file(env_path)
                snapshots[target] = read_env_value(env_path, key)

            pending_path = self.dashboard_pending_path(key)
            was_pending = pending_path.exists()
            changed_targets = [
                target for target in targets if was_pending or snapshots[target] != value
            ]
            if not changed_targets:
                return False
            pending_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(pending_path.parent, 0o700)
            _atomic_write(pending_path, b"pending\n", mode=0o600)
            attempted: list[str | None] = []
            try:
                for target in changed_targets:
                    attempted.append(target)
                    await self._dashboard_env_request(serve, target, key, value)
                for target in targets:
                    env_path = (
                        self.settings.hermes_home / ".env"
                        if target is None
                        else self.profile_home(target) / ".env"
                    )
                    if read_env_value(env_path, key) != value:
                        raise OSError("Hermes credential lifecycle did not persist the expected value")
            except BaseException as exc:
                rollback_failed = False
                for target in reversed(attempted):
                    try:
                        await self._dashboard_env_request(serve, target, key, snapshots[target])
                    except BaseException:
                        rollback_failed = True
                        logger.error("global auth dashboard rollback failed for key=%s", key)
                if isinstance(exc, asyncio.CancelledError):
                    raise
                message = "Authentication could not be synchronized to every bot. Retry the update."
                if rollback_failed:
                    message = "Authentication synchronization failed and needs repair before retrying."
                raise APIError(502, "global_auth_sync_failed", message) from exc
            pending_path.unlink(missing_ok=True)
            return True

    @staticmethod
    async def _dashboard_env_request(
        serve: HermesServe,
        profile: str | None,
        key: str,
        value: str | None,
    ) -> None:
        params = {"profile": profile} if profile is not None else None
        if value is None:
            try:
                await serve.request("DELETE", "/api/env", json={"key": key}, params=params)
            except APIError as exc:
                if exc.status_code != 404:
                    raise
        else:
            await serve.request(
                "PUT", "/api/env", json={"key": key, "value": value}, params=params
            )

    def config_path(self, slug: str | None) -> Path:
        """config.yaml for main (None) or one registered bot profile."""
        home = self.settings.hermes_home if slug is None else self.profile_home(slug)
        path = home / "config.yaml"
        _safe_regular_file(path, allow_missing=False)
        return path

    async def mutate_profile_configs(
        self, mutate: Callable[[Any], bool]
    ) -> bool:
        """Apply one edit to main's and every registered bot's config.yaml.

        `mutate` receives a comment-preserving document and returns True when it
        changed it. Every file is snapshotted first, so a failure part-way
        through leaves no profile holding a half-applied config. Comments and
        formatting survive — see `yaml_io`.
        """
        async with self.lock:
            from .yaml_io import YAMLError, load_yaml, write_yaml_atomic

            targets: list[str | None] = [*(await self.registered_slugs()), None]
            paths = [self.config_path(target) for target in targets]
            snapshots = {path: _snapshot(path) for path in paths}
            changed = False
            written: list[Path] = []
            try:
                for path in paths:
                    document = load_yaml(path)
                    if not isinstance(document, dict):
                        raise OSError(f"Hermes config is not a mapping: {path}")
                    if not mutate(document):
                        continue
                    if write_yaml_atomic(path, document):
                        written.append(path)
                        changed = True
            except (OSError, UnicodeError, ValueError, YAMLError) as exc:
                rollback_failed = False
                for path in written:
                    try:
                        _restore(path, snapshots[path])
                    except OSError:
                        rollback_failed = True
                        logger.error("profile config rollback failed for %s", path)
                message = "The change could not be applied to every bot. Retry it."
                if rollback_failed:
                    message = "The change failed part-way and needs repair before retrying."
                raise APIError(500, "global_config_sync_failed", message) from exc
            return changed

    async def assert_google_mutation_idle(self) -> None:
        busy = self.busy_check()
        if isawaitable(busy):
            busy = await busy
        if busy:
            raise APIError(
                409,
                "connection_busy",
                "Wait for active bot chats to finish before changing Google authentication.",
            )

    async def sync_google_locked(
        self,
        *,
        slugs: list[str] | None = None,
        require_token: bool = True,
    ) -> bool:
        """Copy canonical Google files into profiles and rebuild stale mounts.

        The caller must hold ``self.lock`` and await ``assert_google_mutation_idle``
        before an OAuth exchange or other main-token mutation.
        """
        token_source = self.settings.hermes_home / GOOGLE_TOKEN
        client_source = self.settings.hermes_home / GOOGLE_CLIENT_SECRET
        source_paths = [client_source]
        if token_source.exists() or token_source.is_symlink():
            source_paths.insert(0, token_source)
        elif require_token:
            _safe_regular_file(token_source, allow_missing=False)
        for source in source_paths:
            _safe_regular_file(source, allow_missing=False)
        target_slugs = slugs if slugs is not None else await self.registered_slugs()
        target_paths = [self.profile_home(slug) / source.name for slug in target_slugs for source in source_paths]
        snapshots = {path: _snapshot(path) for path in target_paths}
        source_contents = {source.name: source.read_bytes() for source in source_paths}

        was_pending = self.google_pending_path.exists()
        self._write_google_pending()
        changed = False
        try:
            for source in source_paths:
                if stat.S_IMODE(source.stat().st_mode) != 0o600:
                    os.chmod(source, 0o600)
                    changed = True
            for path in target_paths:
                desired = source_contents[path.name]
                current = snapshots[path]
                if current.contents != desired or current.mode != 0o600:
                    _atomic_write(path, desired, mode=0o600)
                    changed = True
        except OSError as exc:
            for path, snapshot in snapshots.items():
                try:
                    _restore(path, snapshot)
                except OSError:
                    logger.error("global Google auth rollback failed for %s", path.name)
            raise APIError(
                500,
                "global_auth_sync_failed",
                "Google authentication was saved but could not be synchronized to every bot. Retry Connect.",
            ) from exc

        if target_slugs and (changed or was_pending):
            try:
                await self._rebuild_google_sandboxes(target_slugs)
            except RuntimeError as exc:
                raise APIError(
                    502,
                    "sandbox_rebuild_failed",
                    "Google authentication was saved, but bot sandboxes could not be refreshed. Retry Connect.",
                ) from exc
        self.google_pending_path.unlink(missing_ok=True)
        return changed

    async def reconcile_new_profile_locked(self, slug: str) -> None:
        """Give a not-yet-registered clone the current global Google auth."""
        token = self.settings.hermes_home / GOOGLE_TOKEN
        client = self.settings.hermes_home / GOOGLE_CLIENT_SECRET
        if not token.exists() and not token.is_symlink():
            return
        for source in (token, client):
            _safe_regular_file(source, allow_missing=False)
            os.chmod(source, 0o600)
            destination = self.profile_home(slug) / source.name
            _atomic_write(destination, source.read_bytes(), mode=0o600)

    async def remove_google_tokens_locked(self) -> bool:
        """Remove global Google tokens and rebuild sandboxes before success."""
        slugs = await self.registered_slugs()
        paths = [self.settings.hermes_home / GOOGLE_TOKEN]
        paths.extend(self.profile_home(slug) / GOOGLE_TOKEN for slug in slugs)
        snapshots = {path: _snapshot(path) for path in paths}
        was_pending = self.google_pending_path.exists()
        if not any(snapshot.contents is not None for snapshot in snapshots.values()):
            if was_pending and slugs:
                try:
                    await self._rebuild_google_sandboxes(slugs)
                except RuntimeError as exc:
                    raise APIError(
                        502,
                        "sandbox_rebuild_failed",
                        "Google tokens were removed, but bot sandboxes could not be refreshed. Retry Disconnect.",
                    ) from exc
            self.google_pending_path.unlink(missing_ok=True)
            return False

        self._write_google_pending()
        try:
            for path, snapshot in snapshots.items():
                if snapshot.contents is not None:
                    path.unlink()
        except OSError as exc:
            for path, snapshot in snapshots.items():
                try:
                    _restore(path, snapshot)
                except OSError:
                    logger.error("global Google disconnect rollback failed for %s", path.name)
            raise APIError(
                500,
                "global_auth_sync_failed",
                "Google authentication could not be removed from every bot. Retry Disconnect.",
            ) from exc

        if slugs:
            try:
                await self._rebuild_google_sandboxes(slugs)
            except RuntimeError as exc:
                raise APIError(
                    502,
                    "sandbox_rebuild_failed",
                    "Google tokens were removed, but bot sandboxes could not be refreshed. Retry Disconnect.",
                ) from exc
        self.google_pending_path.unlink(missing_ok=True)
        return True

    # ── MCP OAuth grants ─────────────────────────────────────────────────
    #
    # Hermes keeps an MCP grant per profile under
    # `HERMES_HOME/mcp-tokens/<name>.*` (`tools/mcp_oauth.py:434`). The user
    # authorizes once against main, so the grant is copied outward from there —
    # the same shape as the Google token above, minus the sandbox rebuild:
    # the agent process reads these directly, so no container is holding a
    # stale mount.

    def mcp_token_paths(self, home: Path, name: str) -> list[Path]:
        directory = home / MCP_TOKEN_DIR
        return [directory / f"{name}{suffix}" for suffix in MCP_TOKEN_SUFFIXES]

    @staticmethod
    def _mcp_token_identity(contents: bytes) -> tuple[tuple[str, str], ...]:
        """Compare grants by the fields that survive a refresh.

        An access token rotates on every refresh, so a byte comparison would
        report constant drift. Mirrors `_google_token_identity`.
        """
        payload = json.loads(contents.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("MCP token must be an object")
        refresh_token = payload.get("refresh_token")
        if isinstance(refresh_token, str) and refresh_token.strip():
            return tuple(
                (field, json.dumps(payload.get(field), sort_keys=True, separators=(",", ":")))
                for field in MCP_STABLE_TOKEN_FIELDS
            )
        # Without a refresh grant the document cannot legitimately rotate.
        return (("token_document", contents.decode("utf-8")),)

    async def sync_mcp_tokens_locked(self, name: str) -> bool:
        """Copy main's MCP grant for `name` into every registered profile."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", name):
            raise ValueError("Invalid MCP server name")
        sources = [path for path in self.mcp_token_paths(self.settings.hermes_home, name) if path.exists()]
        if not sources:
            return False
        for source in sources:
            _safe_regular_file(source, allow_missing=False)
        slugs = await self.registered_slugs()
        if not slugs:
            return False

        contents = {source.name: source.read_bytes() for source in sources}
        targets: list[Path] = []
        for slug in slugs:
            directory = self.profile_home(slug) / MCP_TOKEN_DIR
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            targets.extend(directory / source.name for source in sources)
        snapshots = {path: _snapshot(path) for path in targets}

        changed = False
        try:
            for source in sources:
                if stat.S_IMODE(source.stat().st_mode) != 0o600:
                    os.chmod(source, 0o600)
            for path in targets:
                desired = contents[path.name]
                current = snapshots[path]
                if current.contents != desired or current.mode != 0o600:
                    _atomic_write(path, desired, mode=0o600)
                    changed = True
        except OSError as exc:
            for path, snapshot in snapshots.items():
                try:
                    _restore(path, snapshot)
                except OSError:
                    logger.error("MCP token rollback failed for %s", path)
            raise APIError(
                500,
                "global_auth_sync_failed",
                "The MCP authorization was saved but could not reach every bot. Retry it.",
            ) from exc
        return changed

    async def mcp_token_consistency(self, name: str) -> tuple[str, ...]:
        """Return registered slugs whose MCP grant differs from main's."""
        main_token = self.settings.hermes_home / MCP_TOKEN_DIR / f"{name}.json"
        _safe_regular_file(main_token)
        if not main_token.exists():
            # Nothing granted yet: a profile holding a grant is the drift.
            return tuple(
                slug
                for slug in await self.registered_slugs()
                if (self.profile_home(slug) / MCP_TOKEN_DIR / f"{name}.json").exists()
            )
        try:
            expected = self._mcp_token_identity(main_token.read_bytes())
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return ("main",)
        mismatches: list[str] = []
        for slug in await self.registered_slugs():
            path = self.profile_home(slug) / MCP_TOKEN_DIR / f"{name}.json"
            try:
                _safe_regular_file(path, allow_missing=False)
                if self._mcp_token_identity(path.read_bytes()) != expected:
                    raise OSError("MCP grant differs")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                mismatches.append(slug)
        return tuple(mismatches)

    async def remove_mcp_tokens_locked(self, name: str) -> bool:
        """Drop the grant for `name` from main and every registered profile."""
        homes = [self.settings.hermes_home]
        homes.extend(self.profile_home(slug) for slug in await self.registered_slugs())
        removed = False
        for home in homes:
            for path in self.mcp_token_paths(home, name):
                if path.exists() or path.is_symlink():
                    _safe_regular_file(path, allow_missing=False)
                    path.unlink()
                    removed = True
        return removed

    async def google_consistency(self) -> tuple[str, ...]:
        """Return registered slugs whose Google files differ from main."""
        async with self.lock:
            return await self._google_consistency_locked()

    async def _google_consistency_locked(self) -> tuple[str, ...]:
        if self.google_pending_path.exists():
            return ("pending",)
        token = self.settings.hermes_home / GOOGLE_TOKEN
        client = self.settings.hermes_home / GOOGLE_CLIENT_SECRET
        _safe_regular_file(token)
        _safe_regular_file(client)
        if not token.exists():
            stale: list[str] = []
            for slug in await self.registered_slugs():
                profile_token = self.profile_home(slug) / GOOGLE_TOKEN
                if profile_token.exists() or profile_token.is_symlink():
                    stale.append(slug)
            return tuple(stale)
        if not client.exists():
            return ("main",)
        if stat.S_IMODE(token.stat().st_mode) != 0o600 or stat.S_IMODE(client.stat().st_mode) != 0o600:
            return ("main",)
        try:
            expected_token_identity = _google_token_identity(token.read_bytes())
            expected_client = client.read_bytes()
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return ("main",)
        mismatches: list[str] = []
        for slug in await self.registered_slugs():
            try:
                home = self.profile_home(slug)
                profile_token = home / GOOGLE_TOKEN
                profile_client = home / GOOGLE_CLIENT_SECRET
                for path in (profile_token, profile_client):
                    _safe_regular_file(path, allow_missing=False)
                    if stat.S_IMODE(path.stat().st_mode) != 0o600:
                        raise OSError("Google credential mode differs")
                if _google_token_identity(profile_token.read_bytes()) != expected_token_identity:
                    raise OSError("Google authorization identity differs")
                if profile_client.read_bytes() != expected_client:
                    raise OSError("Google client credential differs")
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                mismatches.append(slug)
        return tuple(mismatches)

    def _write_google_pending(self) -> None:
        self.settings.prepare_state()
        _atomic_write(self.google_pending_path, b"pending\n", mode=0o600)

    async def _rebuild_google_sandboxes(self, slugs: list[str]) -> None:
        await self.assert_google_mutation_idle()
        await self._sweep_profile_containers(slugs)
        await restart_gateway(self.runner, self.health_check)
        await self.assert_google_mutation_idle()
        await self._sweep_profile_containers(slugs)

    async def _sweep_profile_containers(self, slugs: list[str]) -> None:
        for slug in slugs:
            listed = await self.runner.run(
                [
                    str(self.settings.docker_bin),
                    "ps",
                    "-a",
                    "--filter",
                    "label=hermes-agent=1",
                    "--filter",
                    f"label=hermes-profile={slug}",
                    "--format",
                    "{{.ID}}",
                ],
                timeout=30,
                check=False,
            )
            if listed.returncode != 0:
                raise RuntimeError("Unable to list bot sandbox containers")
            ids = [value.strip() for value in listed.stdout.splitlines() if value.strip()]
            if any(not CONTAINER_ID_PATTERN.fullmatch(container_id) for container_id in ids):
                raise RuntimeError("Docker returned an invalid sandbox container id")
            if ids:
                await self.runner.run(
                    [str(self.settings.docker_bin), "stop", "-t", "10", *ids],
                    timeout=30,
                    check=False,
                )
                removed = await self.runner.run(
                    [str(self.settings.docker_bin), "rm", "-f", *ids],
                    timeout=30,
                    check=False,
                )
                if removed.returncode != 0:
                    raise RuntimeError("Unable to remove stale bot sandbox containers")
            verified = await self.runner.run(
                [
                    str(self.settings.docker_bin),
                    "ps",
                    "-a",
                    "--filter",
                    "label=hermes-agent=1",
                    "--filter",
                    f"label=hermes-profile={slug}",
                    "--format",
                    "{{.ID}}",
                ],
                timeout=30,
                check=False,
            )
            if verified.returncode != 0 or verified.stdout.strip():
                raise RuntimeError("Stale bot sandbox containers remain")
