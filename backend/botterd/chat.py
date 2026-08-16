"""Disconnect-safe chat run orchestration and public SSE translation."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .db import Database
from .events import EventBus, sse_frame
from .hermes import HermesClient, HermesError, HermesEvent
from .models import Bot
from .normalize import derive_task_items, normalize_completed_stream, normalize_row


logger = logging.getLogger(__name__)
END = object()


@dataclass(slots=True)
class ActiveChat:
    run_id: str
    bot: Bot
    session_id: str
    queue: asyncio.Queue[tuple[str, dict[str, Any]] | object] = field(default_factory=asyncio.Queue)
    task: asyncio.Task[None] | None = None
    detached: bool = False


class ChatManager:
    def __init__(
        self,
        hermes: HermesClient,
        db: Database,
        events: EventBus,
        *,
        auth_lock: asyncio.Lock | None = None,
    ):
        self.hermes = hermes
        self.db = db
        self.events = events
        self.by_run: dict[str, ActiveChat] = {}
        self.by_session: dict[str, ActiveChat] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self.auth_lock = auth_lock

    @property
    def has_active_runs(self) -> bool:
        return any(active.task is not None and not active.task.done() for active in self.by_run.values())

    async def start(self, bot: Bot, session_id: str, message: str | list[dict[str, Any]]) -> ActiveChat:
        if self.auth_lock is not None:
            async with self.auth_lock:
                return await self._start_locked(bot, session_id, message)
        return await self._start_locked(bot, session_id, message)

    async def _start_locked(
        self, bot: Bot, session_id: str, message: str | list[dict[str, Any]]
    ) -> ActiveChat:
        previous = self.by_session.get(session_id)
        if previous and previous.task and not previous.task.done():
            from .errors import APIError

            raise APIError(409, "session_busy", "This session already has an active run")
        try:
            started = await self.hermes.start_run(bot.slug, session_id, message)
        except HermesError as exc:
            from .errors import APIError

            raise APIError(502, exc.code, exc.message) from exc
        run_id = str(started.get("run_id") or "")
        if not run_id:
            from .errors import APIError

            raise APIError(502, "invalid_hermes_response", "Hermes did not return a run_id")
        active = ActiveChat(run_id=run_id, bot=bot, session_id=session_id)
        task = asyncio.create_task(self._consume(active), name=f"botter-chat-{run_id}")
        active.task = task
        self.by_run[run_id] = active
        self.by_session[session_id] = active
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return active

    def _emit(self, active: ActiveChat, event: str, payload: dict[str, Any]) -> None:
        if not active.detached:
            active.queue.put_nowait((event, payload))

    async def _completed_message(
        self, active: ActiveChat, history: list[HermesEvent], completed: HermesEvent
    ):
        """Prefer Hermes' canonical persisted row identity after completion."""
        task_items = derive_task_items(history)
        for attempt in range(5):
            try:
                rows = await self.hermes.get_messages(active.bot.slug, active.session_id, limit=50)
            except Exception:
                rows = []
            assistant = next(
                (row for row in reversed(rows) if row.get("role") == "assistant" and row.get("content")),
                None,
            )
            if assistant is not None:
                message = normalize_row(assistant, bot_id=active.bot.id, session_id=active.session_id)
                if task_items:
                    message = message.model_copy(update={"kind": "task_report", "task_items": task_items})
                return message
            if attempt < 4:
                await asyncio.sleep(0.05 * (attempt + 1))
        return normalize_completed_stream(history + [completed], bot_id=active.bot.id, session_id=active.session_id)

    async def _consume(self, active: ActiveChat) -> None:
        history: list[HermesEvent] = []
        # Text streamed since the last tool call. Hermes narrates what it is
        # about to do, so this buffer is a note on the tools that follow it.
        narration: list[str] = []
        try:
            async for upstream in self.hermes.stream_run_events(active.bot.slug, active.run_id):
                data = upstream.data
                if upstream.event == "message.delta":
                    delta = str(data.get("delta") or "")
                    narration.append(delta)
                    self._emit(active, "delta", {"text": delta})
                elif upstream.event in {"tool.started", "tool.completed", "tool.failed"}:
                    if upstream.event == "tool.started":
                        note = "".join(narration).strip()
                        narration.clear()
                        if note:
                            history.append(HermesEvent("assistant.note", {"text": note}))
                    tool_name = str(data.get("tool_name") or data.get("tool") or "tool")
                    canonical_name = "tool.failed" if (
                        upstream.event == "tool.failed" or bool(data.get("error"))
                    ) else upstream.event
                    canonical = HermesEvent(
                        canonical_name,
                        {**data, "tool_name": tool_name, "run_id": active.run_id},
                    )
                    history.append(canonical)
                    status = "started" if upstream.event == "tool.started" else "error" if (
                        upstream.event == "tool.failed" or bool(data.get("error"))
                    ) else "ok"
                    summary = str(data.get("preview") or data.get("summary") or tool_name)
                    self._emit(active, "tool_event", {"name": tool_name, "status": status, "summary": summary})
                elif upstream.event == "approval.request":
                    summary = str(data.get("description") or data.get("summary") or "Approval required")
                    approval = {
                        "run_id": active.run_id,
                        "bot_id": active.bot.id,
                        "session_id": active.session_id,
                        "summary": summary,
                        "requested_at": datetime.now(timezone.utc).isoformat(),
                    }
                    await self.db.add_approval(approval)
                    await self.events.publish(
                        "approval_pending",
                        {"approval": {key: value for key, value in approval.items() if key != "session_id"}},
                    )
                    self._emit(active, "approval_required", {"run_id": active.run_id, "summary": summary})
                elif upstream.event == "run.completed":
                    canonical = HermesEvent(
                        "run.completed",
                        {
                            **data,
                            "message_id": data.get("message_id") or f"run-message-{active.run_id}",
                            "run_id": active.run_id,
                            "timestamp": data.get("timestamp") or datetime.now(timezone.utc).timestamp(),
                        },
                    )
                    message = await self._completed_message(active, history, canonical)
                    if message is not None:
                        self._emit(active, "message_complete", {"message": json.loads(message.model_dump_json())})
                    await self.events.publish("feed_updated", {"bot_id": active.bot.id})
                elif upstream.event in {"run.failed", "run.cancelled"}:
                    message = str(
                        data.get("error")
                        or ("Run cancelled" if upstream.event == "run.cancelled" else "Run failed")
                    )
                    self._emit(active, "error", {"code": upstream.event.replace(".", "_"), "message": message})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("chat upstream consumer failed for run_id=%s", active.run_id)
            self._emit(active, "error", {"code": "upstream_error", "message": str(exc)})
        finally:
            active.queue.put_nowait(END)
            self.by_run.pop(active.run_id, None)
            if self.by_session.get(active.session_id) is active:
                self.by_session.pop(active.session_id, None)

    async def stream(self, active: ActiveChat, *, heartbeat_seconds: float = 15.0) -> AsyncIterator[str]:
        try:
            while True:
                try:
                    item = await asyncio.wait_for(active.queue.get(), timeout=heartbeat_seconds)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if item is END:
                    break
                event, payload = item
                yield sse_frame(event, payload)
        finally:
            # Starlette cancels this generator when the client disconnects.
            # The separately-owned active.task deliberately remains alive.
            active.detached = True

    async def stop(self, session_id: str) -> bool:
        active = self.by_session.get(session_id)
        if active is None:
            return False
        await self.hermes.stop_run(active.bot.slug, active.run_id)
        return True

    async def close(self) -> None:
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
