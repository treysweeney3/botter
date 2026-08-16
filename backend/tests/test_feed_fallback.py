from __future__ import annotations

from datetime import datetime, timezone

import aiosqlite
import pytest

from botterd.config import Settings
from botterd.db import Database
from botterd.events import EventBus
from botterd.feed import FeedService, read_profile_messages, search_profile_messages
from botterd.models import Bot


class OfflineHermes:
    async def list_sessions(self, slug):
        raise RuntimeError("offline")


@pytest.mark.asyncio
async def test_feed_uses_read_only_sqlite_fallback(tmp_path):
    settings = Settings(
        state_dir=tmp_path / "botter-state",
        hermes_home=tmp_path / "hermes",
        hermes_bin=tmp_path / "bin/hermes",
        token_override="token",
        api_server_key_override="key",
    )
    profile = settings.profiles_dir / "research"
    profile.mkdir(parents=True)
    state_db = profile / "state.db"
    connection = await aiosqlite.connect(state_db)
    await connection.execute(
        """CREATE TABLE messages(
               id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
               tool_calls TEXT, tool_name TEXT, timestamp REAL, active INTEGER DEFAULT 1,
               compacted INTEGER DEFAULT 0
           )"""
    )
    await connection.executemany(
        "INSERT INTO messages(id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
        [
            (1, "s1", "user", "question", 1.0),
            (2, "s1", "assistant", "fallback preview", 2.0),
        ],
    )
    await connection.execute(
        "CREATE VIRTUAL TABLE messages_fts USING fts5(content, tool_name, tool_calls, content='messages', content_rowid='id')"
    )
    await connection.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
    await connection.commit()
    await connection.close()

    rows = await read_profile_messages(state_db, session_id="s1", limit=10)
    assert [row["id"] for row in rows] == [2, 1]
    search_rows = await search_profile_messages(state_db, "fallback", limit=10)
    assert [row["id"] for row in search_rows] == [2]

    db = Database(settings.db_path)
    await db.connect()
    bot = Bot(
        id="bot-1", slug="research", display_name="Research", title="Analyst", description="Research",
        avatar_color="#8B5CF6", avatar_glyph="chart.bar.xaxis", approval_boundary="Ask",
        default_session_id="s1", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    await db.insert_bot({**bot.model_dump(mode="json"), "archived": 0})
    try:
        item = await FeedService(settings, db, OfflineHermes(), EventBus()).roster_item(bot)
        assert item.latest_message_preview == "fallback preview"
        assert item.unread_count == 1
    finally:
        await db.close()
