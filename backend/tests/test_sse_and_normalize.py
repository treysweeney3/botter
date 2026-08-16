from __future__ import annotations

from botterd.hermes import HermesEvent, parse_chat_sse, parse_run_events_sse
from botterd.normalize import derive_task_items, normalize_completed_stream, normalize_rows


def test_chat_named_event_parser_strips_capture_harness(fixture_dir):
    lines = (fixture_dir / "chat_stream_notool.sse").read_text(encoding="utf-8").splitlines()
    events = parse_chat_sse(lines)
    assert [event.event for event in events] == [
        "run.started",
        "message.started",
        "assistant.delta",
        "tool.progress",
        "assistant.completed",
        "run.completed",
        "done",
    ]
    assert events[2].data["delta"] == "hi"


def test_run_data_only_event_parser_strips_capture_harness(fixture_dir):
    lines = (fixture_dir / "run_events.sse").read_text(encoding="utf-8").splitlines()
    events = parse_run_events_sse(lines)
    assert [event.event for event in events] == [
        "message.delta",
        "reasoning.available",
        "run.completed",
    ]
    assert events[-1].data["output"] == "hi"


def test_tool_fixture_derives_conservative_task_report(fixture_dir):
    lines = (fixture_dir / "chat_stream_tool.sse").read_text(encoding="utf-8").splitlines()
    events = parse_chat_sse(lines)
    items = derive_task_items(events)
    assert items
    assert items[0].label == "terminal → echo hello"
    assert items[0].state == "done"
    assert any(item.label.startswith("skill_view → hermes-agent") for item in items)

    message = normalize_completed_stream(events, bot_id="bot-1", session_id="session-1")
    assert message is not None
    assert message.kind == "task_report"
    assert message.task_items == items
    assert message.role == "assistant"


def test_narration_rows_fold_into_one_turn():
    """Hermes writes an assistant row per step; a turn stays one bubble."""
    rows = [
        {"id": 1, "role": "user", "content": "Google has been reconnected", "timestamp": 1},
        {
            "id": 2,
            "role": "assistant",
            "content": "Let me check whether the token is now reachable.",
            "tool_calls": '[{"id":"call-a","function":{"name":"terminal","arguments":"{\\"command\\":\\"ls /root\\"}"}}]',
            "timestamp": 2,
        },
        {"id": 3, "role": "tool", "tool_call_id": "call-a", "tool_name": "terminal", "content": "empty", "timestamp": 3},
        {
            "id": 4,
            "role": "assistant",
            "content": "No token found. Let me search the sandbox.",
            "tool_calls": '[{"id":"call-b","function":{"name":"terminal","arguments":"{\\"command\\":\\"find / -name token\\"}"}}]',
            "timestamp": 4,
        },
        {"id": 5, "role": "tool", "tool_call_id": "call-b", "tool_name": "terminal", "content": "nothing", "timestamp": 5},
        {"id": 6, "role": "assistant", "content": "The token is not in the sandbox.", "timestamp": 6},
    ]
    messages = normalize_rows(rows, bot_id="bot", session_id="session")
    assert [message.role for message in messages] == ["user", "assistant"]
    report = messages[1]
    assert report.kind == "task_report"
    assert report.text == "The token is not in the sandbox."
    assert [(item.state, item.label) for item in report.task_items] == [
        ("note", "Let me check whether the token is now reachable."),
        ("done", "terminal → ls /root"),
        ("note", "No token found. Let me search the sandbox."),
        ("done", "terminal → find / -name token"),
    ]


def test_wrapper_calls_pair_by_call_id_and_take_the_real_tool_name():
    """A `tool_call` wrapper hides the tool it runs; the result row names it."""
    rows = [
        {
            "id": 1,
            "role": "assistant",
            "content": "Let me look at Composio.",
            "tool_calls": (
                '[{"id":"call-1","function":{"name":"tool_call","arguments":"{}"}},'
                '{"id":"call-2","function":{"name":"tool_call","arguments":"{}"}}]'
            ),
            "timestamp": 1,
        },
        {"id": 2, "role": "tool", "tool_call_id": "call-2", "tool_name": "mcp__search", "content": "ok", "timestamp": 2},
        {"id": 3, "role": "tool", "tool_call_id": "call-1", "tool_name": "mcp__connect", "content": "ok", "timestamp": 3},
        {"id": 4, "role": "assistant", "content": "Here is the picture.", "timestamp": 4},
    ]
    items = normalize_rows(rows, bot_id="bot", session_id="session")[0].task_items
    assert [(item.state, item.label) for item in items] == [
        ("note", "Let me look at Composio."),
        ("done", "mcp__connect"),
        ("done", "mcp__search"),
    ]


def test_unfinished_turn_keeps_its_trace():
    """A stopped run has no closing row; the work still has to show."""
    rows = [
        {"id": 1, "role": "user", "content": "check the inbox", "timestamp": 1},
        {
            "id": 2,
            "role": "assistant",
            "content": "Let me open Gmail.",
            "tool_calls": '[{"id":"call-a","function":{"name":"terminal","arguments":"{\\"command\\":\\"gws list\\"}"}}]',
            "timestamp": 2,
        },
    ]
    messages = normalize_rows(rows, bot_id="bot", session_id="session")
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[1].text == ""
    assert [(item.state, item.label) for item in messages[1].task_items] == [
        ("note", "Let me open Gmail."),
        ("running", "terminal → gws list"),
    ]


def test_narration_notes_reach_a_live_task_report():
    events = [
        HermesEvent("assistant.note", {"text": "Let me check the token."}),
        HermesEvent("tool.started", {"tool_name": "terminal", "preview": "ls /root"}),
        HermesEvent("tool.completed", {"tool_name": "terminal", "preview": "empty"}),
    ]
    assert [(item.state, item.label) for item in derive_task_items(events)] == [
        ("note", "Let me check the token."),
        ("done", "terminal → ls /root"),
    ]


def test_persisted_tool_rows_reload_as_task_report():
    rows = [
        {
            "id": 1,
            "role": "assistant",
            "content": "",
            "tool_calls": '[{"function":{"name":"terminal","arguments":"{\\"command\\":\\"echo hi\\"}"}}]',
            "timestamp": 1,
        },
        {"id": 2, "role": "tool", "tool_name": "terminal", "content": "hi", "timestamp": 2},
        {"id": 3, "role": "assistant", "content": "Finished.", "timestamp": 3},
    ]
    messages = normalize_rows(rows, bot_id="bot", session_id="session")
    assert len(messages) == 1
    assert messages[0].kind == "task_report"
    assert messages[0].task_items[0].label == "terminal → echo hi"
