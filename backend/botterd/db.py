"""Small botter-owned SQLite registry and state store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite


MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    avatar_color TEXT NOT NULL,
    avatar_glyph TEXT NOT NULL,
    approval_boundary TEXT NOT NULL,
    default_session_id TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS read_state (
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    last_read_message_id TEXT,
    PRIMARY KEY (bot_id, session_id),
    FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS pending_approvals (
    run_id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    decision TEXT,
    FOREIGN KEY (bot_id) REFERENCES bots(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.connection: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.connection = await aiosqlite.connect(self.path)
        self.connection.row_factory = aiosqlite.Row
        await self.connection.execute("PRAGMA journal_mode=WAL")
        await self.connection.execute("PRAGMA foreign_keys=ON")
        await self.connection.executescript(MIGRATION_1)
        row = await self.fetchone("SELECT version FROM schema_version LIMIT 1")
        if row is None:
            await self.connection.execute("INSERT INTO schema_version(version) VALUES (1)")
        await self.connection.commit()

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    def require_connection(self) -> aiosqlite.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not connected")
        return self.connection

    async def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> aiosqlite.Cursor:
        return await self.require_connection().execute(sql, parameters)

    async def fetchone(self, sql: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        cursor = await self.execute(sql, parameters)
        row = await cursor.fetchone()
        await cursor.close()
        return dict(row) if row is not None else None

    async def fetchall(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        cursor = await self.execute(sql, parameters)
        rows = await cursor.fetchall()
        await cursor.close()
        return [dict(row) for row in rows]

    async def commit(self) -> None:
        await self.require_connection().commit()

    async def insert_bot(self, values: dict[str, Any]) -> None:
        columns = tuple(values)
        placeholders = ",".join("?" for _ in columns)
        await self.execute(
            f"INSERT INTO bots ({','.join(columns)}) VALUES ({placeholders})",
            tuple(values[column] for column in columns),
        )
        await self.commit()

    async def list_bots(self, *, include_archived: bool = True) -> list[dict[str, Any]]:
        where = "" if include_archived else " WHERE archived = 0"
        return await self.fetchall(f"SELECT * FROM bots{where} ORDER BY created_at")

    async def get_bot(self, bot_id: str) -> dict[str, Any] | None:
        return await self.fetchone("SELECT * FROM bots WHERE id = ?", (bot_id,))

    async def get_bot_by_slug(self, slug: str) -> dict[str, Any] | None:
        return await self.fetchone("SELECT * FROM bots WHERE slug = ?", (slug,))

    async def get_bot_by_session(self, session_id: str) -> dict[str, Any] | None:
        # The default thread is common; other threads are resolved from Hermes
        # by the service when this direct lookup misses.
        return await self.fetchone("SELECT * FROM bots WHERE default_session_id = ?", (session_id,))

    async def update_bot(self, bot_id: str, values: dict[str, Any]) -> dict[str, Any] | None:
        if values:
            assignments = ",".join(f"{column} = ?" for column in values)
            await self.execute(
                f"UPDATE bots SET {assignments} WHERE id = ?",
                (*values.values(), bot_id),
            )
            await self.commit()
        return await self.get_bot(bot_id)

    async def delete_bot(self, bot_id: str) -> None:
        await self.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
        await self.commit()

    async def set_read_marker(self, bot_id: str, session_id: str, message_id: str | None) -> None:
        await self.execute(
            """INSERT INTO read_state(bot_id, session_id, last_read_message_id)
               VALUES (?, ?, ?)
               ON CONFLICT(bot_id, session_id)
               DO UPDATE SET last_read_message_id = excluded.last_read_message_id""",
            (bot_id, session_id, message_id),
        )
        await self.commit()

    async def read_marker(self, bot_id: str, session_id: str) -> str | None:
        row = await self.fetchone(
            "SELECT last_read_message_id FROM read_state WHERE bot_id = ? AND session_id = ?",
            (bot_id, session_id),
        )
        return row["last_read_message_id"] if row else None

    async def add_approval(self, values: dict[str, Any]) -> None:
        await self.execute(
            """INSERT INTO pending_approvals(run_id, bot_id, session_id, summary, requested_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET summary=excluded.summary,
               requested_at=excluded.requested_at, resolved_at=NULL, decision=NULL""",
            (
                values["run_id"], values["bot_id"], values["session_id"],
                values["summary"], values["requested_at"],
            ),
        )
        await self.commit()

    async def list_pending_approvals(self) -> list[dict[str, Any]]:
        return await self.fetchall(
            "SELECT run_id, bot_id, session_id, summary, requested_at FROM pending_approvals "
            "WHERE resolved_at IS NULL ORDER BY requested_at"
        )

    async def get_approval(self, run_id: str) -> dict[str, Any] | None:
        return await self.fetchone("SELECT * FROM pending_approvals WHERE run_id = ?", (run_id,))

    async def resolve_approval(self, run_id: str, decision: str, resolved_at: str) -> bool:
        cursor = await self.execute(
            "UPDATE pending_approvals SET resolved_at = ?, decision = ? WHERE run_id = ? AND resolved_at IS NULL",
            (resolved_at, decision, run_id),
        )
        await self.commit()
        return cursor.rowcount > 0


def json_or_none(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value

