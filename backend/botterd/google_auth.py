"""Google Workspace OAuth for every Botter-managed profile.

Google is not an env credential, so it cannot ride the generic credential
catalog in `credentials.py`. Hermes' google-workspace skill owns the flow: this
module drives that skill's `setup.py` (`--auth-url`, `--auth-code`,
`--client-secret`, `--revoke`) and then fans the resulting token files out to
every bot profile through `GlobalAuth`.

Behaviour here is unchanged from the `/v1/connections` implementation it
replaces. Only the module boundary moved.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings
from .errors import APIError
from .events import EventBus
from .global_auth import GlobalAuth
from .models import Authorization, GoogleConnect, Integration
from .registry import CommandRunner, SubprocessRunner


GOOGLE_KEY = "GOOGLE_WORKSPACE"
GOOGLE_CODE_INSTRUCTIONS = (
    "Sign in with Google and approve access. Your browser will end on an unreachable "
    "localhost page — copy that page's full URL from the address bar and paste it back here."
)
GOOGLE_CLIENT_SECRET_INSTRUCTIONS = (
    "Google needs an OAuth Desktop client first. In Google Cloud Console create OAuth "
    "client credentials (Desktop app), download the client JSON, and paste its contents here."
)


@dataclass(frozen=True, slots=True)
class _CredentialFileSnapshot:
    contents: bytes | None
    mode: int | None


def _snapshot_credential_file(path: Path) -> _CredentialFileSnapshot:
    if not path.exists() and not path.is_symlink():
        return _CredentialFileSnapshot(None, None)
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise APIError(
            500,
            "unsafe_credential_path",
            "Google client credential path is not a regular file.",
        )
    return _CredentialFileSnapshot(path.read_bytes(), stat.S_IMODE(metadata.st_mode))


def _restore_credential_file(path: Path, snapshot: _CredentialFileSnapshot) -> None:
    if snapshot.contents is None:
        if path.exists() or path.is_symlink():
            path.unlink()
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.restore-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(snapshot.contents)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _parse_expiry(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError("expiry must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class GoogleAuthService:
    def __init__(
        self,
        settings: Settings,
        events: EventBus,
        *,
        runner: CommandRunner | None = None,
        global_auth: GlobalAuth | None = None,
    ):
        self.settings = settings
        self.events = events
        self.runner = runner or SubprocessRunner()
        self.global_auth = global_auth

    @property
    def google_token_path(self) -> Path:
        return self.settings.hermes_home / "google_token.json"

    def _row(self, status_value: str, detail: str) -> Integration:
        return Integration(
            key=GOOGLE_KEY,
            label="Google",
            description="Gmail, Calendar, and Drive through the Hermes google-workspace skill.",
            category="tool",
            kind="integration",
            is_set=status_value == "connected",
            is_password=False,
            status=status_value,
            detail=detail,
            group="google",
            group_label="Google",
            auth="oauth",
        )

    def _main_status(self) -> Integration:
        path = self.google_token_path
        if not path.exists():
            return self._row("not_connected", "No Google token is configured for Botter bots.")
        try:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise OSError("Google token path is not a regular file")
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Google token must be an object")
            token = payload.get("token")
            refresh_token = payload.get("refresh_token")
            has_token = isinstance(token, str) and bool(token.strip())
            has_refresh = isinstance(refresh_token, str) and bool(refresh_token.strip())
            if not has_token and not has_refresh:
                raise ValueError("Google token has no usable grant")
            expiry = _parse_expiry(payload.get("expiry"))
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            return self._row("error", "The Google token file or its expiry is invalid.")
        if expiry is not None and datetime.now(timezone.utc) >= expiry and not has_refresh:
            return self._row(
                "not_connected",
                "The Google token is expired; re-run Google Workspace setup in Hermes.",
            )
        return self._row("connected", "Token configured and unexpired; not externally verified.")

    async def status(self) -> Integration:
        if self.global_auth is None:
            return self._main_status()
        async with self.global_auth.lock:
            main = self._main_status()
            try:
                out_of_sync = await self.global_auth._google_consistency_locked()
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                return self._row("error", "Global Google authentication state could not be read.")
        if out_of_sync:
            return self._row(
                "error",
                "Google authentication needs to be synchronized to all Botter bots. Connect again to repair it.",
            )
        if main.status == "connected":
            return main.model_copy(
                update={
                    "detail": "Connected for every Botter bot; not externally verified.",
                    "sync_status": "synced",
                    "sync_detail": "Available to every Botter bot.",
                }
            )
        return main

    def _command(self, *arguments: str) -> list[str]:
        # /usr/bin/env pins HERMES_HOME for the child without extending the
        # CommandRunner protocol; the script derives every path from it.
        return [
            "/usr/bin/env",
            f"HERMES_HOME={self.settings.hermes_home}",
            str(self.settings.hermes_python),
            str(self.settings.google_setup_script),
            *arguments,
        ]

    @staticmethod
    def _error(stdout: str, stderr: str, fallback: str) -> str:
        for line in (*stdout.splitlines(), *stderr.splitlines()):
            if line.startswith("ERROR:"):
                return line.removeprefix("ERROR:").strip()[:300]
        return fallback

    async def _store_client_secret(self, raw_json: str) -> _CredentialFileSnapshot:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError as exc:
            raise APIError(422, "invalid_client_secret", "Client secret must be valid JSON") from exc
        if not isinstance(payload, dict) or not ({"installed", "web"} & set(payload)):
            raise APIError(
                422, "invalid_client_secret", "Expected a Google OAuth client JSON (Desktop app)"
            )
        destination = self.settings.google_client_secret_path
        snapshot = _snapshot_credential_file(destination)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".google-client.botter-", dir=self.settings.hermes_home
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload))
            try:
                result = await self.runner.run(
                    self._command("--client-secret", str(temporary)), timeout=60, check=False
                )
            except BaseException:
                _restore_credential_file(destination, snapshot)
                raise
        finally:
            temporary.unlink(missing_ok=True)
        if result.returncode != 0:
            _restore_credential_file(destination, snapshot)
            message = self._error(result.stdout, result.stderr, "Client secret was rejected")
            raise APIError(422, "invalid_client_secret", message)
        try:
            stored = _snapshot_credential_file(destination)
            if stored.contents is None:
                raise OSError("Google client secret was not stored")
            os.chmod(destination, 0o600)
        except (OSError, APIError) as exc:
            _restore_credential_file(destination, snapshot)
            raise APIError(
                500,
                "client_secret_store_failed",
                "Google client credentials could not be stored safely.",
            ) from exc
        return snapshot

    async def connect(self, request: GoogleConnect) -> Integration | Authorization:
        if request.client_secret_json is not None:
            if self.global_auth is not None:
                async with self.global_auth.lock:
                    await self.global_auth.assert_google_mutation_idle()
                    snapshot = await self._store_client_secret(request.client_secret_json)
                    try:
                        await self.global_auth.sync_google_locked(require_token=False)
                    except APIError as exc:
                        if exc.code != "sandbox_rebuild_failed":
                            _restore_credential_file(self.settings.google_client_secret_path, snapshot)
                        raise
                    except BaseException:
                        _restore_credential_file(self.settings.google_client_secret_path, snapshot)
                        raise
            else:
                await self._store_client_secret(request.client_secret_json)

        if request.code is not None:
            if not request.code.strip():
                raise APIError(422, "google_auth_failed", "Paste the full redirect URL first")
            if self.global_auth is not None:
                async with self.global_auth.lock:
                    await self.global_auth.assert_google_mutation_idle()
                    result = await self.runner.run(
                        self._command("--auth-code", request.code.strip()), timeout=120, check=False
                    )
                    if result.returncode != 0:
                        message = self._error(
                            result.stdout, result.stderr, "Google did not accept the pasted URL"
                        )
                        raise APIError(422, "google_auth_failed", message)
                    if self._main_status().status != "connected":
                        raise APIError(502, "google_auth_failed", "Google did not create a usable token")
                    await self.global_auth.sync_google_locked()
            else:
                result = await self.runner.run(
                    self._command("--auth-code", request.code.strip()), timeout=120, check=False
                )
                if result.returncode != 0:
                    message = self._error(
                        result.stdout, result.stderr, "Google did not accept the pasted URL"
                    )
                    raise APIError(422, "google_auth_failed", message)
            row = await self.status()
            await self._publish(row)
            return row

        main_row = self._main_status()
        if main_row.status == "connected" and request.client_secret_json is None:
            if self.global_auth is not None:
                async with self.global_auth.lock:
                    await self.global_auth.assert_google_mutation_idle()
                    await self.global_auth.sync_google_locked()
            row = await self.status()
            await self._publish(row)
            return row
        if not self.settings.google_client_secret_path.exists():
            return Authorization(
                url=None,
                instructions=GOOGLE_CLIENT_SECRET_INSTRUCTIONS,
                needs_client_secret=True,
            )
        result = await self.runner.run(self._command("--auth-url"), timeout=60, check=False)
        url = next(
            (line.strip() for line in reversed(result.stdout.splitlines()) if line.strip().startswith("https://")),
            None,
        )
        if result.returncode != 0 or url is None:
            message = self._error(
                result.stdout, result.stderr, "Unable to start the Google sign-in flow"
            )
            raise APIError(502, "google_auth_failed", message)
        return Authorization(url=url, instructions=GOOGLE_CODE_INSTRUCTIONS, code_entry=True)

    async def disconnect(self) -> Integration:
        try:
            if self.global_auth is not None:
                async with self.global_auth.lock:
                    await self.global_auth.assert_google_mutation_idle()
                    # Best-effort server-side revoke; local token removal is the guarantee.
                    with suppress(RuntimeError, OSError):
                        await self.runner.run(self._command("--revoke"), timeout=60, check=False)
                    await self.global_auth.remove_google_tokens_locked()
            else:
                with suppress(RuntimeError, OSError):
                    await self.runner.run(self._command("--revoke"), timeout=60, check=False)
                self._delete_token()
        except OSError as exc:
            raise APIError(
                500, "connection_write_failed", "Unable to update the Hermes connection file"
            ) from exc
        row = (await self.status()).model_copy(update={"detail": "Removed from every Botter bot."})
        await self._publish(row)
        return row

    def _delete_token(self) -> None:
        path = self.google_token_path
        if not path.exists() and not path.is_symlink():
            return
        metadata = path.lstat()
        if not (stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)):
            raise OSError("Google token path cannot be removed safely")
        path.unlink()

    async def _publish(self, row: Integration) -> None:
        await self.events.publish(
            "integration_updated", {"key": row.key, "is_set": row.is_set}
        )
