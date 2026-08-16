"""Hermes cron routine proxy and read-only execution history."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .config import Settings
from .db import Database
from .errors import APIError
from .events import EventBus
from .hermes import HermesClient, HermesError
from .models import (
    Bot,
    Execution,
    NormalizedMessage,
    Routine,
    RoutineCreate,
    RoutinePatch,
    RoutineReference,
)
from .normalize import normalize_datetime


def normalize_routine(raw: dict[str, Any], *, bot_id: str) -> Routine:
    schedule = raw.get("schedule") or ""
    if isinstance(schedule, dict):
        schedule = schedule.get("expr") or schedule.get("display") or ""
    paused = not bool(raw.get("enabled", True)) or raw.get("state") == "paused" or bool(raw.get("paused_at"))
    return Routine(
        id=str(raw.get("id") or ""),
        bot_id=bot_id,
        name=str(raw.get("name") or ""),
        schedule=str(schedule),
        prompt=str(raw.get("prompt") or ""),
        paused=paused,
        state=str(raw.get("state") or ("paused" if paused else "scheduled")),
        last_run_at=normalize_datetime(raw["last_run_at"]) if raw.get("last_run_at") else None,
        last_status=str(raw["last_status"]) if raw.get("last_status") else None,
        next_run_at=normalize_datetime(raw["next_run_at"]) if raw.get("next_run_at") else None,
    )


async def read_executions(db_path: Path, routine_id: str | None, limit: int) -> list[Execution]:
    if not db_path.exists():
        return []
    connection = await aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = aiosqlite.Row
    try:
        where = " WHERE job_id = ?" if routine_id is not None else ""
        parameters: tuple[Any, ...] = (
            (routine_id, max(0, min(limit, 1000)))
            if routine_id is not None
            else (max(0, min(limit, 1000)),)
        )
        cursor = await connection.execute(
            "SELECT id, job_id, status, claimed_at, started_at, finished_at, error "
            f"FROM executions{where} ORDER BY claimed_at DESC, id DESC LIMIT ?",
            parameters,
        )
        rows = await cursor.fetchall()
        await cursor.close()
    except aiosqlite.Error:
        return []
    finally:
        await connection.close()
    return [
        Execution(
            id=str(row["id"]),
            routine_id=str(row["job_id"]),
            started_at=normalize_datetime(row["started_at"] or row["claimed_at"]),
            finished_at=normalize_datetime(row["finished_at"]) if row["finished_at"] else None,
            status=str(row["status"] or "unknown"),
            summary=str(row["error"] or ""),
        )
        for row in rows
    ]


def read_routine_names(jobs_path: Path) -> dict[str, str]:
    """Read only the id/name fields from Hermes' profile-local jobs store."""
    try:
        payload = json.loads(jobs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(payload, dict):
        jobs = payload.get("jobs")
    else:
        jobs = payload  # Compatibility with pre-envelope Hermes stores.
    if not isinstance(jobs, list):
        return {}
    return {
        str(item["id"]): str(item.get("name") or item["id"])
        for item in jobs
        if isinstance(item, dict) and item.get("id")
    }


async def synthesized_execution_messages(
    profile_dir: Path,
    *,
    bot_id: str,
    session_id: str,
    limit: int = 500,
) -> list[NormalizedMessage]:
    """Project terminal cron executions into the bot's virtual main thread.

    Hermes persists cron work in a fresh session per fire and has no delivery
    target for api_server sessions. These deterministic virtual messages keep
    Botter's default thread honest without writing to Hermes state.
    """
    executions = await read_executions(profile_dir / "cron" / "executions.db", None, limit)
    names = read_routine_names(profile_dir / "cron" / "jobs.json")
    messages: list[NormalizedMessage] = []
    for execution in executions:
        if execution.status not in {"completed", "failed", "unknown"}:
            continue
        name = names.get(execution.routine_id, execution.routine_id)
        if execution.status == "completed":
            text = f"Routine completed: {name}"
        elif execution.status == "failed":
            detail = f" — {execution.summary}" if execution.summary else ""
            text = f"Routine failed: {name}{detail}"
        else:
            text = f"Routine outcome unknown after scheduler restart: {name}"
        messages.append(
            NormalizedMessage(
                id=f"routine-execution:{execution.id}",
                session_id=session_id,
                bot_id=bot_id,
                role="assistant",
                kind="routine_created",
                text=text,
                routine=RoutineReference(id=execution.routine_id, name=name),
                created_at=execution.finished_at or execution.started_at,
            )
        )
    return messages


class RoutineService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        hermes: HermesClient,
        events: EventBus,
        *,
        auth_lock: asyncio.Lock | None = None,
    ):
        self.settings = settings
        self.db = db
        self.hermes = hermes
        self.events = events
        self.auth_lock = auth_lock

    async def has_active_executions(self) -> bool:
        """Return whether Hermes reports a managed routine as claimed/running."""
        for row in await self.db.list_bots(include_archived=True):
            slug = str(row.get("slug") or "")
            db_path = self.settings.profiles_dir / slug / "cron" / "executions.db"
            if not db_path.is_file() or db_path.is_symlink():
                continue
            connection = await aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                cursor = await connection.execute(
                    "SELECT 1 FROM executions "
                    "WHERE status IN ('claimed', 'running') AND finished_at IS NULL LIMIT 1"
                )
                active = await cursor.fetchone()
                await cursor.close()
                if active is not None:
                    return True
            except aiosqlite.Error:
                # A malformed/unreadable live ledger is not proof of active work.
                continue
            finally:
                await connection.close()
        return False

    async def _bot(self, bot_id: str) -> Bot:
        row = await self.db.get_bot(bot_id)
        if row is None:
            raise APIError(404, "bot_not_found", f"Bot not found: {bot_id}")
        return Bot.model_validate(row)

    async def list(self, bot_id: str) -> list[Routine]:
        bot = await self._bot(bot_id)
        try:
            return [normalize_routine(job, bot_id=bot.id) for job in await self.hermes.list_jobs(bot.slug)]
        except HermesError as exc:
            raise APIError(502, exc.code, exc.message) from exc

    async def create(self, bot_id: str, request: RoutineCreate) -> Routine:
        bot = await self._bot(bot_id)
        try:
            raw = await self.hermes.create_job(
                bot.slug,
                {"name": request.name, "schedule": request.schedule, "prompt": request.prompt, "deliver": "local"},
            )
        except HermesError as exc:
            raise APIError(502, exc.code, exc.message) from exc
        routine = normalize_routine(raw, bot_id=bot.id)
        await self.events.publish("bot_updated", {"bot_id": bot.id})
        return routine

    async def find(self, routine_id: str) -> tuple[Bot, dict[str, Any]]:
        for row in await self.db.list_bots():
            bot = Bot.model_validate(row)
            try:
                for job in await self.hermes.list_jobs(bot.slug):
                    if str(job.get("id")) == routine_id:
                        return bot, job
            except HermesError:
                continue
        raise APIError(404, "routine_not_found", f"Routine not found: {routine_id}")

    async def patch(self, routine_id: str, request: RoutinePatch) -> Routine:
        bot, _ = await self.find(routine_id)
        body = request.model_dump(exclude_none=True)
        if not body:
            return normalize_routine(await self.hermes.get_job(bot.slug, routine_id), bot_id=bot.id)
        try:
            return normalize_routine(
                await self.hermes.update_job(bot.slug, routine_id, body), bot_id=bot.id
            )
        except HermesError as exc:
            raise APIError(502, exc.code, exc.message) from exc

    async def delete(self, routine_id: str) -> None:
        bot, _ = await self.find(routine_id)
        try:
            await self.hermes.delete_job(bot.slug, routine_id)
        except HermesError as exc:
            raise APIError(502, exc.code, exc.message) from exc

    async def action(self, routine_id: str, action: str) -> tuple[Routine, Bot]:
        if action == "run" and self.auth_lock is not None:
            async with self.auth_lock:
                return await self._action_unlocked(routine_id, action)
        return await self._action_unlocked(routine_id, action)

    async def _action_unlocked(self, routine_id: str, action: str) -> tuple[Routine, Bot]:
        bot, _ = await self.find(routine_id)
        try:
            routine = normalize_routine(
                await self.hermes.job_action(bot.slug, routine_id, action), bot_id=bot.id
            )
        except HermesError as exc:
            raise APIError(502, exc.code, exc.message) from exc
        return routine, bot

    async def executions(self, routine_id: str, limit: int) -> list[Execution]:
        bot, _ = await self.find(routine_id)
        return await read_executions(
            self.settings.profiles_dir / bot.slug / "cron" / "executions.db", routine_id, limit
        )
