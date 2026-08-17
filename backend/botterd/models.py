"""Shared public HTTP contract models for botterd and the mock server."""

from __future__ import annotations

import base64
import binascii
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


AVATAR_PALETTE = frozenset(
    {"#2EC7A6", "#E8833A", "#8B5CF6", "#3B82F6", "#EF4444", "#22C55E", "#EAB308", "#EC4899"}
)
SUPPORTED_IMAGE_MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})
MAX_IMAGE_BYTES = 5_000_000
MAX_IMAGE_DATA_URL_LENGTH = 6_700_100


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(ContractModel):
    code: str
    message: str


class ErrorResponse(ContractModel):
    error: ErrorDetail


class HealthResponse(ContractModel):
    status: Literal["ok", "degraded"]
    version: str
    hermes: dict[str, Any]


class TaskItem(ContractModel):
    label: str
    detail: str = ""
    # `note` is what the agent said it would do; the other states are tool steps.
    state: Literal["done", "failed", "running", "note"]


class RoutineReference(ContractModel):
    id: str
    name: str


class ImageAttachment(ContractModel):
    type: Literal["image"] = "image"
    url: str
    media_type: str
    filename: str | None = None


class NormalizedMessage(ContractModel):
    id: str
    session_id: str
    bot_id: str
    role: Literal["user", "assistant", "system"]
    kind: Literal["text", "task_report", "routine_created", "approval_request", "attachment"] = "text"
    text: str = ""
    attachments: list[ImageAttachment] = Field(default_factory=list)
    task_items: list[TaskItem] = Field(default_factory=list)
    routine: RoutineReference | None = None
    created_at: datetime


class BotBase(ContractModel):
    slug: str
    display_name: str
    title: str
    description: str
    avatar_color: str
    avatar_glyph: str
    approval_boundary: str

    @field_validator("avatar_color")
    @classmethod
    def validate_color(cls, value: str) -> str:
        if len(value) != 7 or not value.startswith("#"):
            raise ValueError("avatar_color must be a #RRGGBB value")
        try:
            int(value[1:], 16)
        except ValueError as exc:
            raise ValueError("avatar_color must be a #RRGGBB value") from exc
        normalized = value.upper()
        if normalized not in AVATAR_PALETTE:
            raise ValueError("avatar_color must be from the Botter palette")
        return normalized

    @field_validator("avatar_glyph")
    @classmethod
    def validate_glyph(cls, value: str) -> str:
        if not value or any(not (character.isascii() and (character.isalnum() or character in ".-")) for character in value):
            raise ValueError("avatar_glyph must be an icon name, not an emoji")
        return value


def validate_description_text(value: str) -> str:
    """The description is the only free-text role definition in SOUL.md and the
    text Hermes' kanban orchestrator routes on — an empty one renders a persona
    that says nothing, so writes reject it. Reads stay permissive for rows
    written before this rule existed."""
    text = value.strip()
    if not text:
        raise ValueError("description must describe the role in operational terms")
    return text


class BotCreate(BotBase):
    model: str | None = None

    @field_validator("description")
    @classmethod
    def validate_required_description(cls, value: str) -> str:
        return validate_description_text(value)


class BotPatch(ContractModel):
    display_name: str | None = None
    title: str | None = None
    description: str | None = None
    avatar_color: str | None = None
    avatar_glyph: str | None = None
    approval_boundary: str | None = None
    archived: bool | None = None

    @field_validator("description")
    @classmethod
    def validate_optional_description(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return validate_description_text(value)

    @field_validator("avatar_color")
    @classmethod
    def validate_optional_color(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return BotBase.validate_color(value)

    @field_validator("avatar_glyph")
    @classmethod
    def validate_optional_glyph(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return BotBase.validate_glyph(value)


class Bot(BotBase):
    id: str
    default_session_id: str | None = None
    archived: bool = False
    created_at: datetime
    updated_at: datetime


class BotRosterItem(Bot):
    latest_message_preview: str | None = None
    latest_message_at: datetime | None = None
    unread_count: int = 0


class BotDetail(Bot):
    memory_summary: str = ""
    routine_count: int = 0


class BotsResponse(ContractModel):
    bots: list[BotRosterItem]


class BotResponse(ContractModel):
    bot: Bot | BotDetail


class SessionCreate(ContractModel):
    title: str | None = None
    model: str | None = None


class Session(ContractModel):
    id: str
    bot_id: str
    title: str | None = None
    model: str | None = None
    message_count: int = 0
    created_at: datetime
    updated_at: datetime


class SessionsResponse(ContractModel):
    sessions: list[Session]


class SessionResponse(ContractModel):
    session: Session


class MessagesResponse(ContractModel):
    messages: list[NormalizedMessage]
    has_more: bool = False


class ChatTextPart(ContractModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=200_000)


class ChatImageURL(ContractModel):
    url: str = Field(max_length=MAX_IMAGE_DATA_URL_LENGTH)
    detail: Literal["auto", "low", "high"] = "auto"

    @field_validator("url")
    @classmethod
    def validate_inline_image(cls, value: str) -> str:
        prefix, separator, payload = value.partition(",")
        if not separator or not prefix.lower().startswith("data:image/") or not prefix.lower().endswith(";base64"):
            raise ValueError("image attachments must be base64 data URLs")
        media_type = prefix[5:].split(";", 1)[0].lower()
        if media_type not in SUPPORTED_IMAGE_MEDIA_TYPES:
            raise ValueError("image attachments must be PNG, JPEG, GIF, or WebP")
        try:
            decoded = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image attachment contains invalid base64 data") from exc
        if not decoded or len(decoded) > MAX_IMAGE_BYTES:
            raise ValueError("image attachments must be between 1 byte and 5 MB")
        signatures = {
            "image/png": decoded.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/jpeg": decoded.startswith(b"\xff\xd8\xff"),
            "image/gif": decoded.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": len(decoded) >= 12 and decoded.startswith(b"RIFF") and decoded[8:12] == b"WEBP",
        }
        if not signatures[media_type]:
            raise ValueError("image attachment bytes do not match its media type")
        return value


class ChatImagePart(ContractModel):
    type: Literal["image_url"]
    image_url: ChatImageURL


class ChatRequest(ContractModel):
    message: str | list[ChatTextPart | ChatImagePart]

    @field_validator("message")
    @classmethod
    def validate_visible_message(
        cls, value: str | list[ChatTextPart | ChatImagePart]
    ) -> str | list[ChatTextPart | ChatImagePart]:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("message must not be empty")
            return value
        if not value or not any(
            isinstance(part, ChatImagePart) or (isinstance(part, ChatTextPart) and part.text.strip())
            for part in value
        ):
            raise ValueError("message must include text or an image")
        if sum(isinstance(part, ChatImagePart) for part in value) > 1:
            raise ValueError("only one image attachment is supported per message")
        return value


class ReadRequest(ContractModel):
    message_id: str | None = None


class ReadResponse(ContractModel):
    session_id: str
    last_read_message_id: str | None


class StopResponse(ContractModel):
    session_id: str
    stopped: bool


class RoutineCreate(ContractModel):
    name: str
    schedule: str
    prompt: str


class RoutinePatch(ContractModel):
    name: str | None = None
    schedule: str | None = None
    prompt: str | None = None


class Routine(ContractModel):
    id: str
    bot_id: str
    name: str
    schedule: str
    prompt: str
    paused: bool = False
    state: str = "scheduled"
    last_run_at: datetime | None = None
    last_status: str | None = None
    next_run_at: datetime | None = None


class RoutinesResponse(ContractModel):
    routines: list[Routine]


class RoutineResponse(ContractModel):
    routine: Routine


class RoutineRunResponse(ContractModel):
    routine: Routine
    state: Literal["queued"] = "queued"


class Execution(ContractModel):
    id: str
    routine_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str
    summary: str = ""


class ExecutionsResponse(ContractModel):
    executions: list[Execution]


class Approval(ContractModel):
    run_id: str
    bot_id: str
    session_id: str
    summary: str
    requested_at: datetime


class ApprovalsResponse(ContractModel):
    approvals: list[Approval]


class ApprovalDecision(ContractModel):
    decision: Literal["once", "session", "always", "deny"]


class ApprovalResponse(ContractModel):
    run_id: str
    decision: Literal["once", "session", "always", "deny"]
    resolved: bool


class MemoryResponse(ContractModel):
    bot_id: str
    memory: str
    user: str


class SearchResponse(ContractModel):
    messages: list[NormalizedMessage]


class GoogleConnect(ContractModel):
    # An empty object starts the flow. `code` carries the pasted redirect URL
    # (or raw auth code); `client_secret_json` bootstraps a missing OAuth
    # client file.
    code: str | None = None
    client_secret_json: str | None = None


class Authorization(ContractModel):
    url: str | None = None
    instructions: str
    # True when the flow finishes by POSTing back `code` (pasted redirect URL).
    code_entry: bool = False
    # True when the flow needs `client_secret_json` before it can start.
    needs_client_secret: bool = False


class AuthorizationResponse(ContractModel):
    authorization: Authorization


class Integration(ContractModel):
    """One Hermes credential or setting.

    This is the single credential row. It covers the generic env catalog
    (tool/provider/skill/setting/custom keys) and the curated apps that used to
    live behind `/v1/connections`. The curated extras — `group`, `required`,
    `restart_after_write`, `auth` — are display and behaviour that Hermes' own
    catalog does not carry.
    """

    key: str
    label: str
    description: str = ""
    url: str | None = None
    category: str = "custom"
    # "integration" = external-service credential; "config" = plain Hermes
    # setting (timeout, debug flag, path). The app renders them on separate tabs.
    kind: Literal["integration", "config"] = "integration"
    is_set: bool = False
    redacted_value: str | None = None
    is_password: bool = True
    advanced: bool = False
    custom: bool = False
    # Credential integrations are global across Botter-managed profiles.
    # Config rows leave these null because they intentionally remain main-only.
    sync_status: Literal["synced", "out_of_sync"] | None = None
    sync_detail: str | None = None
    # Rolled-up row state. `error` folds in drift and the Google/Slack cases
    # that `is_set` alone cannot express.
    status: Literal["connected", "not_connected", "error"] = "not_connected"
    detail: str | None = None
    # Curated grouping: several keys that the app renders as one card
    # (Vercel = VERCEL_TOKEN + VERCEL_TEAM_ID).
    group: str | None = None
    group_label: str | None = None
    # False marks an optional field inside a group.
    required: bool = True
    # Exa's key is cached in-process, so its write restarts the gateway.
    restart_after_write: bool = False
    # "value" = pasted secret; "oauth" = Google; "external" = read-only row
    # that Hermes owns (Slack).
    auth: Literal["value", "oauth", "external"] = "value"


class IntegrationUpdate(ContractModel):
    value: str


class IntegrationsResponse(ContractModel):
    integrations: list[Integration]


class IntegrationResponse(ContractModel):
    integration: Integration


class McpServer(ContractModel):
    """One MCP server in `mcp_servers` of every Botter-managed config.yaml.

    An MCP server gives bots a whole catalog of tools behind one entry, so it
    needs none of the per-app env plumbing that `Integration` carries.
    """

    name: str
    label: str
    description: str = ""
    # Remote servers carry `url`; stdio servers carry `command`. Botter writes
    # remote servers only, but it reports whatever Hermes already holds.
    url: str | None = None
    command: str | None = None
    auth: Literal["none", "oauth", "header"] = "none"
    enabled: bool = True
    # Set when the entry matches a known preset (for example "composio").
    preset: str | None = None
    docs_url: str | None = None
    # True when Hermes holds an OAuth grant for this server on the main profile.
    authorized: bool = False
    status: Literal["connected", "not_connected", "error"] = "not_connected"
    detail: str | None = None
    sync_status: Literal["synced", "out_of_sync"] | None = None
    sync_detail: str | None = None


class McpServerUpdate(ContractModel):
    url: str
    auth: Literal["none", "oauth", "header"] = "oauth"
    # Header auth only. Values may reference an env key as `${NAME}`, which
    # Hermes expands per profile.
    headers: dict[str, str] = Field(default_factory=dict)


class McpAuthorization(ContractModel):
    """One in-flight MCP OAuth flow, driven by Hermes' dashboard backend.

    `starting` → `authorization_required` (open `url`) → `finishing` → `approved`
    | `error`. The app polls until the flow settles. `finishing` is botterd's own
    step, not the provider's: the grant is copied to every bot and the gateway
    restarts, which takes long enough that it runs in the background instead of
    inside the poll. Only `approved` and `error` are terminal.
    """

    flow_id: str
    server: str
    status: Literal["starting", "authorization_required", "finishing", "approved", "error"]
    url: str | None = None
    instructions: str = ""
    error: str | None = None


class McpAuthorizationResponse(ContractModel):
    authorization: McpAuthorization
    # Present once the flow is approved and the grant has reached every bot.
    # Absent while `finishing`: the bots really are out of sync part-way through
    # the fan-out, and that is not a state worth showing anyone.
    server_state: McpServer | None = None


class McpServersResponse(ContractModel):
    servers: list[McpServer]
    # Presets the app offers as one-click cards.
    presets: list[McpServer] = Field(default_factory=list)


class McpServerResponse(ContractModel):
    server: McpServer
    restarted: bool = False


class DeleteResponse(ContractModel):
    id: str
    archived: bool = False
    purged: bool = False
