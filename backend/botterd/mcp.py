"""MCP servers for every Botter bot.

An MCP server is the cheap way to give bots a large tool catalog. One entry in
`mcp_servers` reaches hundreds of apps, where the credential surface in
`credentials.py` costs one env key, one fan-out, and sometimes a restart per
app.

Three verified Hermes behaviours shape this module:

* Config lives in `mcp_servers` of each profile's `config.yaml`, with a
  free-form `url` and `headers` map (`hermes_cli/mcp_config.py`). Hermes expands
  `${NAME}` in headers from that profile's `.env`.
* OAuth servers store their grant per profile under
  `HERMES_HOME/mcp-tokens/<name>.json` (`tools/mcp_oauth.py`).
* **The gateway does not watch config.yaml.** Its config watcher lives in the
  interactive CLI (`cli.py:_check_config_mcp_changes`), and `gateway/run.py`
  calls `discover_mcp_tools()` once at startup. Every mutation here therefore
  restarts the gateway, exactly as `channels.py` does.

Writes go straight to the files rather than through `hermes serve`. The
dashboard's config endpoints run the document through `yaml.dump`, which would
strip the comments from the user's hand-maintained config for the sake of one
nested key. They add no validation that Botter needs. See `yaml_io`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import APIError
from .events import EventBus
from .global_auth import GlobalAuth
from .hermes_serve import HermesServe
from .models import McpAuthorization, McpServer, McpServerUpdate
from .registry import CommandRunner, restart_gateway
from .yaml_io import YAMLError, load_yaml

MCP_SERVERS_KEY = "mcp_servers"
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class McpPreset:
    name: str
    label: str
    description: str
    url: str
    auth: str
    docs_url: str


# Composio Connect is one endpoint for roughly a thousand apps. Phase 0 proved
# it is an MCP OAuth resource, not an API-key endpoint: it answers 401 with
# `WWW-Authenticate: Bearer ... resource_metadata=…`, and that metadata names
# https://login.composio.dev as the authorization server. So it holds no key,
# and there is nothing to add to the credential surface for it.
PRESETS: tuple[McpPreset, ...] = (
    McpPreset(
        name="composio",
        label="Composio",
        description=(
            "About a thousand apps — Gmail, Notion, Linear, Jira, Slack — behind one "
            "connection. A bot asks you to authorize an app the first time it needs it."
        ),
        url="https://connect.composio.dev/mcp",
        auth="oauth",
        docs_url="https://docs.composio.dev/docs/composio-connect",
    ),
)
PRESETS_BY_URL = {preset.url: preset for preset in PRESETS}
PRESETS_BY_NAME = {preset.name: preset for preset in PRESETS}


def _entry_auth(entry: dict[str, Any]) -> str:
    if str(entry.get("auth") or "").lower() == "oauth":
        return "oauth"
    return "header" if entry.get("headers") else "none"


class McpService:
    def __init__(
        self,
        settings: Settings,
        events: EventBus,
        *,
        runner: CommandRunner,
        health_check: Callable[[], Awaitable[bool]],
        serve: HermesServe | None = None,
        global_auth: GlobalAuth | None = None,
    ):
        self.settings = settings
        self.events = events
        self.runner = runner
        self.health_check = health_check
        self.serve = serve
        self.global_auth = global_auth

    @property
    def config_path(self) -> Path:
        return self.settings.hermes_config_path

    def token_path(self, name: str) -> Path:
        return self.settings.hermes_home / "mcp-tokens" / f"{name}.json"

    # ── reads ────────────────────────────────────────────────────────────

    def _servers_in(self, path: Path) -> dict[str, dict[str, Any]]:
        try:
            document = load_yaml(path)
        except (OSError, UnicodeError, YAMLError) as exc:
            raise APIError(502, "hermes_config_unreadable", "Hermes config could not be read") from exc
        if not isinstance(document, dict):
            return {}
        servers = document.get(MCP_SERVERS_KEY)
        if not isinstance(servers, dict):
            return {}
        return {str(key): dict(value) for key, value in servers.items() if isinstance(value, dict)}

    def _row(self, name: str, entry: dict[str, Any]) -> McpServer:
        url = entry.get("url")
        preset = PRESETS_BY_URL.get(str(url)) or PRESETS_BY_NAME.get(name)
        auth = _entry_auth(entry)
        authorized = self.token_path(name).exists()
        enabled = entry.get("enabled") is not False
        if not enabled:
            status, detail = "not_connected", "Disabled in the Hermes config."
        elif auth == "oauth" and not authorized:
            status, detail = "not_connected", "Authorization is required before bots can use it."
        else:
            status, detail = "connected", "Available to every Botter bot; not externally verified."
        return McpServer(
            name=name,
            label=preset.label if preset else name.replace("-", " ").replace("_", " ").title(),
            description=preset.description if preset else "",
            url=str(url) if url else None,
            command=str(entry["command"]) if entry.get("command") else None,
            auth=auth,
            enabled=enabled,
            preset=preset.name if preset else None,
            docs_url=preset.docs_url if preset else None,
            authorized=authorized,
            status=status,
            detail=detail,
        )

    def _preset_rows(self, installed: set[str]) -> list[McpServer]:
        return [
            McpServer(
                name=preset.name,
                label=preset.label,
                description=preset.description,
                url=preset.url,
                auth=preset.auth,
                preset=preset.name,
                docs_url=preset.docs_url,
                status="not_connected",
                detail="Not added yet.",
            )
            for preset in PRESETS
            if preset.name not in installed
        ]

    async def list(self) -> tuple[list[McpServer], list[McpServer]]:
        entries = self._servers_in(self.config_path)
        rows = [self._row(name, entry) for name, entry in sorted(entries.items())]
        rows = [await self._with_sync(row) for row in rows]
        return rows, self._preset_rows(set(entries))

    async def _with_sync(self, row: McpServer) -> McpServer:
        if self.global_auth is None:
            return row
        expected = self._servers_in(self.config_path).get(row.name)
        drifted: list[str] = []
        try:
            for slug in await self.global_auth.registered_slugs():
                if self._servers_in(self.global_auth.config_path(slug)).get(row.name) != expected:
                    drifted.append(slug)
            # A bot with the config entry but not the OAuth grant cannot use the
            # server, so that counts as drift too.
            if row.authorized:
                async with self.global_auth.lock:
                    for slug in await self.global_auth.mcp_token_consistency(row.name):
                        if slug not in drifted:
                            drifted.append(slug)
        except (OSError, UnicodeError, ValueError, APIError):
            drifted = ["unavailable"]
        if drifted:
            return row.model_copy(
                update={
                    "sync_status": "out_of_sync",
                    "status": "error",
                    "sync_detail": f"Missing or different on {len(drifted)} bot profile(s).",
                    "detail": f"Missing or different on {len(drifted)} bot profile(s). Save it again to repair it.",
                }
            )
        return row.model_copy(
            update={"sync_status": "synced", "sync_detail": "Available to every Botter bot."}
        )

    async def status(self, name: str) -> McpServer:
        entry = self._servers_in(self.config_path).get(name)
        if entry is None:
            raise APIError(404, "mcp_server_not_found", f"MCP server not found: {name}")
        return await self._with_sync(self._row(name, entry))

    # ── writes ───────────────────────────────────────────────────────────

    @staticmethod
    def _validate(name: str, request: McpServerUpdate) -> None:
        if not NAME_PATTERN.fullmatch(name):
            raise APIError(422, "invalid_mcp_name", "Name must be lower-case letters, digits, - or _")
        url = request.url.strip()
        if not url.startswith("https://"):
            raise APIError(422, "invalid_mcp_url", "The server URL must start with https://")
        for header, value in request.headers.items():
            if not HEADER_NAME_PATTERN.fullmatch(header):
                raise APIError(422, "invalid_mcp_header", f"Unsupported header name: {header}")
            if any(character in value for character in ("\x00", "\r", "\n")):
                raise APIError(422, "invalid_mcp_header", f"{header} contains unsupported characters")
        if request.auth == "header" and not request.headers:
            raise APIError(422, "invalid_mcp_header", "Header auth needs at least one header")

    async def put(self, name: str, request: McpServerUpdate) -> tuple[McpServer, bool]:
        self._validate(name, request)
        entry: dict[str, Any] = {"url": request.url.strip()}
        if request.auth == "oauth":
            entry["auth"] = "oauth"
        if request.headers:
            entry["headers"] = dict(request.headers)

        def mutate(document: Any) -> bool:
            servers = document.get(MCP_SERVERS_KEY)
            if not isinstance(servers, dict):
                servers = {}
                document[MCP_SERVERS_KEY] = servers
            if dict(servers.get(name) or {}) == entry:
                return False
            servers[name] = entry
            return True

        changed = await self._apply(mutate)
        restarted = changed and await self._restart()
        server = await self.status(name)
        await self._publish(server)
        return server, restarted

    async def delete(self, name: str) -> tuple[McpServer, bool]:
        before = await self.status(name)

        def mutate(document: Any) -> bool:
            servers = document.get(MCP_SERVERS_KEY)
            if not isinstance(servers, dict) or name not in servers:
                return False
            del servers[name]
            # Leave no empty mapping behind in the user's config.
            if not servers:
                del document[MCP_SERVERS_KEY]
            return True

        changed = await self._apply(mutate)
        # Removing the entry without the grant would leave orphaned tokens that
        # silently re-authorize a re-added server.
        if self.global_auth is not None:
            async with self.global_auth.lock:
                changed = await self.global_auth.remove_mcp_tokens_locked(name) or changed
        restarted = changed and await self._restart()
        removed = before.model_copy(
            update={
                "status": "not_connected",
                "sync_status": None,
                "sync_detail": None,
                "detail": "Removed from every Botter bot.",
            }
        )
        await self._publish(removed)
        return removed, restarted

    # ── OAuth ────────────────────────────────────────────────────────────
    #
    # Hermes' dashboard backend owns the flow: it registers a client, opens a
    # loopback callback on the supervised `hermes serve` child, and writes the
    # grant into that profile's mcp-tokens directory
    # (`hermes_cli/web_routers/mcp.py:221`). botterd authorizes main only, then
    # copies the grant outward — one authorization, every bot.

    AUTHORIZE_INSTRUCTIONS = (
        "Sign in and approve access in your browser. This window updates by itself "
        "when the provider confirms it."
    )

    def _require_serve(self) -> HermesServe:
        if self.serve is None:
            raise APIError(
                502, "hermes_dashboard_unavailable", "The Hermes management service is not running"
            )
        return self.serve

    @staticmethod
    def _flow_from_payload(payload: Any, name: str, instructions: str) -> McpAuthorization:
        if not isinstance(payload, dict) or not payload.get("flow_id"):
            raise APIError(502, "hermes_dashboard_error", "Unexpected MCP OAuth flow shape")
        status = str(payload.get("status") or "starting")
        if status not in ("starting", "authorization_required", "approved", "error"):
            status = "error"
        return McpAuthorization(
            flow_id=str(payload["flow_id"]),
            server=str(payload.get("server_name") or name),
            status=status,  # type: ignore[arg-type]
            url=payload.get("authorization_url"),
            instructions=instructions if status == "authorization_required" else "",
            error=payload.get("error"),
        )

    async def authorize(self, name: str) -> McpAuthorization:
        await self.status(name)  # 404s an unknown server before starting a flow
        payload = await self._require_serve().request(
            "POST", f"/api/mcp/servers/{name}/auth"
        )
        return self._flow_from_payload(payload, name, self.AUTHORIZE_INSTRUCTIONS)

    async def authorization_status(self, flow_id: str) -> tuple[McpAuthorization, McpServer | None]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", flow_id):
            raise APIError(422, "invalid_flow_id", "Malformed authorization id")
        payload = await self._require_serve().request("GET", f"/api/mcp/oauth/flows/{flow_id}")
        name = str((payload or {}).get("server_name") or "")
        flow = self._flow_from_payload(payload, name, self.AUTHORIZE_INSTRUCTIONS)
        if flow.status != "approved":
            return flow, None
        # Approved against main. Give every bot the same grant, then restart so
        # the gateway reconnects holding it.
        if self.global_auth is not None:
            async with self.global_auth.lock:
                await self.global_auth.sync_mcp_tokens_locked(flow.server)
        await self._restart()
        server = await self.status(flow.server)
        await self._publish(server)
        return flow, server

    async def _apply(self, mutate: Callable[[Any], bool]) -> bool:
        if self.global_auth is not None:
            return await self.global_auth.mutate_profile_configs(mutate)
        from .yaml_io import write_yaml_atomic

        document = load_yaml(self.config_path)
        if not mutate(document):
            return False
        return write_yaml_atomic(self.config_path, document)

    async def _restart(self) -> bool:
        # The gateway reads mcp_servers once at startup, so a change is inert
        # until it restarts. Verified in Phase 0 against gateway/run.py.
        try:
            await restart_gateway(self.runner, self.health_check)
        except RuntimeError as exc:
            raise APIError(
                502,
                "gateway_restart_failed",
                "MCP server saved but the Hermes gateway restart failed",
            ) from exc
        return True

    async def _publish(self, server: McpServer) -> None:
        await self.events.publish(
            "mcp_updated", {"name": server.name, "status": server.status}
        )
