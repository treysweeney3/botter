"""Hermes row and event normalization into the public message schema."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Iterable

from .hermes import HermesEvent
from .models import ImageAttachment, NormalizedMessage, TaskItem


def normalize_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _structured_content(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return decoded


def _text_content(value: Any) -> str:
    if value is None:
        return ""
    value = _structured_content(value)
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(
            str(item.get("text", ""))
            if isinstance(item, dict) and item.get("type") in {None, "text", "input_text", "output_text"}
            else str(item) if not isinstance(item, dict)
            else ""
            for item in value
        ).strip()
    return str(value)


def _image_attachments(value: Any) -> list[ImageAttachment]:
    value = _structured_content(value)
    if not isinstance(value, list):
        return []
    attachments: list[ImageAttachment] = []
    for item in value:
        if not isinstance(item, dict) or item.get("type") not in {"image_url", "input_image"}:
            continue
        image_ref = item.get("image_url")
        url = image_ref.get("url") if isinstance(image_ref, dict) else image_ref
        if not isinstance(url, str) or not url:
            continue
        media_type = "image"
        if url.lower().startswith("data:image/"):
            media_type = url[5:].split(";", 1)[0].lower()
        attachments.append(
            ImageAttachment(
                url=url,
                media_type=media_type,
                filename=item.get("filename") if isinstance(item.get("filename"), str) else None,
            )
        )
    return attachments


def normalize_row(row: dict[str, Any], *, bot_id: str, session_id: str | None = None) -> NormalizedMessage:
    role = str(row.get("role") or "assistant")
    if role == "tool":
        role = "system"
    if role not in {"user", "assistant", "system"}:
        role = "system"
    content = row.get("content", row.get("text", ""))
    attachments = _image_attachments(content)
    return NormalizedMessage(
        id=str(row.get("id") or row.get("message_id") or "unknown"),
        session_id=str(session_id or row.get("session_id") or ""),
        bot_id=bot_id,
        role=role,
        kind="attachment" if attachments else "text",
        text=_text_content(content),
        attachments=attachments,
        created_at=normalize_datetime(row.get("timestamp", row.get("created_at"))),
    )


def _parse_tool_calls(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [call for call in value if isinstance(call, dict)]


def _tool_step(call: dict[str, Any]) -> dict[str, Any]:
    """One pending tool step, keyed by the call id Hermes echoes on the result row."""
    function = call.get("function") if isinstance(call.get("function"), dict) else call
    name = str(function.get("name") or call.get("name") or "tool")
    arguments = function.get("arguments", call.get("args"))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError:
            pass
    if isinstance(arguments, dict):
        preview = next(
            (str(arguments[key]) for key in ("command", "query", "path", "url") if arguments.get(key)),
            "",
        )
    else:
        preview = str(arguments or "")
    call_id = call.get("call_id") or call.get("id")
    return {
        "kind": "tool",
        "call_id": str(call_id) if call_id else None,
        "name": name,
        "preview": preview[:100],
    }


def _tool_label(name: str, preview: str) -> str:
    return f"{name} → {preview}" if preview else name


def _tool_result(row: dict[str, Any]) -> tuple[str, str]:
    detail = _text_content(row.get("content")).strip().replace("\n", " ")[:160]
    failure = str(row.get("effect_disposition") or "").lower() in {"failed", "error"}
    if not failure:
        failure = detail.lower().startswith(("error", "failed"))
    return detail, "failed" if failure else "done"


def _trace_items(trace: Iterable[dict[str, Any]]) -> list[TaskItem]:
    items: list[TaskItem] = []
    for step in trace:
        if step["kind"] == "note":
            items.append(TaskItem(label=step["label"], detail="", state="note"))
            continue
        # A call with no result row belongs to a turn that never finished.
        state = step.get("state", "running")
        items.append(
            TaskItem(label=_tool_label(step["name"], step["preview"]), detail=step.get("detail", ""), state=state)
        )
    return items


def normalize_rows(rows: Iterable[dict[str, Any]], *, bot_id: str, session_id: str) -> list[NormalizedMessage]:
    """One assistant message for each turn, with the work between as its trace.

    Hermes persists an assistant row for every step of a turn. Each interim row
    carries `tool_calls` and, usually, a sentence of narration. Only the closing
    row holds the answer. Interim rows therefore become trace items — never
    bubbles of their own.
    """
    normalized: list[NormalizedMessage] = []
    trace: list[dict[str, Any]] = []
    last_interim: dict[str, Any] | None = None

    def flush_unfinished() -> None:
        """Keep the trace of a turn that never produced a closing row."""
        nonlocal trace, last_interim
        if trace and last_interim is not None:
            message = normalize_row(last_interim, bot_id=bot_id, session_id=session_id)
            normalized.append(
                message.model_copy(
                    update={"kind": "task_report", "text": "", "attachments": [], "task_items": _trace_items(trace)}
                )
            )
        trace = []
        last_interim = None

    for row in rows:
        role = row.get("role")
        if role == "tool":
            call_id = str(row.get("tool_call_id") or "")
            name = str(row.get("tool_name") or "")
            detail, state = _tool_result(row)
            pending = next(
                (
                    step
                    for step in trace
                    if step["kind"] == "tool"
                    and "state" not in step
                    and (step["call_id"] == call_id if call_id and step["call_id"] else step["name"] == name)
                ),
                None,
            )
            if pending is None:
                # A result with no matching call still describes real work.
                trace.append({"kind": "tool", "call_id": call_id or None, "name": name or "tool", "preview": ""})
                pending = trace[-1]
            # The result row names the tool that ran; a wrapper call does not.
            if name:
                pending["name"] = name
            pending.update(detail=detail, state=state)
            continue
        if role not in {"user", "assistant", "system"}:
            continue
        message = normalize_row(row, bot_id=bot_id, session_id=session_id)
        if role == "user":
            flush_unfinished()
            normalized.append(message)
            continue
        if role == "system":
            normalized.append(message)
            continue
        calls = _parse_tool_calls(row.get("tool_calls"))
        if calls:
            if message.text.strip():
                trace.append({"kind": "note", "label": message.text.strip()})
            trace.extend(_tool_step(call) for call in calls)
            last_interim = row
            continue
        items = _trace_items(trace)
        trace = []
        last_interim = None
        if not message.text and not message.attachments and not items:
            continue
        if items and message.kind == "text":
            message = message.model_copy(update={"kind": "task_report", "task_items": items})
        normalized.append(message)
    flush_unfinished()
    return normalized


def derive_task_items(events: Iterable[HermesEvent]) -> list[TaskItem]:
    """Conservatively pair tool.started with tool.completed/tool.failed.

    `assistant.note` is synthesized by the chat consumer from the narration that
    precedes a tool call. It keeps a live trace equal to the reloaded one.
    """
    pending: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    items: list[TaskItem] = []
    for event in events:
        if event.event == "assistant.note":
            text = str(event.data.get("text") or "").strip()
            if text:
                items.append(TaskItem(label=text, detail="", state="note"))
        elif event.event == "tool.started":
            tool_name = str(event.data.get("tool_name") or "tool")
            pending[tool_name].append(event.data)
        elif event.event in {"tool.completed", "tool.failed"}:
            tool_name = str(event.data.get("tool_name") or "tool")
            if not pending[tool_name]:
                continue
            started = pending[tool_name].popleft()
            preview = str(started.get("preview") or "").strip()
            label = f"{tool_name} → {preview}" if preview else tool_name
            detail = str(event.data.get("preview") or "").strip()
            items.append(
                TaskItem(label=label, detail=detail, state="failed" if event.event == "tool.failed" else "done")
            )
    return items


def normalize_completed_stream(
    events: Iterable[HermesEvent], *, bot_id: str, session_id: str
) -> NormalizedMessage | None:
    materialized = list(events)
    completed = next((event for event in reversed(materialized) if event.event in {"assistant.completed", "run.completed"}), None)
    if completed is None:
        return None
    message_id = str(completed.data.get("message_id") or "")
    text = str(completed.data.get("content") or completed.data.get("output") or "")
    items = derive_task_items(materialized)
    return NormalizedMessage(
        id=message_id or f"run-message-{completed.data.get('run_id', 'unknown')}",
        session_id=session_id,
        bot_id=bot_id,
        role="assistant",
        kind="task_report" if items else "text",
        text=text,
        task_items=items,
        created_at=normalize_datetime(completed.data.get("ts", completed.data.get("timestamp"))),
    )
