"""In-memory contract server for SwiftUI development."""

from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import uvicorn
from fastapi import FastAPI, Query, Request, status
from fastapi.responses import StreamingResponse

from botterd.auth import BearerAuthMiddleware
from botterd.config import mock_token
from botterd.credentials import CURATED, SLACK_KEY, integration_kind_for
from botterd.errors import APIError, install_error_handlers
from botterd.events import EventBus, sse_frame
from botterd.google_auth import GOOGLE_CODE_INSTRUCTIONS, GOOGLE_KEY
from botterd.mcp import PRESETS
from botterd.models import (
    Approval,
    ApprovalDecision,
    ApprovalResponse,
    ApprovalsResponse,
    AuthorizationResponse,
    Bot,
    BotCreate,
    BotDetail,
    BotPatch,
    BotResponse,
    BotRosterItem,
    BotsResponse,
    ChatRequest,
    Authorization,
    GoogleConnect,
    McpAuthorization,
    McpAuthorizationResponse,
    McpServer,
    McpServerResponse,
    McpServersResponse,
    McpServerUpdate,
    DeleteResponse,
    Execution,
    ExecutionsResponse,
    HealthResponse,
    ImageAttachment,
    Integration,
    IntegrationResponse,
    IntegrationsResponse,
    IntegrationUpdate,
    MemoryResponse,
    MessagesResponse,
    NormalizedMessage,
    ReadRequest,
    ReadResponse,
    Routine,
    RoutineCreate,
    RoutinePatch,
    RoutineResponse,
    RoutineRunResponse,
    RoutinesResponse,
    SearchResponse,
    Session,
    SessionCreate,
    SessionResponse,
    SessionsResponse,
    StopResponse,
    TaskItem,
)
from botterd.logging import configure_logging


PALETTE = ["#2EC7A6", "#E8833A", "#8B5CF6", "#3B82F6", "#EF4444", "#22C55E"]
# Glyph names come from the app's bundled otter set (SPEC §5):
# float swim dive stand sprawl peek groom shell wave raft
ROSTER = [
    ("sales-outbound", "Sales Outbound", "Pipeline Development", "Builds qualified prospect lists and drafts focused outreach.", "dive"),
    ("inbox-manager", "Inbox Manager", "Communications Triage", "Sorts incoming mail and prepares concise replies.", "shell"),
    ("expense-manager", "Expense Manager", "Finance Operations", "Reviews expenses and keeps records organized.", "sprawl"),
    ("talent-scout", "Talent Scout", "Recruiting", "Finds strong candidates and prepares outreach briefs.", "peek"),
    ("chief-of-staff", "Chief of Staff", "Executive Operations", "Turns priorities into plans, follow-ups, and decisions.", "stand"),
    ("research-analyst", "Research Analyst", "Strategic Research", "Investigates questions and produces source-backed findings.", "swim"),
]


# (key, label, description, url, category, is_set, is_password, advanced)
MOCK_INTEGRATIONS = [
    ("BRAVE_API_KEY", "Brave API key", "Brave Search API key for web search.", "https://brave.com/search/api/", "tool", True, True, False),
    ("NOTION_API_KEY", "Notion API key", "Notion integration token.", "https://www.notion.so/my-integrations", "tool", False, True, False),
    ("ELEVENLABS_API_KEY", "ElevenLabs API key", "Text-to-speech voice generation.", "https://elevenlabs.io", "tool", False, True, False),
    ("ANTHROPIC_API_KEY", "Anthropic API key", "Claude model provider key.", "https://console.anthropic.com", "provider", True, True, True),
    ("GEMINI_API_KEY", "Gemini API key", "Google AI Studio API key.", "https://aistudio.google.com/app/apikey", "provider", False, True, True),
    ("WEATHER_UNITS", "Weather Units", "Preferred units for the weather skill.", None, "setting", False, False, True),
]


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class MockState:
    bots: dict[str, Bot] = field(default_factory=dict)
    sessions: dict[str, Session] = field(default_factory=dict)
    messages: dict[str, list[NormalizedMessage]] = field(default_factory=dict)
    routines: dict[str, Routine] = field(default_factory=dict)
    approvals: dict[str, Approval] = field(default_factory=dict)
    read_markers: dict[str, str | None] = field(default_factory=dict)
    executions: dict[str, list[Execution]] = field(default_factory=dict)
    mcp_servers: dict[str, McpServer] = field(default_factory=dict)
    mcp_flows: dict[str, str] = field(default_factory=dict)
    # Polls taken on each flow, so the mock can walk the real phase sequence.
    mcp_flow_polls: dict[str, int] = field(default_factory=dict)
    integrations: dict[str, Integration] = field(default_factory=dict)
    events: EventBus = field(default_factory=EventBus)

    @classmethod
    def canned(cls) -> "MockState":
        state = cls()
        timestamp = now()
        for index, (slug, name, title, description, glyph) in enumerate(ROSTER):
            bot_id = f"bot-{index + 1}"
            session_id = f"session-{index + 1}"
            bot = Bot(
                id=bot_id,
                slug=slug,
                display_name=name,
                title=title,
                description=description,
                avatar_color=PALETTE[index],
                avatar_glyph=glyph,
                approval_boundary="Ask before sending, purchasing, deleting, or changing external systems.",
                default_session_id=session_id,
                archived=False,
                created_at=timestamp - timedelta(days=30 - index),
                updated_at=timestamp - timedelta(minutes=index * 7),
            )
            state.bots[bot_id] = bot
            state.sessions[session_id] = Session(
                id=session_id,
                bot_id=bot_id,
                title=f"{name} main",
                model="mock/botter-1",
                message_count=1,
                created_at=bot.created_at,
                updated_at=bot.updated_at,
            )
            state.messages[session_id] = [
                NormalizedMessage(
                    id=f"message-{index + 1}",
                    session_id=session_id,
                    bot_id=bot_id,
                    role="assistant",
                    kind="text",
                    text=(
                        "I pulled a focused list of accounts for review."
                        if index == 0 else f"{name} is ready for the next request."
                    ),
                    created_at=bot.updated_at,
                )
            ]
        routine = Routine(
            id="routine-1",
            bot_id="bot-1",
            name="Overnight outbound",
            schedule="0 2 * * *",
            prompt="Build tomorrow's outbound prospect list.",
            paused=False,
            state="scheduled",
            last_run_at=timestamp - timedelta(days=1),
            last_status="ok",
            next_run_at=timestamp + timedelta(hours=8),
        )
        state.routines[routine.id] = routine
        state.executions[routine.id] = [
            Execution(
                id="execution-1",
                routine_id=routine.id,
                started_at=timestamp - timedelta(days=1, minutes=3),
                finished_at=timestamp - timedelta(days=1),
                status="completed",
                summary="Prepared 52 qualified accounts.",
            )
        ]
        # The curated apps are ordinary credential rows now. They differ only
        # by their overlay metadata, which the app uses to group and rank them.
        curated_set = {
            "GITHUB_TOKEN", "OPENROUTER_API_KEY", "EXA_API_KEY", "SUPABASE_ACCESS_TOKEN",
        }
        for key, curated in CURATED.items():
            is_set = key in curated_set
            state.integrations[key] = Integration(
                key=key,
                label=curated.label if curated.required else key.replace("_", " ").title(),
                description=f"{curated.label} credential for every Botter bot.",
                category="tool",
                kind="integration",
                is_set=is_set,
                redacted_value="••••5678" if is_set else None,
                is_password=True,
                status="connected" if is_set else "not_connected",
                detail=(
                    "Configured for every Botter bot; not externally verified."
                    if is_set
                    else "Not configured for Botter bots."
                ),
                sync_status="synced" if is_set else None,
                sync_detail="Available to every Botter bot." if is_set else None,
                group=curated.group,
                group_label=curated.label,
                required=curated.required,
                restart_after_write=curated.restart,
            )
        state.integrations[SLACK_KEY] = Integration(
            key=SLACK_KEY,
            label="Slack",
            description="Main's own Slack agent.",
            category="tool",
            is_set=True,
            is_password=False,
            status="connected",
            detail="Configured in the main Hermes profile; managed by Hermes.",
            group="slack",
            group_label="Slack",
            auth="external",
        )
        state.integrations[GOOGLE_KEY] = Integration(
            key=GOOGLE_KEY,
            label="Google",
            description="Gmail, Calendar, and Drive through the Hermes google-workspace skill.",
            category="tool",
            is_set=False,
            is_password=False,
            status="not_connected",
            detail="No Google token is configured for Botter bots.",
            group="google",
            group_label="Google",
            auth="oauth",
        )
        for key, label, description, url, category, is_set, is_password, advanced in MOCK_INTEGRATIONS:
            state.integrations[key] = Integration(
                key=key,
                label=label,
                description=description,
                url=url,
                category=category,
                kind=integration_kind_for(
                    key,
                    {"is_password": is_password, "url": url, "advanced": advanced, "custom": False},
                ),
                is_set=is_set,
                redacted_value="••••5678" if is_set else None,
                is_password=is_password,
                advanced=advanced,
            )
        return state

    def bot(self, bot_id: str) -> Bot:
        bot = self.bots.get(bot_id)
        if bot is None:
            raise APIError(404, "bot_not_found", f"Bot not found: {bot_id}")
        return bot

    def session(self, session_id: str) -> Session:
        session = self.sessions.get(session_id)
        if session is None:
            raise APIError(404, "session_not_found", f"Session not found: {session_id}")
        return session

    def routine(self, routine_id: str) -> Routine:
        routine = self.routines.get(routine_id)
        if routine is None:
            raise APIError(404, "routine_not_found", f"Routine not found: {routine_id}")
        return routine


def create_app(*, token: str | None = None) -> FastAPI:
    state_store = MockState.canned()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async def ticker() -> None:
            while True:
                await asyncio.sleep(2)
                await state_store.events.publish("feed_updated", {"bot_id": None})

        task = asyncio.create_task(ticker(), name="mock-feed-events")
        app.state.mock = state_store
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="botterd mock", version="0.1.0", lifespan=lifespan)
    app.state.mock = state_store
    app.add_middleware(BearerAuthMiddleware, token=token or mock_token())
    install_error_handlers(app)

    @app.get("/v1/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            version="0.1.0-mock",
            hermes={"reachable": True, "status": "mock", "version": "mock"},
        )

    @app.get("/v1/bots", response_model=BotsResponse)
    async def bots(request: Request) -> BotsResponse:
        state: MockState = request.app.state.mock
        roster = []
        for bot in state.bots.values():
            history = state.messages.get(bot.default_session_id or "", [])
            latest = history[-1] if history else None
            marker = state.read_markers.get(bot.default_session_id or "")
            unread = 0 if marker == (latest.id if latest else None) else sum(message.role == "assistant" for message in history)
            roster.append(
                BotRosterItem(
                    **bot.model_dump(),
                    latest_message_preview=latest.text[:160] if latest else None,
                    latest_message_at=latest.created_at if latest else None,
                    unread_count=unread,
                )
            )
        return BotsResponse(bots=roster)

    @app.post("/v1/bots", response_model=BotResponse, status_code=status.HTTP_201_CREATED)
    async def create_bot(body: BotCreate, request: Request) -> BotResponse:
        state: MockState = request.app.state.mock
        if any(bot.slug == body.slug for bot in state.bots.values()):
            raise APIError(409, "slug_exists", f"A bot already uses slug: {body.slug}")
        from botterd.registry import validate_slug

        validate_slug(body.slug)
        bot_id = f"bot-{uuid.uuid4().hex[:8]}"
        session_id = f"session-{uuid.uuid4().hex[:8]}"
        timestamp = now()
        bot = Bot(
            **body.model_dump(exclude={"model"}), id=bot_id, default_session_id=session_id,
            archived=False, created_at=timestamp, updated_at=timestamp,
        )
        state.bots[bot_id] = bot
        state.sessions[session_id] = Session(
            id=session_id, bot_id=bot_id, title=f"{bot.display_name} main", model=body.model or "mock/botter-1",
            created_at=timestamp, updated_at=timestamp,
        )
        state.messages[session_id] = []
        await state.events.publish("bot_updated", {"bot_id": bot_id})
        return BotResponse(bot=bot)

    @app.get("/v1/bots/{bot_id}", response_model=BotResponse)
    async def bot_detail(bot_id: str, request: Request) -> BotResponse:
        state: MockState = request.app.state.mock
        bot = state.bot(bot_id)
        return BotResponse(
            bot=BotDetail(
                **bot.model_dump(),
                memory_summary=f"Working memory for {bot.display_name}.",
                routine_count=sum(routine.bot_id == bot_id for routine in state.routines.values()),
            )
        )

    @app.patch("/v1/bots/{bot_id}", response_model=BotResponse)
    async def patch_bot(bot_id: str, body: BotPatch, request: Request) -> BotResponse:
        state: MockState = request.app.state.mock
        bot = state.bot(bot_id)
        updated = bot.model_copy(update={**body.model_dump(exclude_none=True), "updated_at": now()})
        state.bots[bot_id] = updated
        await state.events.publish("bot_updated", {"bot_id": bot_id})
        return BotResponse(bot=updated)

    @app.delete("/v1/bots/{bot_id}", response_model=DeleteResponse)
    async def delete_bot(bot_id: str, request: Request, purge: bool = False) -> DeleteResponse:
        state: MockState = request.app.state.mock
        bot = state.bot(bot_id)
        if purge:
            state.bots.pop(bot_id)
            removed_sessions = [item.id for item in state.sessions.values() if item.bot_id == bot_id]
            for session_id in removed_sessions:
                state.sessions.pop(session_id, None)
                state.messages.pop(session_id, None)
                state.read_markers.pop(session_id, None)
            removed_routines = [item.id for item in state.routines.values() if item.bot_id == bot_id]
            for routine_id in removed_routines:
                state.routines.pop(routine_id, None)
                state.executions.pop(routine_id, None)
            for run_id in [item.run_id for item in state.approvals.values() if item.bot_id == bot_id]:
                state.approvals.pop(run_id, None)
            return DeleteResponse(id=bot_id, purged=True)
        state.bots[bot_id] = bot.model_copy(update={"archived": True, "updated_at": now()})
        return DeleteResponse(id=bot_id, archived=True)

    @app.get("/v1/bots/{bot_id}/sessions", response_model=SessionsResponse)
    async def sessions(bot_id: str, request: Request) -> SessionsResponse:
        state: MockState = request.app.state.mock
        state.bot(bot_id)
        return SessionsResponse(sessions=[item for item in state.sessions.values() if item.bot_id == bot_id])

    @app.post("/v1/bots/{bot_id}/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
    async def create_session(bot_id: str, body: SessionCreate, request: Request) -> SessionResponse:
        state: MockState = request.app.state.mock
        bot = state.bot(bot_id)
        session_id = f"session-{uuid.uuid4().hex[:8]}"
        timestamp = now()
        session = Session(
            id=session_id, bot_id=bot_id, title=body.title or f"{bot.display_name} conversation",
            model=body.model or "mock/botter-1", created_at=timestamp, updated_at=timestamp,
        )
        state.sessions[session_id] = session
        state.messages[session_id] = []
        return SessionResponse(session=session)

    @app.get("/v1/sessions/{session_id}/messages", response_model=MessagesResponse)
    async def messages(
        session_id: str, request: Request, before: str | None = None,
        limit: int = Query(50, ge=1, le=200),
    ) -> MessagesResponse:
        state: MockState = request.app.state.mock
        state.session(session_id)
        history = state.messages[session_id]
        if before:
            index = next((index for index, message in enumerate(history) if message.id == before), None)
            if index is None:
                raise APIError(400, "invalid_cursor", f"Message cursor not found: {before}")
            history = history[:index]
        page = history[-limit:]
        return MessagesResponse(messages=page, has_more=len(history) > len(page))

    @app.post("/v1/sessions/{session_id}/chat")
    async def chat(session_id: str, body: ChatRequest, request: Request) -> StreamingResponse:
        state: MockState = request.app.state.mock
        session = state.session(session_id)
        run_id = f"run_{uuid.uuid4().hex}"
        raw_message = body.model_dump(mode="json")["message"]
        message_text = raw_message if isinstance(raw_message, str) else "\n".join(
            str(part.get("text") or "")
            for part in raw_message
            if part.get("type") == "text"
        ).strip()
        attachments = [] if isinstance(raw_message, str) else [
            ImageAttachment(
                url=part["image_url"]["url"],
                media_type=part["image_url"]["url"][5:].split(";", 1)[0].lower(),
            )
            for part in raw_message
            if part.get("type") == "image_url"
        ]
        user_message = NormalizedMessage(
            id=f"message-{uuid.uuid4().hex[:8]}", session_id=session_id, bot_id=session.bot_id,
            role="user", kind="attachment" if attachments else "text", text=message_text,
            attachments=attachments, created_at=now(),
        )
        state.messages[session_id].append(user_message)

        async def scripted():
            lower = message_text.lower()
            if "approval" in lower or "send" in lower:
                approval = Approval(
                    run_id=run_id, bot_id=session.bot_id, session_id=session_id,
                    summary="Send the prepared outreach message", requested_at=now(),
                )
                state.approvals[run_id] = approval
                approval_event = json.loads(approval.model_dump_json())
                approval_event.pop("session_id", None)
                await state.events.publish("approval_pending", {"approval": approval_event})
                yield sse_frame("approval_required", {"run_id": run_id, "summary": approval.summary})
                complete = NormalizedMessage(
                    id=f"message-{uuid.uuid4().hex[:8]}", session_id=session_id, bot_id=session.bot_id,
                    role="assistant", kind="approval_request", text=approval.summary, created_at=now(),
                )
            elif "task" in lower or "report" in lower:
                yield sse_frame("tool_event", {"name": "salesforce", "status": "started", "summary": "Pull qualified accounts"})
                await asyncio.sleep(0.01)
                yield sse_frame("tool_event", {"name": "salesforce", "status": "ok", "summary": "52 accounts"})
                complete = NormalizedMessage(
                    id=f"message-{uuid.uuid4().hex[:8]}", session_id=session_id, bot_id=session.bot_id,
                    role="assistant", kind="task_report", text="Outbound research is complete.",
                    task_items=[TaskItem(label="Salesforce → list pulled", detail="52 accounts", state="done")],
                    created_at=now(),
                )
            else:
                pieces = ["I checked ", "the latest context ", "and prepared a concise answer."]
                for piece in pieces:
                    yield sse_frame("delta", {"text": piece})
                    await asyncio.sleep(0.01)
                complete = NormalizedMessage(
                    id=f"message-{uuid.uuid4().hex[:8]}", session_id=session_id, bot_id=session.bot_id,
                    role="assistant", kind="text", text="".join(pieces), created_at=now(),
                )
            state.messages[session_id].append(complete)
            state.sessions[session_id] = session.model_copy(
                update={"message_count": len(state.messages[session_id]), "updated_at": now()}
            )
            yield sse_frame("message_complete", {"message": json.loads(complete.model_dump_json())})
            await state.events.publish("feed_updated", {"bot_id": session.bot_id})

        return StreamingResponse(
            scripted(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/sessions/{session_id}/read", response_model=ReadResponse)
    async def mark_read(session_id: str, body: ReadRequest, request: Request) -> ReadResponse:
        state: MockState = request.app.state.mock
        state.session(session_id)
        message_id = body.message_id or (state.messages[session_id][-1].id if state.messages[session_id] else None)
        state.read_markers[session_id] = message_id
        return ReadResponse(session_id=session_id, last_read_message_id=message_id)

    @app.post("/v1/sessions/{session_id}/stop", response_model=StopResponse)
    async def stop(session_id: str, request: Request) -> StopResponse:
        request.app.state.mock.session(session_id)
        return StopResponse(session_id=session_id, stopped=True)

    @app.get("/v1/bots/{bot_id}/routines", response_model=RoutinesResponse)
    async def routines(bot_id: str, request: Request) -> RoutinesResponse:
        state: MockState = request.app.state.mock
        state.bot(bot_id)
        return RoutinesResponse(routines=[routine for routine in state.routines.values() if routine.bot_id == bot_id])

    @app.post("/v1/bots/{bot_id}/routines", response_model=RoutineResponse, status_code=status.HTTP_201_CREATED)
    async def create_routine(bot_id: str, body: RoutineCreate, request: Request) -> RoutineResponse:
        state: MockState = request.app.state.mock
        state.bot(bot_id)
        routine = Routine(
            id=f"routine-{uuid.uuid4().hex[:8]}", bot_id=bot_id, **body.model_dump(),
            paused=False, state="scheduled", next_run_at=now() + timedelta(hours=1),
        )
        state.routines[routine.id] = routine
        state.executions[routine.id] = []
        return RoutineResponse(routine=routine)

    @app.patch("/v1/routines/{routine_id}", response_model=RoutineResponse)
    async def patch_routine(routine_id: str, body: RoutinePatch, request: Request) -> RoutineResponse:
        state: MockState = request.app.state.mock
        routine = state.routine(routine_id).model_copy(update=body.model_dump(exclude_none=True))
        state.routines[routine_id] = routine
        return RoutineResponse(routine=routine)

    @app.delete("/v1/routines/{routine_id}")
    async def delete_routine(routine_id: str, request: Request) -> dict[str, Any]:
        state: MockState = request.app.state.mock
        state.routine(routine_id)
        state.routines.pop(routine_id)
        state.executions.pop(routine_id, None)
        return {"id": routine_id, "deleted": True}

    @app.post("/v1/routines/{routine_id}/run", response_model=RoutineRunResponse, status_code=status.HTTP_202_ACCEPTED)
    async def run_routine(routine_id: str, request: Request) -> RoutineRunResponse:
        state: MockState = request.app.state.mock
        routine = state.routine(routine_id).model_copy(update={"state": "queued", "next_run_at": now()})
        state.routines[routine_id] = routine
        await state.events.publish(
            "routine_fired", {"bot_id": routine.bot_id, "routine_id": routine.id, "name": routine.name}
        )
        return RoutineRunResponse(routine=routine)

    @app.post("/v1/routines/{routine_id}/pause", response_model=RoutineResponse)
    async def pause(routine_id: str, request: Request) -> RoutineResponse:
        state: MockState = request.app.state.mock
        routine = state.routine(routine_id).model_copy(update={"paused": True, "state": "paused"})
        state.routines[routine_id] = routine
        return RoutineResponse(routine=routine)

    @app.post("/v1/routines/{routine_id}/resume", response_model=RoutineResponse)
    async def resume(routine_id: str, request: Request) -> RoutineResponse:
        state: MockState = request.app.state.mock
        routine = state.routine(routine_id).model_copy(update={"paused": False, "state": "scheduled"})
        state.routines[routine_id] = routine
        return RoutineResponse(routine=routine)

    @app.get("/v1/routines/{routine_id}/executions", response_model=ExecutionsResponse)
    async def executions(
        routine_id: str, request: Request, limit: int = Query(20, ge=1, le=100)
    ) -> ExecutionsResponse:
        state: MockState = request.app.state.mock
        state.routine(routine_id)
        return ExecutionsResponse(executions=state.executions.get(routine_id, [])[:limit])

    @app.get("/v1/approvals", response_model=ApprovalsResponse)
    async def approvals(request: Request) -> ApprovalsResponse:
        return ApprovalsResponse(approvals=list(request.app.state.mock.approvals.values()))

    @app.post("/v1/approvals/{run_id}", response_model=ApprovalResponse)
    async def decide(run_id: str, body: ApprovalDecision, request: Request) -> ApprovalResponse:
        state: MockState = request.app.state.mock
        if state.approvals.pop(run_id, None) is None:
            raise APIError(404, "approval_not_found", f"Pending approval not found: {run_id}")
        await state.events.publish("approval_resolved", {"run_id": run_id, "decision": body.decision})
        return ApprovalResponse(run_id=run_id, decision=body.decision, resolved=True)

    @app.get("/v1/bots/{bot_id}/memory", response_model=MemoryResponse)
    async def memory(bot_id: str, request: Request) -> MemoryResponse:
        bot = request.app.state.mock.bot(bot_id)
        return MemoryResponse(
            bot_id=bot.id,
            memory=f"# {bot.display_name} memory\n\nKeep concise operational context.",
            user="# User\n\nPrefers direct, evidence-backed updates.",
        )

    @app.get("/v1/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            request.app.state.mock.events.stream(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post(
        "/v1/auth/google",
        response_model=IntegrationResponse | AuthorizationResponse,
    )
    async def connect_google(
        request: Request,
        body: GoogleConnect | None = None,
    ) -> IntegrationResponse | AuthorizationResponse:
        state: MockState = request.app.state.mock
        body = body or GoogleConnect()
        row = state.integrations[GOOGLE_KEY]
        if body.code is not None:
            if not body.code.strip():
                raise APIError(422, "google_auth_failed", "Paste the full redirect URL first")
            updated = row.model_copy(
                update={
                    "status": "connected",
                    "is_set": True,
                    "detail": "Token configured (mock).",
                    "sync_status": "synced",
                    "sync_detail": "Available to every Botter bot.",
                }
            )
            state.integrations[GOOGLE_KEY] = updated
            await state.events.publish(
                "integration_updated", {"key": updated.key, "is_set": updated.is_set}
            )
            return IntegrationResponse(integration=updated)
        return AuthorizationResponse(
            authorization=Authorization(
                url="https://accounts.google.com/o/oauth2/mock",
                instructions=GOOGLE_CODE_INSTRUCTIONS,
                code_entry=True,
            )
        )

    @app.delete("/v1/auth/google", response_model=IntegrationResponse)
    async def disconnect_google(request: Request) -> IntegrationResponse:
        state: MockState = request.app.state.mock
        updated = state.integrations[GOOGLE_KEY].model_copy(
            update={
                "status": "not_connected",
                "is_set": False,
                "detail": "Removed from every Botter bot.",
                "sync_status": None,
                "sync_detail": None,
            }
        )
        state.integrations[GOOGLE_KEY] = updated
        await state.events.publish(
            "integration_updated", {"key": updated.key, "is_set": updated.is_set}
        )
        return IntegrationResponse(integration=updated)

    @app.get("/v1/integrations", response_model=IntegrationsResponse)
    async def integrations(request: Request) -> IntegrationsResponse:
        state: MockState = request.app.state.mock
        return IntegrationsResponse(integrations=list(state.integrations.values()))

    def guard_integration(key: str) -> None:
        # Google has its own /v1/auth/google flow; Slack is Hermes-owned.
        if key in (SLACK_KEY, GOOGLE_KEY):
            raise APIError(403, "integration_not_managed", f"{key} is managed elsewhere")

    @app.put("/v1/integrations/{key}", response_model=IntegrationResponse)
    async def put_integration(key: str, body: IntegrationUpdate, request: Request) -> IntegrationResponse:
        state: MockState = request.app.state.mock
        guard_integration(key)
        if not body.value.strip():
            raise APIError(422, "integration_value_required", "value must not be empty")
        existing = state.integrations.get(key)
        if existing is None:
            row = {"custom": True, "is_password": True, "url": None, "advanced": False}
            existing = Integration(
                key=key,
                label=key.replace("_", " ").title(),
                category="custom",
                kind=integration_kind_for(key, row),
                custom=True,
            )
        updated = existing.model_copy(
            update={
                "is_set": True,
                "redacted_value": "••••5678",
                "status": "connected",
                "detail": "Configured for every Botter bot; not externally verified.",
                "sync_status": "synced" if existing.kind == "integration" else None,
                "sync_detail": "Available to every Botter bot." if existing.kind == "integration" else None,
            }
        )
        state.integrations[key] = updated
        await state.events.publish("integration_updated", {"key": key, "is_set": True})
        return IntegrationResponse(integration=updated)

    @app.delete("/v1/integrations/{key}", response_model=IntegrationResponse)
    async def delete_integration(key: str, request: Request) -> IntegrationResponse:
        state: MockState = request.app.state.mock
        guard_integration(key)
        existing = state.integrations.get(key)
        if existing is None or not existing.is_set:
            raise APIError(404, "integration_not_found", f"{key} is not set")
        updated = existing.model_copy(
            update={
                "is_set": False,
                "redacted_value": None,
                "status": "not_connected",
                "detail": "Removed from every Botter bot.",
            }
        )
        if existing.custom:
            state.integrations.pop(key)
        else:
            state.integrations[key] = updated
        await state.events.publish("integration_updated", {"key": key, "is_set": False})
        return IntegrationResponse(integration=updated)

    def mcp_presets(state: MockState) -> list[McpServer]:
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
            if preset.name not in state.mcp_servers
        ]

    @app.get("/v1/mcp", response_model=McpServersResponse)
    async def mcp_list(request: Request) -> McpServersResponse:
        state: MockState = request.app.state.mock
        return McpServersResponse(
            servers=list(state.mcp_servers.values()), presets=mcp_presets(state)
        )

    @app.put("/v1/mcp/{name}", response_model=McpServerResponse)
    async def mcp_put(name: str, body: McpServerUpdate, request: Request) -> McpServerResponse:
        state: MockState = request.app.state.mock
        if not body.url.startswith("https://"):
            raise APIError(422, "invalid_mcp_url", "The server URL must start with https://")
        preset = next((item for item in PRESETS if item.url == body.url or item.name == name), None)
        server = McpServer(
            name=name,
            label=preset.label if preset else name.replace("-", " ").title(),
            description=preset.description if preset else "",
            url=body.url,
            auth=body.auth,
            preset=preset.name if preset else None,
            docs_url=preset.docs_url if preset else None,
            authorized=body.auth != "oauth",
            status="not_connected" if body.auth == "oauth" else "connected",
            detail=(
                "Authorization is required before bots can use it."
                if body.auth == "oauth"
                else "Available to every Botter bot (mock)."
            ),
            sync_status="synced",
            sync_detail="Available to every Botter bot.",
        )
        state.mcp_servers[name] = server
        await state.events.publish("mcp_updated", {"name": name, "status": server.status})
        return McpServerResponse(server=server, restarted=True)

    @app.delete("/v1/mcp/{name}", response_model=McpServerResponse)
    async def mcp_delete(name: str, request: Request) -> McpServerResponse:
        state: MockState = request.app.state.mock
        server = state.mcp_servers.pop(name, None)
        if server is None:
            raise APIError(404, "mcp_server_not_found", f"MCP server not found: {name}")
        removed = server.model_copy(
            update={
                "status": "not_connected",
                "sync_status": None,
                "sync_detail": None,
                "detail": "Removed from every Botter bot.",
            }
        )
        await state.events.publish("mcp_updated", {"name": name, "status": removed.status})
        return McpServerResponse(server=removed, restarted=True)

    @app.post("/v1/mcp/{name}/authorize", response_model=McpAuthorizationResponse)
    async def mcp_authorize(name: str, request: Request) -> McpAuthorizationResponse:
        state: MockState = request.app.state.mock
        if name not in state.mcp_servers:
            raise APIError(404, "mcp_server_not_found", f"MCP server not found: {name}")
        state.mcp_flows[f"flow-{name}"] = name
        state.mcp_flow_polls[f"flow-{name}"] = 0
        return McpAuthorizationResponse(
            authorization=McpAuthorization(
                flow_id=f"flow-{name}",
                server=name,
                status="authorization_required",
                url="https://login.composio.dev/authorize?mock=1",
                instructions=(
                    "Sign in and approve access in your browser. This window updates by "
                    "itself when the provider confirms it."
                ),
            )
        )

    @app.get("/v1/mcp/authorizations/{flow_id}", response_model=McpAuthorizationResponse)
    async def mcp_authorization_status(flow_id: str, request: Request) -> McpAuthorizationResponse:
        state: MockState = request.app.state.mock
        name = state.mcp_flows.get(flow_id)
        if name is None:
            raise APIError(404, "mcp_flow_not_found", "OAuth flow not found or expired")
        # Walk the real phase sequence — waiting on the browser, then botterd's
        # own fan-out and gateway restart — so every UI phase is reachable here.
        # Each poll advances one step, rather than waiting on a real clock.
        polls = state.mcp_flow_polls.get(flow_id, 0) + 1
        state.mcp_flow_polls[flow_id] = polls
        if polls < 3:
            return McpAuthorizationResponse(
                authorization=McpAuthorization(
                    flow_id=flow_id,
                    server=name,
                    status="authorization_required" if polls < 2 else "finishing",
                    url="https://login.composio.dev/authorize?mock=1" if polls < 2 else None,
                    instructions=(
                        "Sign in and approve access in your browser. This window updates "
                        "by itself when the provider confirms it."
                        if polls < 2
                        else "Approved. Copying the grant to your bots and restarting "
                        "Hermes so they can use it."
                    ),
                ),
            )
        server = state.mcp_servers[name].model_copy(
            update={
                "authorized": True,
                "status": "connected",
                "detail": "Available to every Botter bot (mock).",
            }
        )
        state.mcp_servers[name] = server
        await state.events.publish("mcp_updated", {"name": name, "status": server.status})
        return McpAuthorizationResponse(
            authorization=McpAuthorization(
                flow_id=flow_id, server=name, status="approved", url=None
            ),
            server_state=server,
        )

    @app.get("/v1/search", response_model=SearchResponse)
    async def search(
        request: Request, q: str = Query(min_length=1), bot_id: str | None = None
    ) -> SearchResponse:
        state: MockState = request.app.state.mock
        if bot_id is not None:
            state.bot(bot_id)
        query = q.casefold()
        found = [
            message
            for history in state.messages.values()
            for message in history
            if query in message.text.casefold() and (bot_id is None or message.bot_id == bot_id)
        ]
        return SearchResponse(messages=found)

    return app


app = create_app()


def run() -> None:
    configure_logging()
    uvicorn.run("mockserver.main:app", host="127.0.0.1", port=8674, log_config=None)
