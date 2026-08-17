"""FastAPI application for the real Botter companion daemon."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Query, Request, Response, status
from fastapi.responses import StreamingResponse

from . import __version__
from .approvals import ApprovalService
from .auth import BearerAuthMiddleware
from .chat import ChatManager
from .credentials import CredentialService
from .google_auth import GoogleAuthService
from .hermes_serve import HermesServe
from .config import Settings, read_default_model
from .db import Database
from .errors import APIError, error_response, install_error_handlers
from .events import EventBus
from .feed import FeedService, FeedWatcher
from .global_auth import GlobalAuth
from .hermes import HermesClient, HermesError
from .logging import configure_logging
from .mcp import McpService
from .models import (
    ApprovalDecision,
    ApprovalResponse,
    ApprovalsResponse,
    BotCreate,
    BotDetail,
    BotPatch,
    BotResponse,
    BotsResponse,
    ChatRequest,
    Authorization,
    AuthorizationResponse,
    GoogleConnect,
    McpAuthorizationResponse,
    McpServerResponse,
    McpServersResponse,
    McpServerUpdate,
    DeleteResponse,
    ExecutionsResponse,
    HealthResponse,
    IntegrationResponse,
    IntegrationsResponse,
    IntegrationUpdate,
    MemoryResponse,
    MessagesResponse,
    ReadRequest,
    ReadResponse,
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
)
from .normalize import normalize_datetime
from .registry import Registry, SubprocessRunner
from .routines import RoutineService


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _session_model(raw: dict[str, Any], bot_id: str) -> Session:
    created = normalize_datetime(raw.get("started_at"))
    updated = normalize_datetime(raw.get("last_active") or raw.get("started_at"))
    return Session(
        id=str(raw.get("id") or ""),
        bot_id=bot_id,
        title=str(raw["title"]) if raw.get("title") else None,
        model=str(raw["model"]) if raw.get("model") else None,
        message_count=int(raw.get("message_count") or 0),
        created_at=created,
        updated_at=updated,
    )


def create_app(
    settings: Settings | None = None,
    *,
    hermes_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        settings.prepare_state()
        settings.load_or_create_token()
        database = Database(settings.db_path)
        await database.connect()
        client = httpx.AsyncClient(transport=hermes_transport, timeout=httpx.Timeout(30))
        try:
            default_model = read_default_model(settings.hermes_config_path)
            hermes = HermesClient(client, settings.gateway_url, settings.load_api_server_key(), default_model)
            events = EventBus()
            runner = SubprocessRunner()
            serve = HermesServe(settings)
            chats = ChatManager(hermes, database, events)
            routines = RoutineService(settings, database, hermes, events)

            async def gateway_healthy() -> bool:
                try:
                    await hermes.health()
                    return True
                except Exception:
                    return False

            async def auth_busy() -> bool:
                return chats.has_active_runs or await routines.has_active_executions()

            global_auth = GlobalAuth(
                settings,
                database,
                runner,
                gateway_healthy,
                busy_check=auth_busy,
            )
            chats.auth_lock = global_auth.lock
            routines.auth_lock = global_auth.lock
            registry = Registry(
                settings,
                database,
                hermes,
                events,
                runner=runner,
                health_check=gateway_healthy,
                global_auth=global_auth,
            )
            google = GoogleAuthService(
                settings,
                events,
                runner=runner,
                global_auth=global_auth,
            )
            credentials = CredentialService(
                settings,
                serve,
                events,
                runner=runner,
                health_check=gateway_healthy,
                google=google,
                global_auth=global_auth,
            )
            mcp = McpService(
                settings,
                events,
                runner=runner,
                health_check=gateway_healthy,
                serve=serve,
                global_auth=global_auth,
            )
            feed = FeedService(settings, database, hermes, events)
            approvals = ApprovalService(database, hermes, events)
            watcher = FeedWatcher(settings, database, events)
            watcher_task = asyncio.create_task(watcher.run(), name="botter-feed-watcher")
            app.state.settings = settings
            app.state.db = database
            app.state.hermes = hermes
            app.state.events = events
            app.state.global_auth = global_auth
            app.state.registry = registry
            app.state.google = google
            app.state.serve = serve
            app.state.credentials = credentials
            app.state.mcp = mcp
            app.state.feed = feed
            app.state.chats = chats
            app.state.approvals = approvals
            app.state.routines = routines
            yield
        finally:
            if "watcher_task" in locals():
                watcher_task.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher_task
            if "mcp" in locals():
                await mcp.close()
            if "serve" in locals():
                await serve.close()
            if "chats" in locals():
                await chats.close()
            await client.aclose()
            await database.close()

    app = FastAPI(title="botterd", version=__version__, lifespan=lifespan)
    app.add_middleware(BearerAuthMiddleware, token_provider=settings.load_or_create_token)
    install_error_handlers(app)

    @app.exception_handler(HermesError)
    async def hermes_error_handler(_request: Request, exc: HermesError):
        return error_response(502, exc.code, exc.message)

    @app.get("/v1/health", response_model=HealthResponse)
    async def health(request: Request) -> HealthResponse:
        try:
            upstream = await request.app.state.hermes.health()
            detailed = await request.app.state.hermes.health_detailed()
            hermes_status = {
                "reachable": True,
                "status": upstream.get("status", "ok"),
                "version": upstream.get("version"),
                "gateway_pid": detailed.get("pid"),
                "gateway_pid_fresh": bool(detailed.get("pid")),
                "gateway_updated_at": detailed.get("updated_at"),
            }
            overall = "ok"
        except Exception:
            hermes_status = {"reachable": False, "status": "unavailable", "version": None}
            overall = "degraded"
        return HealthResponse(status=overall, version=__version__, hermes=hermes_status)

    @app.get("/v1/bots", response_model=BotsResponse)
    async def list_bots(request: Request) -> BotsResponse:
        return BotsResponse(bots=await request.app.state.feed.roster())

    @app.post("/v1/bots", response_model=BotResponse, status_code=status.HTTP_201_CREATED)
    async def create_bot(body: BotCreate, request: Request) -> BotResponse:
        return BotResponse(bot=await request.app.state.registry.create(body))

    @app.get("/v1/bots/{bot_id}", response_model=BotResponse)
    async def get_bot(bot_id: str, request: Request) -> BotResponse:
        bot = await request.app.state.registry.get(bot_id)
        profile = settings.profiles_dir / bot.slug
        memory = "\n\n".join(filter(None, (_read_text(profile / "memories/MEMORY.md"), _read_text(profile / "memories/USER.md"))))
        routines = await request.app.state.routines.list(bot_id)
        detail = BotDetail(**bot.model_dump(), memory_summary=memory[:500], routine_count=len(routines))
        return BotResponse(bot=detail)

    @app.patch("/v1/bots/{bot_id}", response_model=BotResponse)
    async def patch_bot(bot_id: str, body: BotPatch, request: Request) -> BotResponse:
        return BotResponse(bot=await request.app.state.registry.patch(bot_id, body))

    @app.delete("/v1/bots/{bot_id}", response_model=DeleteResponse)
    async def delete_bot(bot_id: str, request: Request, purge: bool = False) -> DeleteResponse:
        if purge:
            await request.app.state.registry.purge(bot_id)
            return DeleteResponse(id=bot_id, purged=True)
        await request.app.state.registry.archive(bot_id)
        return DeleteResponse(id=bot_id, archived=True)

    @app.get("/v1/bots/{bot_id}/sessions", response_model=SessionsResponse)
    async def list_sessions(bot_id: str, request: Request) -> SessionsResponse:
        bot = await request.app.state.registry.get(bot_id)
        raw = await request.app.state.hermes.list_sessions(bot.slug)
        return SessionsResponse(sessions=[_session_model(item, bot.id) for item in raw])

    @app.post("/v1/bots/{bot_id}/sessions", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
    async def create_session(bot_id: str, body: SessionCreate, request: Request) -> SessionResponse:
        bot = await request.app.state.registry.get(bot_id)
        raw = await request.app.state.hermes.create_session(
            bot.slug, title=body.title, model=body.model or request.app.state.hermes.default_model
        )
        return SessionResponse(session=_session_model(raw, bot.id))

    @app.get("/v1/sessions/{session_id}/messages", response_model=MessagesResponse)
    async def messages(
        session_id: str,
        request: Request,
        before: str | None = None,
        limit: int = Query(50, ge=1, le=200),
    ) -> MessagesResponse:
        bot, _ = await request.app.state.feed.resolve_session(session_id)
        all_messages = await request.app.state.feed.messages(bot, session_id, limit=500)
        if before:
            index = next((i for i, message in enumerate(all_messages) if message.id == before), None)
            if index is None:
                raise APIError(400, "invalid_cursor", f"Message cursor not found: {before}")
            all_messages = all_messages[:index]
        page = all_messages[-limit:]
        return MessagesResponse(messages=page, has_more=len(all_messages) > len(page))

    @app.post("/v1/sessions/{session_id}/chat")
    async def chat(session_id: str, body: ChatRequest, request: Request) -> StreamingResponse:
        bot, _ = await request.app.state.feed.resolve_session(session_id)
        message = body.model_dump(mode="json")["message"]
        active = await request.app.state.chats.start(bot, session_id, message)
        return StreamingResponse(
            request.app.state.chats.stream(active),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/sessions/{session_id}/read", response_model=ReadResponse)
    async def mark_read(session_id: str, body: ReadRequest, request: Request) -> ReadResponse:
        bot, _ = await request.app.state.feed.resolve_session(session_id)
        message_id = body.message_id
        if message_id is None:
            history = await request.app.state.feed.messages(bot, session_id, limit=1)
            message_id = history[-1].id if history else None
        await request.app.state.db.set_read_marker(bot.id, session_id, message_id)
        await request.app.state.events.publish("feed_updated", {"bot_id": bot.id})
        return ReadResponse(session_id=session_id, last_read_message_id=message_id)

    @app.post("/v1/sessions/{session_id}/stop", response_model=StopResponse)
    async def stop(session_id: str, request: Request) -> StopResponse:
        await request.app.state.feed.resolve_session(session_id)
        stopped = await request.app.state.chats.stop(session_id)
        return StopResponse(session_id=session_id, stopped=stopped)

    @app.get("/v1/bots/{bot_id}/routines", response_model=RoutinesResponse)
    async def list_routines(bot_id: str, request: Request) -> RoutinesResponse:
        return RoutinesResponse(routines=await request.app.state.routines.list(bot_id))

    @app.post("/v1/bots/{bot_id}/routines", response_model=RoutineResponse, status_code=status.HTTP_201_CREATED)
    async def create_routine(bot_id: str, body: RoutineCreate, request: Request) -> RoutineResponse:
        return RoutineResponse(routine=await request.app.state.routines.create(bot_id, body))

    @app.patch("/v1/routines/{routine_id}", response_model=RoutineResponse)
    async def patch_routine(routine_id: str, body: RoutinePatch, request: Request) -> RoutineResponse:
        return RoutineResponse(routine=await request.app.state.routines.patch(routine_id, body))

    @app.delete("/v1/routines/{routine_id}")
    async def delete_routine(routine_id: str, request: Request) -> dict[str, Any]:
        await request.app.state.routines.delete(routine_id)
        return {"id": routine_id, "deleted": True}

    @app.post("/v1/routines/{routine_id}/run", response_model=RoutineRunResponse, status_code=status.HTTP_202_ACCEPTED)
    async def run_routine(routine_id: str, request: Request) -> RoutineRunResponse:
        routine, _ = await request.app.state.routines.action(routine_id, "run")
        return RoutineRunResponse(routine=routine)

    @app.post("/v1/routines/{routine_id}/pause", response_model=RoutineResponse)
    async def pause_routine(routine_id: str, request: Request) -> RoutineResponse:
        routine, _ = await request.app.state.routines.action(routine_id, "pause")
        return RoutineResponse(routine=routine)

    @app.post("/v1/routines/{routine_id}/resume", response_model=RoutineResponse)
    async def resume_routine(routine_id: str, request: Request) -> RoutineResponse:
        routine, _ = await request.app.state.routines.action(routine_id, "resume")
        return RoutineResponse(routine=routine)

    @app.get("/v1/routines/{routine_id}/executions", response_model=ExecutionsResponse)
    async def executions(
        routine_id: str, request: Request, limit: int = Query(20, ge=1, le=100)
    ) -> ExecutionsResponse:
        return ExecutionsResponse(executions=await request.app.state.routines.executions(routine_id, limit))

    @app.get("/v1/approvals", response_model=ApprovalsResponse)
    async def approvals(request: Request) -> ApprovalsResponse:
        return ApprovalsResponse(approvals=await request.app.state.approvals.list())

    @app.post("/v1/approvals/{run_id}", response_model=ApprovalResponse)
    async def decide(run_id: str, body: ApprovalDecision, request: Request) -> ApprovalResponse:
        return await request.app.state.approvals.decide(run_id, body)

    @app.get("/v1/bots/{bot_id}/memory", response_model=MemoryResponse)
    async def memory(bot_id: str, request: Request) -> MemoryResponse:
        bot = await request.app.state.registry.get(bot_id)
        memory_dir = settings.profiles_dir / bot.slug / "memories"
        return MemoryResponse(
            bot_id=bot.id,
            memory=_read_text(memory_dir / "MEMORY.md"),
            user=_read_text(memory_dir / "USER.md"),
        )

    @app.get("/v1/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            request.app.state.events.stream(),
            media_type="text/event-stream",
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
        result = await request.app.state.google.connect(body or GoogleConnect())
        if isinstance(result, Authorization):
            return AuthorizationResponse(authorization=result)
        return IntegrationResponse(integration=result)

    @app.delete("/v1/auth/google", response_model=IntegrationResponse)
    async def disconnect_google(request: Request) -> IntegrationResponse:
        return IntegrationResponse(integration=await request.app.state.google.disconnect())

    @app.get("/v1/integrations", response_model=IntegrationsResponse)
    async def list_integrations(request: Request) -> IntegrationsResponse:
        return IntegrationsResponse(integrations=await request.app.state.credentials.list())

    @app.put("/v1/integrations/{key}", response_model=IntegrationResponse)
    async def put_integration(key: str, body: IntegrationUpdate, request: Request) -> IntegrationResponse:
        return IntegrationResponse(integration=await request.app.state.credentials.put(key, body.value))

    @app.delete("/v1/integrations/{key}", response_model=IntegrationResponse)
    async def delete_integration(key: str, request: Request) -> IntegrationResponse:
        return IntegrationResponse(integration=await request.app.state.credentials.delete(key))

    @app.get("/v1/mcp", response_model=McpServersResponse)
    async def list_mcp_servers(request: Request) -> McpServersResponse:
        servers, presets = await request.app.state.mcp.list()
        return McpServersResponse(servers=servers, presets=presets)

    @app.put("/v1/mcp/{name}", response_model=McpServerResponse)
    async def put_mcp_server(name: str, body: McpServerUpdate, request: Request) -> McpServerResponse:
        server, restarted = await request.app.state.mcp.put(name, body)
        return McpServerResponse(server=server, restarted=restarted)

    @app.delete("/v1/mcp/{name}", response_model=McpServerResponse)
    async def delete_mcp_server(name: str, request: Request) -> McpServerResponse:
        server, restarted = await request.app.state.mcp.delete(name)
        return McpServerResponse(server=server, restarted=restarted)

    @app.post("/v1/mcp/{name}/authorize", response_model=McpAuthorizationResponse)
    async def authorize_mcp_server(name: str, request: Request) -> McpAuthorizationResponse:
        return McpAuthorizationResponse(
            authorization=await request.app.state.mcp.authorize(name)
        )

    @app.get("/v1/mcp/authorizations/{flow_id}", response_model=McpAuthorizationResponse)
    async def mcp_authorization_status(flow_id: str, request: Request) -> McpAuthorizationResponse:
        authorization, server = await request.app.state.mcp.authorization_status(flow_id)
        return McpAuthorizationResponse(authorization=authorization, server_state=server)

    @app.get("/v1/search", response_model=SearchResponse)
    async def search(
        request: Request, q: str = Query(min_length=1), bot_id: str | None = None
    ) -> SearchResponse:
        if bot_id is not None:
            await request.app.state.registry.get(bot_id)
        return SearchResponse(messages=await request.app.state.feed.search(q, bot_id))

    return app


app = create_app()


def run() -> None:
    settings = Settings.from_env()
    configure_logging()
    uvicorn.run("botterd.main:app", host=settings.host, port=settings.port, log_config=None)
