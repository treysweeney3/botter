"""Roster aggregation, read-only Hermes SQLite fallback, search, and WAL watcher."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from .config import Settings
from .db import Database
from .events import EventBus
from .hermes import HermesClient
from .models import Bot, BotRosterItem, NormalizedMessage
from .normalize import normalize_datetime, normalize_row, normalize_rows
from .routines import read_executions, read_routine_names, synthesized_execution_messages


logger = logging.getLogger(__name__)


async def _table_columns(connection: aiosqlite.Connection, table: str) -> set[str]:
    cursor = await connection.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    await cursor.close()
    return {str(row[1]) for row in rows}


async def read_profile_messages(
    db_path: Path,
    *,
    session_id: str | None = None,
    limit: int = 100,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """Read a Hermes state.db through SQLite's read-only URI mode."""
    if not db_path.exists():
        return []
    uri = f"file:{db_path}?mode=ro"
    connection = await aiosqlite.connect(uri, uri=True)
    connection.row_factory = aiosqlite.Row
    try:
        columns = await _table_columns(connection, "messages")
        if not columns:
            return []
        selected = [
            column for column in
            ("id", "session_id", "role", "content", "tool_call_id", "tool_calls", "tool_name", "timestamp", "created_at")
            if column in columns
        ]
        clauses = []
        parameters: list[Any] = []
        if session_id and "session_id" in columns:
            clauses.append("session_id = ?")
            parameters.append(session_id)
        if search and "content" in columns:
            clauses.append("content LIKE ?")
            parameters.append(f"%{search}%")
        if "active" in columns:
            clauses.append("active = 1")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order_column = "timestamp" if "timestamp" in columns else "created_at" if "created_at" in columns else "rowid"
        cursor = await connection.execute(
            f"SELECT {','.join(selected)} FROM messages{where} ORDER BY {order_column} DESC LIMIT ?",
            (*parameters, max(0, min(limit, 500))),
        )
        rows = [dict(row) for row in await cursor.fetchall()]
        await cursor.close()
        return rows
    finally:
        await connection.close()


async def search_profile_messages(db_path: Path, query: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Search Hermes' FTS5 index read-only, with a compatibility fallback."""
    if not db_path.exists():
        return []
    connection = await aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True)
    connection.row_factory = aiosqlite.Row
    try:
        try:
            cursor = await connection.execute(
                """SELECT m.id, m.session_id, m.role, m.content, m.tool_calls,
                          m.tool_name, m.timestamp
                   FROM messages_fts
                   JOIN messages m ON m.id = messages_fts.rowid
                   WHERE messages_fts MATCH ?
                     AND (COALESCE(m.active, 1) = 1 OR COALESCE(m.compacted, 0) = 1)
                   ORDER BY rank LIMIT ?""",
                (query, max(0, min(limit, 500))),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            await cursor.close()
            return rows
        except aiosqlite.Error:
            # Older/minimal fixture databases may not have the FTS table or
            # active/compacted columns. Preserve read-only search via LIKE.
            columns = await _table_columns(connection, "messages")
            selected = [
                column for column in
                ("id", "session_id", "role", "content", "tool_call_id", "tool_calls", "tool_name", "timestamp", "created_at")
                if column in columns
            ]
            cursor = await connection.execute(
                f"SELECT {','.join(selected)} FROM messages WHERE content LIKE ? ORDER BY rowid DESC LIMIT ?",
                (f"%{query}%", max(0, min(limit, 500))),
            )
            rows = [dict(row) for row in await cursor.fetchall()]
            await cursor.close()
            return rows
    finally:
        await connection.close()


async def profile_has_session(db_path: Path, session_id: str) -> bool:
    if not db_path.exists():
        return False
    connection = await aiosqlite.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = await connection.execute("SELECT 1 FROM sessions WHERE id = ? LIMIT 1", (session_id,))
        found = await cursor.fetchone() is not None
        await cursor.close()
        return found
    except aiosqlite.Error:
        return False
    finally:
        await connection.close()


class FeedService:
    def __init__(self, settings: Settings, db: Database, hermes: HermesClient, events: EventBus):
        self.settings = settings
        self.db = db
        self.hermes = hermes
        self.events = events

    async def resolve_session(self, session_id: str) -> tuple[Bot, dict[str, Any]]:
        direct = await self.db.get_bot_by_session(session_id)
        if direct:
            return Bot.model_validate(direct), {"id": session_id}
        bots = [Bot.model_validate(row) for row in await self.db.list_bots()]

        async def find(bot: Bot) -> tuple[Bot, dict[str, Any]] | None:
            try:
                for session in await self.hermes.list_sessions(bot.slug):
                    if str(session.get("id")) == session_id:
                        return bot, session
            except Exception:
                return None
            return None

        results = await asyncio.gather(*(find(bot) for bot in bots))
        for result in results:
            if result is not None:
                return result
        fallback_matches = [
            bot
            for bot in bots
            if await profile_has_session(self.settings.profiles_dir / bot.slug / "state.db", session_id)
        ]
        if len(fallback_matches) == 1:
            return fallback_matches[0], {"id": session_id}
        if len(fallback_matches) > 1:
            from .errors import APIError

            raise APIError(409, "ambiguous_session", f"Session ID exists in more than one bot profile: {session_id}")
        from .errors import APIError

        raise APIError(404, "session_not_found", f"Session not found: {session_id}")

    async def messages(self, bot: Bot, session_id: str, *, limit: int = 100) -> list[NormalizedMessage]:
        try:
            rows = await self.hermes.get_messages(bot.slug, session_id, limit=limit)
        except Exception:
            fallback_rows = await read_profile_messages(
                self.settings.profiles_dir / bot.slug / "state.db", session_id=session_id, limit=limit
            )
            rows = list(reversed(fallback_rows))
        # Hermes returns the selected latest page in chronological order; the
        # fallback is reversed above to preserve that public invariant.
        messages = normalize_rows(rows, bot_id=bot.id, session_id=session_id)
        if session_id == bot.default_session_id:
            messages.extend(
                await synthesized_execution_messages(
                    self.settings.profiles_dir / bot.slug,
                    bot_id=bot.id,
                    session_id=session_id,
                    limit=limit,
                )
            )
            messages.sort(key=lambda message: (message.created_at, message.id))
        return messages

    async def roster_item(self, bot: Bot) -> BotRosterItem:
        preview: str | None = None
        timestamp: datetime | None = None
        unread = 0
        rows_by_session: dict[str, list[dict[str, Any]]] = {}
        try:
            sessions = await self.hermes.list_sessions(bot.slug)
            if not sessions and bot.default_session_id:
                sessions = [{"id": bot.default_session_id}]

            async def load(session: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
                session_id = str(session.get("id") or "")
                return session_id, await self.hermes.get_messages(bot.slug, session_id, limit=500)

            rows_by_session = dict(await asyncio.gather(*(load(session) for session in sessions)))
            if sessions:
                latest_session = max(sessions, key=lambda item: float(item.get("last_active") or item.get("started_at") or 0))
                preview = latest_session.get("preview") or None
                timestamp = normalize_datetime(latest_session.get("last_active") or latest_session.get("started_at"))
        except Exception:
            fallback = list(
                reversed(await read_profile_messages(self.settings.profiles_dir / bot.slug / "state.db", limit=500))
            )
            for row in fallback:
                rows_by_session.setdefault(str(row.get("session_id") or bot.default_session_id or ""), []).append(row)
        if bot.default_session_id:
            virtual = await synthesized_execution_messages(
                self.settings.profiles_dir / bot.slug,
                bot_id=bot.id,
                session_id=bot.default_session_id,
                limit=500,
            )
            default_rows = rows_by_session.setdefault(bot.default_session_id, [])
            default_rows.extend(
                {
                    "id": message.id,
                    "session_id": message.session_id,
                    "role": message.role,
                    "content": message.text,
                    "timestamp": message.created_at,
                }
                for message in virtual
            )
            default_rows.sort(key=lambda item: normalize_datetime(item.get("timestamp", item.get("created_at"))))
        all_rows = [row for rows in rows_by_session.values() for row in rows]
        if all_rows:
            latest = next(
                (
                    row
                    for row in sorted(
                        all_rows,
                        key=lambda item: normalize_datetime(item.get("timestamp", item.get("created_at"))),
                        reverse=True,
                    )
                    if row.get("role") in {"user", "assistant"} and row.get("content")
                ),
                all_rows[-1],
            )
            preview = str(latest.get("content") or preview or "").replace("\n", " ")[:160] or None
            timestamp = normalize_datetime(latest.get("timestamp", latest.get("created_at")))
        for session_id, rows in rows_by_session.items():
            marker = await self.db.read_marker(bot.id, session_id)
            marker_index = next(
                (index for index, row in enumerate(rows) if marker and str(row.get("id")) == marker),
                -1,
            )
            unread += sum(row.get("role") == "assistant" for row in rows[marker_index + 1 :])
        return BotRosterItem(
            **bot.model_dump(),
            latest_message_preview=preview,
            latest_message_at=timestamp,
            unread_count=unread,
        )

    async def roster(self) -> list[BotRosterItem]:
        bots = [Bot.model_validate(row) for row in await self.db.list_bots()]
        return list(await asyncio.gather(*(self.roster_item(bot) for bot in bots)))

    async def search(self, query: str, bot_id: str | None = None) -> list[NormalizedMessage]:
        bots = [Bot.model_validate(row) for row in await self.db.list_bots()]
        if bot_id:
            bots = [bot for bot in bots if bot.id == bot_id]
        found: list[NormalizedMessage] = []
        for bot in bots:
            rows = await search_profile_messages(
                self.settings.profiles_dir / bot.slug / "state.db", query, limit=100
            )
            for row in rows:
                # A hit is one row, not a turn, so it keeps its own text.
                if row.get("role") not in {"user", "assistant", "system"}:
                    continue
                found.append(
                    normalize_row(row, bot_id=bot.id, session_id=str(row.get("session_id") or ""))
                )
        return sorted(found, key=lambda message: message.created_at, reverse=True)


class FeedWatcher:
    def __init__(self, settings: Settings, db: Database, events: EventBus, *, interval: float = 2.0):
        self.settings = settings
        self.db = db
        self.events = events
        self.interval = interval
        self._mtimes: dict[str, tuple[int | None, int | None, int | None, int | None]] = {}
        self._terminal_execution_ids: dict[str, set[str]] = {}

    @staticmethod
    def _mtime(path: Path) -> int | None:
        try:
            return path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    async def poll_once(self) -> None:
        for row in await self.db.list_bots():
            slug = str(row["slug"])
            if slug in {"main", "default"}:
                continue
            profile = self.settings.profiles_dir / slug
            execution_db = profile / "cron" / "executions.db"
            current = (
                self._mtime(profile / "state.db"),
                self._mtime(profile / "state.db-wal"),
                self._mtime(execution_db),
                self._mtime(execution_db.with_name("executions.db-wal")),
            )
            previous = self._mtimes.get(slug)
            self._mtimes[slug] = current
            executions = await read_executions(execution_db, None, 1000)
            terminal = {
                execution.id
                for execution in executions
                if execution.status in {"completed", "failed", "unknown"}
            }
            prior_terminal = self._terminal_execution_ids.get(slug)
            self._terminal_execution_ids[slug] = terminal
            if previous is not None and current[:2] != previous[:2]:
                await self.events.publish("feed_updated", {"bot_id": row["id"]})
            if previous is not None and current[2:] != previous[2:]:
                names = read_routine_names(profile / "cron" / "jobs.json")
                new_ids = terminal - (prior_terminal or set())
                for execution in reversed(executions):
                    if execution.id not in new_ids:
                        continue
                    await self.events.publish(
                        "routine_fired",
                        {
                            "bot_id": row["id"],
                            "routine_id": execution.routine_id,
                            "name": names.get(execution.routine_id, execution.routine_id),
                        },
                    )
                await self.events.publish("feed_updated", {"bot_id": row["id"]})

    async def run(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self.interval)
