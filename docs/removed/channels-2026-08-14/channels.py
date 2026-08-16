"""Messaging-channel catalog and credential writes via the Hermes dashboard API.

Channels are Hermes messaging platforms (Telegram, Discord, Matrix, …) on the
main profile. The catalog and all writes go through a supervised `hermes serve`
child (see hermes_serve.py) so key allowlists, value validation, and the
enabled flag stay Hermes-owned. Slack is excluded here on purpose — main's
Slack is the user's own agent and stays a display-only row in /v1/integrations
(see `credentials.py`).
Gateway restarts reuse botterd's proven kickstart + health-wait path because
the dashboard's own restart endpoint is fire-and-forget.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .errors import APIError
from .events import EventBus
from .hermes_serve import HermesServe
from .models import Channel, ChannelEnvVar, ChannelUpdate
from .registry import CommandRunner, restart_gateway


# slack: locked to the existing display-only Connection row (never mutate main's
# Slack). The rest are port-binding or machine-to-machine pseudo-platforms that
# make no sense as user-added chat channels from this UI.
EXCLUDED_CHANNELS = frozenset(
    {"slack", "api_server", "webhook", "msgraph_webhook", "wecom_callback", "relay", "local"}
)


def _channel_from_payload(payload: dict[str, Any]) -> Channel:
    env_vars = [
        ChannelEnvVar(
            key=str(var.get("key") or ""),
            label=str(var.get("prompt") or var.get("key") or ""),
            description=str(var.get("description") or ""),
            help=str(var.get("help") or ""),
            url=var.get("url"),
            required=bool(var.get("required")),
            is_set=bool(var.get("is_set")),
            redacted_value=var.get("redacted_value"),
            is_password=bool(var.get("is_password")),
            advanced=bool(var.get("advanced")),
        )
        for var in payload.get("env_vars") or []
        if isinstance(var, dict)
    ]
    return Channel(
        id=str(payload.get("id") or ""),
        name=str(payload.get("name") or ""),
        description=str(payload.get("description") or ""),
        docs_url=payload.get("docs_url"),
        enabled=bool(payload.get("enabled")),
        configured=bool(payload.get("configured")),
        gateway_running=bool(payload.get("gateway_running")),
        state=payload.get("state"),
        error_message=payload.get("error_message"),
        env_vars=env_vars,
    )


class ChannelService:
    def __init__(
        self,
        serve: HermesServe,
        events: EventBus,
        *,
        runner: CommandRunner,
        health_check: Callable[[], Awaitable[bool]],
    ):
        self.serve = serve
        self.events = events
        self.runner = runner
        self.health_check = health_check

    def _guard(self, channel_id: str) -> None:
        if channel_id == "slack":
            raise APIError(403, "channel_not_managed", "Slack is managed by Hermes")
        if channel_id in EXCLUDED_CHANNELS:
            raise APIError(404, "channel_not_found", f"Channel not found: {channel_id}")

    async def list(self) -> list[Channel]:
        payload = await self.serve.request("GET", "/api/messaging/platforms")
        platforms = payload.get("platforms") if isinstance(payload, dict) else None
        if not isinstance(platforms, list):
            raise APIError(502, "hermes_dashboard_error", "Unexpected messaging catalog shape")
        return [
            _channel_from_payload(entry)
            for entry in platforms
            if isinstance(entry, dict) and entry.get("id") not in EXCLUDED_CHANNELS
        ]

    async def status(self, channel_id: str) -> Channel:
        self._guard(channel_id)
        payload = await self.serve.request("POST", f"/api/messaging/platforms/{channel_id}/test")
        if not isinstance(payload, dict):
            raise APIError(502, "hermes_dashboard_error", "Unexpected channel status shape")
        return _channel_from_payload(payload)

    async def update(self, channel_id: str, request: ChannelUpdate) -> tuple[Channel, bool]:
        self._guard(channel_id)
        env = {key: value.strip() for key, value in request.env.items() if value.strip()}
        clear_env = sorted(set(request.clear_env))
        if not env and not clear_env and request.enabled is None:
            raise APIError(422, "empty_channel_update", "Provide env, clear_env, or enabled")
        body: dict[str, Any] = {"env": env, "clear_env": clear_env}
        if request.enabled is not None:
            body["enabled"] = request.enabled
        await self.serve.request("PUT", f"/api/messaging/platforms/{channel_id}", json=body)

        # The gateway loads config.yaml/.env once at startup — every mutation
        # needs a restart to take effect (no hot reload exists in Hermes).
        try:
            await restart_gateway(self.runner, self.health_check)
        except RuntimeError as exc:
            raise APIError(
                502,
                "gateway_restart_failed",
                "Channel saved but Hermes gateway restart failed",
            ) from exc
        channel = await self.status(channel_id)
        # Adapters reconnect asynchronously after the gateway boots; give the
        # fresh state a short window to settle so the response isn't a stale
        # "pending_restart" the UI would have to poll away itself.
        for _ in range(10):
            if channel.state not in ("pending_restart", "gateway_stopped") or not channel.enabled:
                break
            await asyncio.sleep(1)
            channel = await self.status(channel_id)
        await self.events.publish(
            "channel_updated", {"id": channel.id, "state": channel.state}
        )
        return channel, True
