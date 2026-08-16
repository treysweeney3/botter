"""The single credential surface for every Botter bot.

This replaces the old split between `/v1/connections` (8 hardcoded apps) and
`/v1/integrations` (the generic Hermes env catalog). Both meant the same thing:
an env credential that every Botter-managed profile must be able to read.

Everything reads from, and writes through, the supervised `hermes serve`
child's `/api/env` surface. That runs Hermes' unified credential lifecycle,
which reconciles stale config.yaml mirrors and clears env-seeded
credential-pool entries. A raw .env file edit does neither, which is why the
curated apps no longer take that shortcut.

Two rows are not env values and stay explicit:

* Google — an OAuth token file. See `google_auth.py`.
* Slack — main's own agent. Read-only here; Hermes owns it.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import yaml

from .config import Settings, read_env_value
from .errors import APIError
from .events import EventBus
from .global_auth import GlobalAuth
from .google_auth import GOOGLE_KEY, GoogleAuthService
from .hermes_serve import HermesServe
from .models import Integration
from .registry import CommandRunner, restart_gateway


ENV_KEY_PATTERN = re.compile(r"^[A-Z_][A-Z0-9_]*$")
SLACK_KEY = "SLACK"

# Infrastructure keys that would break botterd/Hermes plumbing if edited blind.
# Slack's two tokens stay here: main's Slack is the user's own agent, so Botter
# shows it and never writes it.
PROTECTED_KEYS = frozenset(
    {"API_SERVER_KEY", "HERMES_DASHBOARD_SESSION_TOKEN", "SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"}
)

_ACRONYMS = {"API", "URL", "ID", "TTS", "STT", "SDK", "AWS", "GCP", "SSH", "HTTP", "JSON", "OAUTH"}


@dataclass(frozen=True, slots=True)
class Curated:
    """Display and behaviour that Hermes' env catalog does not carry."""

    group: str
    label: str
    order: int
    required: bool = True
    restart: bool = False


# Ranked ahead of the generic catalog. Membership here changes presentation
# only — every key below is an ordinary env credential and takes the same
# write path as the other ~125.
CURATED: dict[str, Curated] = {
    "GITHUB_TOKEN": Curated("github", "GitHub", 0),
    "VERCEL_TOKEN": Curated("vercel", "Vercel", 1),
    "VERCEL_TEAM_ID": Curated("vercel", "Vercel", 1, required=False),
    "SUPABASE_ACCESS_TOKEN": Curated("supabase", "Supabase", 2),
    "SUPABASE_PROJECT_REF": Curated("supabase", "Supabase", 2, required=False),
    "OPENROUTER_API_KEY": Curated("openrouter", "OpenRouter", 3),
    "EXA_API_KEY": Curated("exa", "Exa", 4, restart=True),
    "XAI_API_KEY": Curated("xai", "xAI", 5),
}


def _label_for(key: str, row: dict[str, Any]) -> str:
    provider_label = str(row.get("provider_label") or "").strip()
    if provider_label:
        suffix = key.rsplit("_", 1)[-1]
        if suffix in ("KEY", "TOKEN"):
            return f"{provider_label} API key"
        return provider_label
    words = [
        word if word in _ACRONYMS else word.capitalize()
        for word in key.split("_")
        if word
    ]
    return " ".join(words) or key


_CUSTOM_CONFIG_SUFFIXES = (
    "_BASE_URL",
    "_THREAD_ID",
    "_DIRECTORY",
    "_DISABLED",
    "_INTERVAL",
    "_ENABLED",
    "_ENDPOINT",
    "_FILENAME",
    "_TIMEOUT",
    "_VOLUMES",
    "_PROXIES",
    "_SECONDS",
    "_STEALTH",
    "_PROFILE",
    "_REGION",
    "_SCHEME",
    "_DOMAIN",
    "_ENGINE",
    "_FORMAT",
    "_PROMPT",
    "_DEBUG",
    "_UNITS",
    "_LIMIT",
    "_COUNT",
    "_LEVEL",
    "_MODEL",
    "_IMAGE",
    "_PATH",
    "_FILE",
    "_MODE",
    "_HOST",
    "_PORT",
    "_ENV",
    "_TTL",
    "_FLAG",
    "_URL",
)
_SERVICE_IDENTIFIER_SUFFIXES = (
    "_CREDENTIALS_PATH",
    "_PROJECT_ID",
    "_PUBLIC_KEY",
)


def integration_kind_for(key: str, row: dict[str, Any]) -> str:
    """Classify catalog rows by product meaning, not Hermes input masking.

    Hermes treats unknown keys as custom password fields, including ordinary
    settings already present in .env. Infer those from credential-oriented key
    suffixes instead. For catalog entries, password fields are credentials and
    a non-advanced vendor link marks a service locator/identifier; advanced
    URLs are documentation for ordinary overrides such as browser engines and
    provider base URLs.
    """
    if key in CURATED:
        return "integration"
    if row.get("custom"):
        return "config" if key.endswith(_CUSTOM_CONFIG_SUFFIXES) else "integration"
    if (
        row.get("is_password", True)
        or key.endswith(_SERVICE_IDENTIFIER_SUFFIXES)
        or (row.get("url") and not row.get("advanced"))
    ):
        return "integration"
    return "config"


def _integration_from_row(key: str, row: dict[str, Any]) -> Integration:
    curated = CURATED.get(key)
    is_set = bool(row.get("is_set"))
    return Integration(
        key=key,
        label=curated.label if curated and curated.required else _label_for(key, row),
        description=str(row.get("description") or ""),
        url=row.get("url"),
        category="tool" if curated else str(row.get("category") or "custom"),
        kind=integration_kind_for(key, row),
        is_set=is_set,
        redacted_value=row.get("redacted_value"),
        is_password=bool(row.get("is_password", True)),
        advanced=bool(row.get("advanced")),
        custom=bool(row.get("custom")),
        status="connected" if is_set else "not_connected",
        group=curated.group if curated else None,
        group_label=curated.label if curated else None,
        required=curated.required if curated else True,
        restart_after_write=curated.restart if curated else False,
        auth="value",
    )


class CredentialService:
    def __init__(
        self,
        settings: Settings,
        serve: HermesServe,
        events: EventBus,
        *,
        runner: CommandRunner,
        health_check: Callable[[], Awaitable[bool]],
        google: GoogleAuthService | None = None,
        global_auth: GlobalAuth | None = None,
    ):
        self.settings = settings
        self.serve = serve
        self.events = events
        self.runner = runner
        self.health_check = health_check
        self.google = google
        self.global_auth = global_auth

    @property
    def env_path(self):
        return self.settings.hermes_home / ".env"

    async def _catalog(self) -> dict[str, dict[str, Any]]:
        payload = await self.serve.request("GET", "/api/env")
        if not isinstance(payload, dict):
            raise APIError(502, "hermes_dashboard_error", "Unexpected env catalog shape")
        return payload

    @staticmethod
    def _visible(key: str, row: dict[str, Any]) -> bool:
        # "messaging" covers per-platform extras beyond the Channels cards
        # (home channels, allow-lists) plus cross-cutting gateway knobs —
        # platform config belongs to the Channels surface, not here.
        return (
            not row.get("channel_managed")
            and row.get("category") != "messaging"
            and key not in PROTECTED_KEYS
        )

    def _slack_row(self) -> Integration:
        def row(status_value: str, detail: str) -> Integration:
            return Integration(
                key=SLACK_KEY,
                label="Slack",
                description="Main's own Slack agent.",
                category="tool",
                kind="integration",
                is_set=status_value == "connected",
                is_password=False,
                status=status_value,
                detail=detail,
                group="slack",
                group_label="Slack",
                auth="external",
            )

        try:
            config = yaml.safe_load(self.settings.hermes_config_path.read_text(encoding="utf-8")) or {}
            slack = config.get("platforms", {}).get("slack", {}) if isinstance(config, dict) else {}
            enabled = slack.get("enabled") is True if isinstance(slack, dict) else False
            bot_token = read_env_value(self.env_path, "SLACK_BOT_TOKEN")
            app_token = read_env_value(self.env_path, "SLACK_APP_TOKEN")
        except (OSError, UnicodeError, yaml.YAMLError, AttributeError):
            return row("error", "Main-profile Slack configuration could not be read.")
        if enabled and bot_token and app_token:
            return row(
                "connected",
                "Configured in the main Hermes profile, not externally verified; managed by Hermes.",
            )
        if enabled and (bot_token or app_token):
            return row(
                "error",
                "Slack is enabled in the main Hermes profile but its credentials are incomplete.",
            )
        return row(
            "not_connected",
            "Slack is not configured in the main Hermes profile; managed by Hermes.",
        )

    async def list(self) -> list[Integration]:
        if self.global_auth is not None:
            async with self.global_auth.lock:
                rows = await self._list_unlocked(lock_held=True)
        else:
            rows = await self._list_unlocked(lock_held=False)

        rows.append(self._slack_row())
        if self.google is not None:
            rows.append(await self.google.status())

        # Curated apps first in their fixed order, then configured keys, then
        # the browseable catalog by category and label.
        category_order = {"tool": 0, "skill": 1, "provider": 2, "setting": 3, "custom": 4}
        curated_order = {
            **{key: item.order for key, item in CURATED.items()},
            SLACK_KEY: 6,
            GOOGLE_KEY: 7,
        }
        rows.sort(key=lambda item: (
            curated_order.get(item.key, 99),
            not item.is_set,
            category_order.get(item.category, 5),
            item.label.lower(),
        ))
        return rows

    async def _list_unlocked(self, *, lock_held: bool) -> list[Integration]:
        catalog = await self._catalog()
        rows = [
            _integration_from_row(key, row)
            for key, row in sorted(catalog.items())
            if isinstance(row, dict) and self._visible(key, row)
        ]
        # A curated key Hermes has never seen still deserves its card.
        known = {item.key for item in rows}
        rows.extend(
            _integration_from_row(key, {"is_set": False})
            for key in CURATED
            if key not in known
        )
        if self.global_auth is None:
            return rows
        integration_keys = [item.key for item in rows if item.kind == "integration"]
        try:
            if lock_held:
                _, mismatches = await self.global_auth._env_mismatches_locked(integration_keys)
            else:
                _, mismatches = await self.global_auth.env_mismatches(integration_keys)
        except (OSError, UnicodeError):
            mismatches = {key: ("unavailable",) for key in integration_keys}
        return [self._with_sync(item, mismatches.get(item.key, ())) for item in rows]

    async def _guard(self, key: str) -> dict[str, Any]:
        if key in (SLACK_KEY, GOOGLE_KEY):
            raise APIError(403, "integration_not_managed", f"{key} is managed elsewhere")
        if not ENV_KEY_PATTERN.fullmatch(key):
            raise APIError(422, "invalid_integration_key", "Key must be UPPER_SNAKE_CASE")
        catalog = await self._catalog()
        row = catalog.get(key)
        if isinstance(row, dict) and not self._visible(key, row):
            raise APIError(403, "integration_not_managed", f"{key} is managed elsewhere")
        return row if isinstance(row, dict) else {}

    async def put(self, key: str, value: str) -> Integration:
        cleaned = value.strip()
        if not cleaned:
            raise APIError(422, "integration_value_required", "value must not be empty")
        if any(character in cleaned for character in ("\x00", "\r", "\n")):
            raise APIError(422, "invalid_integration_value", "value contains unsupported characters")
        prior_row = await self._guard(key)
        candidate = _integration_from_row(key, prior_row)
        try:
            if self.global_auth is not None and candidate.kind == "integration":
                changed = await self.global_auth.mutate_dashboard_env(self.serve, key, cleaned)
            else:
                await self.serve.request("PUT", "/api/env", json={"key": key, "value": cleaned})
                changed = True
        except APIError as exc:
            if exc.status_code == 400:
                # Hermes' save path rejects denylisted names (PATH, LD_PRELOAD, …)
                # with a human-readable reason — surface it as a client error.
                raise APIError(422, "invalid_integration_key", exc.message) from exc
            raise
        restarted = changed and await self._restart_if_required(candidate)
        integration = await self._status(key)
        if restarted:
            integration = integration.model_copy(
                update={
                    "detail": f"Hermes gateway restarted for {candidate.group_label or key}."
                }
            )
        await self._publish(integration)
        return integration

    async def delete(self, key: str) -> Integration:
        prior_row = await self._guard(key)
        prior = _integration_from_row(key, prior_row)
        try:
            if self.global_auth is not None and prior.kind == "integration":
                changed = await self.global_auth.mutate_dashboard_env(self.serve, key, None)
                if not changed:
                    raise APIError(404, "integration_not_found", f"{key} is not set")
            else:
                await self.serve.request("DELETE", "/api/env", json={"key": key})
                changed = True
        except APIError as exc:
            if exc.status_code == 404:
                raise APIError(404, "integration_not_found", f"{key} is not set") from exc
            raise
        restarted = changed and await self._restart_if_required(prior)
        integration = await self._status(key, fallback_row=prior_row)
        detail = "Removed from every Botter bot."
        if restarted:
            detail += f" Hermes gateway restarted for {prior.group_label or key}."
        integration = integration.model_copy(update={"detail": detail})
        await self._publish(integration)
        return integration

    async def _restart_if_required(self, candidate: Integration) -> bool:
        if not candidate.restart_after_write:
            return False
        try:
            await restart_gateway(self.runner, self.health_check)
        except RuntimeError as exc:
            raise APIError(
                502,
                "gateway_restart_failed",
                "Credential saved but Hermes gateway restart failed",
            ) from exc
        return True

    async def _status(
        self, key: str, fallback_row: dict[str, Any] | None = None
    ) -> Integration:
        if self.global_auth is not None:
            async with self.global_auth.lock:
                return await self._status_unlocked(key, fallback_row, lock_held=True)
        return await self._status_unlocked(key, fallback_row, lock_held=False)

    async def _status_unlocked(
        self,
        key: str,
        fallback_row: dict[str, Any] | None,
        *,
        lock_held: bool,
    ) -> Integration:
        catalog = await self._catalog()
        row = catalog.get(key)
        if isinstance(row, dict):
            integration = _integration_from_row(key, row)
            return await self._sync_one(integration, lock_held=lock_held)
        # A deleted custom key drops out of the catalog entirely. A curated key
        # keeps its card.
        fallback = dict(fallback_row or {})
        fallback.update({"is_set": False, "redacted_value": None})
        if key not in CURATED:
            fallback.update({"category": "custom", "custom": True})
        return await self._sync_one(_integration_from_row(key, fallback), lock_held=lock_held)

    async def _sync_one(self, integration: Integration, *, lock_held: bool = False) -> Integration:
        if self.global_auth is None or integration.kind != "integration":
            return integration
        try:
            if lock_held:
                _, mismatches = await self.global_auth._env_mismatches_locked([integration.key])
            else:
                _, mismatches = await self.global_auth.env_mismatches([integration.key])
            return self._with_sync(integration, mismatches[integration.key])
        except (OSError, UnicodeError):
            return self._with_sync(integration, ("unavailable",))

    @staticmethod
    def _with_sync(integration: Integration, mismatches: tuple[str, ...]) -> Integration:
        if integration.kind != "integration":
            return integration
        if mismatches:
            return integration.model_copy(
                update={
                    "sync_status": "out_of_sync",
                    "sync_detail": f"Authentication is out of sync for {len(mismatches)} bot profile(s).",
                    "status": "error",
                    "detail": (
                        f"Authentication is out of sync for {len(mismatches)} bot profile(s). "
                        "Save it again to repair it."
                    ),
                }
            )
        detail = (
            "Configured for every Botter bot; not externally verified."
            if integration.is_set
            else "Not configured for Botter bots."
        )
        return integration.model_copy(
            update={
                "sync_status": "synced",
                "sync_detail": "Available to every Botter bot.",
                "detail": detail,
            }
        )

    async def _publish(self, integration: Integration) -> None:
        await self.events.publish(
            "integration_updated", {"key": integration.key, "is_set": integration.is_set}
        )
