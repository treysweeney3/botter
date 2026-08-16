from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest

from botterd.config import Settings
from botterd.db import Database
from botterd.feed import FeedService, FeedWatcher
from botterd.models import Bot
from botterd.routines import RoutineService, read_executions, synthesized_execution_messages


EXECUTION_SCHEMA = """
CREATE TABLE executions (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    source TEXT NOT NULL,
    process_id TEXT NOT NULL,
    pid INTEGER NOT NULL,
    process_started_at INTEGER,
    status TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error TEXT
)
"""


async def insert_execution(
    path: Path,
    *,
    execution_id: str,
    status: str,
    claimed_at: str,
    error: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = await aiosqlite.connect(path)
    await connection.execute(EXECUTION_SCHEMA.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))
    await connection.execute(
        """INSERT INTO executions
           (id, job_id, source, process_id, pid, status, claimed_at, started_at, finished_at, error)
           VALUES (?, 'routine-1', 'ticker', 'process', 1, ?, ?, ?, ?, ?)""",
        (
            execution_id,
            status,
            claimed_at,
            claimed_at if status != "claimed" else None,
            claimed_at if status in {"completed", "failed", "unknown"} else None,
            error,
        ),
    )
    await connection.commit()
    await connection.close()


def settings_for(tmp_path: Path) -> Settings:
    return Settings(
        state_dir=tmp_path / "botter-state",
        hermes_home=tmp_path / "hermes",
        hermes_bin=tmp_path / "bin/hermes",
        token_override="token",
        api_server_key_override="key",
    )


def bot() -> Bot:
    now = datetime.now(timezone.utc)
    return Bot(
        id="bot-1",
        slug="research",
        display_name="Research",
        title="Analyst",
        description="Research",
        avatar_color="#8B5CF6",
        avatar_glyph="chart.bar.xaxis",
        approval_boundary="Ask",
        default_session_id="session-main",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_execution_history_normalizes_profile_ledger(tmp_path):
    db_path = tmp_path / "executions.db"
    await insert_execution(
        db_path,
        execution_id="execution-a",
        status="failed",
        claimed_at="2026-08-14T10:00:00+00:00",
        error="provider unavailable",
    )
    await insert_execution(
        db_path,
        execution_id="execution-b",
        status="completed",
        claimed_at="2026-08-14T11:00:00+00:00",
    )

    executions = await read_executions(db_path, "routine-1", 20)

    assert [execution.id for execution in executions] == ["execution-b", "execution-a"]
    assert executions[0].routine_id == "routine-1"
    assert executions[0].status == "completed"
    assert executions[1].summary == "provider unavailable"


@pytest.mark.asyncio
async def test_terminal_executions_become_virtual_default_thread_messages(tmp_path):
    profile = tmp_path / "research"
    cron = profile / "cron"
    cron.mkdir(parents=True)
    (cron / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": "routine-1", "name": "Morning review"}], "updated_at": "2026-08-14"}),
        encoding="utf-8",
    )
    await insert_execution(
        cron / "executions.db",
        execution_id="execution-a",
        status="failed",
        claimed_at="2026-08-14T10:00:00+00:00",
        error="provider unavailable",
    )

    messages = await synthesized_execution_messages(
        profile,
        bot_id="bot-1",
        session_id="session-main",
    )

    assert len(messages) == 1
    assert messages[0].id == "routine-execution:execution-a"
    assert messages[0].kind == "routine_created"
    assert messages[0].routine is not None
    assert messages[0].routine.name == "Morning review"
    assert messages[0].text == "Routine failed: Morning review — provider unavailable"


class EmptyHermes:
    async def get_messages(self, slug, session_id, *, limit=100):
        return []


class RecordingEvents:
    def __init__(self):
        self.items: list[tuple[str, dict]] = []

    async def publish(self, event: str, data: dict) -> None:
        self.items.append((event, data))


class BotRows:
    async def list_bots(self, *, include_archived=True):
        return [{"slug": "research", "archived": 0}]


@pytest.mark.asyncio
async def test_routine_service_reports_running_execution_for_auth_quiescence(tmp_path):
    settings = settings_for(tmp_path)
    ledger = settings.profiles_dir / "research" / "cron" / "executions.db"
    await insert_execution(
        ledger,
        execution_id="execution-running",
        status="running",
        claimed_at="2026-08-14T11:00:00+00:00",
    )
    service = RoutineService(
        settings,
        BotRows(),  # type: ignore[arg-type]
        EmptyHermes(),  # type: ignore[arg-type]
        RecordingEvents(),  # type: ignore[arg-type]
    )

    assert await service.has_active_executions() is True


@pytest.mark.asyncio
async def test_feed_merges_virtual_message_and_watcher_emits_completion(tmp_path):
    settings = settings_for(tmp_path)
    profile = settings.profiles_dir / "research"
    cron = profile / "cron"
    cron.mkdir(parents=True)
    (cron / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": "routine-1", "name": "Morning review"}], "updated_at": "2026-08-14"}),
        encoding="utf-8",
    )
    database = Database(settings.db_path)
    await database.connect()
    current_bot = bot()
    await database.insert_bot({**current_bot.model_dump(mode="json"), "archived": 0})
    events = RecordingEvents()
    watcher = FeedWatcher(settings, database, events)
    try:
        await watcher.poll_once()
        await insert_execution(
            cron / "executions.db",
            execution_id="execution-a",
            status="completed",
            claimed_at="2026-08-14T11:00:00+00:00",
        )
        await watcher.poll_once()

        assert (
            "routine_fired",
            {"bot_id": "bot-1", "routine_id": "routine-1", "name": "Morning review"},
        ) in events.items
        assert ("feed_updated", {"bot_id": "bot-1"}) in events.items

        messages = await FeedService(settings, database, EmptyHermes(), events).messages(
            current_bot, "session-main", limit=20
        )
        assert [message.id for message in messages] == ["routine-execution:execution-a"]
    finally:
        await database.close()
