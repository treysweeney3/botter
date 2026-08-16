"""Typed async adapter for the multiplexed Hermes api_server."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


CAPTURE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z(?:\s(.*))?$")


@dataclass(slots=True, frozen=True)
class HermesEvent:
    event: str
    data: dict[str, Any]


class HermesError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def clean_capture_line(raw_line: str) -> str | None:
    """Remove the Phase 0 harness timestamp column and harness sentinels."""
    line = raw_line.rstrip("\r\n")
    if line.startswith("START_UTC ") or line.startswith("END_UTC "):
        return None
    match = CAPTURE_PREFIX.match(line)
    if match:
        return match.group(1) or ""
    return line


def _decode_json(data: str, *, dialect: str) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {dialect} SSE JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid {dialect} SSE payload: expected object")
    return payload


def parse_chat_sse(lines: Iterable[str]) -> list[HermesEvent]:
    """Parse Hermes session chat named-event SSE framing."""
    events: list[HermesEvent] = []
    event_name: str | None = None
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_name, data_lines
        if data_lines:
            payload = _decode_json("\n".join(data_lines), dialect="chat-stream")
            events.append(HermesEvent(event_name or str(payload.get("event") or "message"), payload))
        event_name = None
        data_lines = []

    for raw_line in lines:
        line = clean_capture_line(raw_line)
        if line is None:
            continue
        if line == "":
            flush()
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush()
    return events


def parse_run_events_sse(lines: Iterable[str]) -> list[HermesEvent]:
    """Parse Hermes runs data-only SSE framing with event inside JSON."""
    events: list[HermesEvent] = []
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal data_lines
        if data_lines:
            payload = _decode_json("\n".join(data_lines), dialect="run-events")
            name = payload.get("event")
            if not isinstance(name, str) or not name:
                raise ValueError("Invalid run-events SSE payload: missing event")
            events.append(HermesEvent(name, payload))
        data_lines = []

    for raw_line in lines:
        line = clean_capture_line(raw_line)
        if line is None:
            continue
        if line == "":
            flush()
        elif line.startswith(":"):
            continue
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush()
    return events


async def parse_chat_sse_async(lines: AsyncIterable[str]) -> AsyncIterator[HermesEvent]:
    buffered: list[str] = []
    async for line in lines:
        cleaned = clean_capture_line(line)
        if cleaned is None:
            continue
        if cleaned == "":
            for event in parse_chat_sse(buffered + [""]):
                yield event
            buffered = []
        else:
            buffered.append(cleaned)
    for event in parse_chat_sse(buffered):
        yield event


async def parse_run_events_sse_async(lines: AsyncIterable[str]) -> AsyncIterator[HermesEvent]:
    buffered: list[str] = []
    async for line in lines:
        cleaned = clean_capture_line(line)
        if cleaned is None:
            continue
        if cleaned == "":
            for event in parse_run_events_sse(buffered + [""]):
                yield event
            buffered = []
        else:
            buffered.append(cleaned)
    for event in parse_run_events_sse(buffered):
        yield event


class HermesClient:
    def __init__(self, client: httpx.AsyncClient, base_url: str, api_key: str, default_model: str):
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.default_model = default_model

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def profile_url(self, slug: str, path: str) -> str:
        if slug in {"main", "default"}:
            raise ValueError("main/default is not a bot profile")
        return f"{self.base_url}/p/{quote(slug, safe='')}{path}"

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        raw_error = payload.get("error", {}) if isinstance(payload, dict) else {}
        if isinstance(raw_error, dict):
            code = str(raw_error.get("code") or "hermes_error")
            message = str(raw_error.get("message") or response.reason_phrase)
        else:
            code = "hermes_error"
            message = str(raw_error or response.reason_phrase)
        raise HermesError(response.status_code, code, message)

    async def request(self, method: str, slug: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.client.request(method, self.profile_url(slug, path), headers=self.headers, **kwargs)
        except httpx.HTTPError as exc:
            raise HermesError(502, "hermes_unavailable", "Hermes api_server is unavailable") from exc
        self._raise(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise HermesError(502, "invalid_hermes_response", "Hermes returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HermesError(502, "invalid_hermes_response", "Hermes returned a non-object response")
        return payload

    async def health(self) -> dict[str, Any]:
        try:
            response = await self.client.get(f"{self.base_url}/health")
        except httpx.HTTPError as exc:
            raise HermesError(502, "hermes_unavailable", "Hermes api_server is unavailable") from exc
        self._raise(response)
        payload = response.json()
        return payload if isinstance(payload, dict) else {"status": "ok"}

    async def health_detailed(self) -> dict[str, Any]:
        try:
            response = await self.client.get(f"{self.base_url}/health/detailed", headers=self.headers)
        except httpx.HTTPError as exc:
            raise HermesError(502, "hermes_unavailable", "Hermes api_server is unavailable") from exc
        self._raise(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise HermesError(502, "invalid_hermes_response", "Hermes returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise HermesError(502, "invalid_hermes_response", "Hermes returned a non-object response")
        return payload

    async def list_sessions(self, slug: str) -> list[dict[str, Any]]:
        payload = await self.request("GET", slug, "/api/sessions", params={"limit": 200})
        return list(payload.get("data") or [])

    async def create_session(self, slug: str, *, title: str | None = None, model: str | None = None) -> dict[str, Any]:
        # Hermes' implicit literal `hermes-agent` default is invalid on this install.
        body: dict[str, Any] = {"model": model or self.default_model}
        if title:
            body["title"] = title
        payload = await self.request("POST", slug, "/api/sessions", json=body)
        return dict(payload.get("session") or {})

    async def get_messages(self, slug: str, session_id: str, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        payload = await self.request(
            "GET", slug, f"/api/sessions/{quote(session_id, safe='')}/messages",
            params={"limit": limit, "offset": offset, "order": "latest"},
        )
        return list(payload.get("data") or [])

    async def start_run(self, slug: str, session_id: str, message: str | list[dict[str, Any]]) -> dict[str, Any]:
        history_rows = await self.get_messages(slug, session_id, limit=500)
        history: list[dict[str, str]] = []
        for row in history_rows:
            if row.get("role") not in {"user", "assistant", "system"}:
                continue
            content = row.get("content") or ""
            if isinstance(content, list):
                content = "\n".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") in {"text", "input_text"}
                )
            history.append({"role": str(row["role"]), "content": str(content)})
        run_input: str | list[dict[str, Any]]
        if isinstance(message, str):
            run_input = message
        else:
            # Hermes /v1/runs treats an input array as messages and preserves
            # the last message's structured content for multimodal models.
            run_input = [{"role": "user", "content": message}]
        return await self.request(
            "POST",
            slug,
            "/v1/runs",
            json={"input": run_input, "session_id": session_id, "conversation_history": history},
        )

    async def stream_run_events(self, slug: str, run_id: str) -> AsyncIterator[HermesEvent]:
        async with self.client.stream(
            "GET", self.profile_url(slug, f"/v1/runs/{quote(run_id, safe='')}/events"), headers=self.headers,
            timeout=httpx.Timeout(30, read=None),
        ) as response:
            if not response.is_success:
                await response.aread()
            self._raise(response)
            async for event in parse_run_events_sse_async(response.aiter_lines()):
                yield event

    async def stream_chat(self, slug: str, session_id: str, message: str) -> AsyncIterator[HermesEvent]:
        async with self.client.stream(
            "POST",
            self.profile_url(slug, f"/api/sessions/{quote(session_id, safe='')}/chat/stream"),
            headers=self.headers,
            json={"message": message},
            timeout=httpx.Timeout(30, read=None),
        ) as response:
            if not response.is_success:
                await response.aread()
            self._raise(response)
            async for event in parse_chat_sse_async(response.aiter_lines()):
                yield event

    async def approve(self, slug: str, run_id: str, decision: str) -> dict[str, Any]:
        # Public Botter uses `decision`; Hermes' source-verified field is `choice`.
        return await self.request(
            "POST", slug, f"/v1/runs/{quote(run_id, safe='')}/approval", json={"choice": decision}
        )

    async def stop_run(self, slug: str, run_id: str) -> dict[str, Any]:
        return await self.request("POST", slug, f"/v1/runs/{quote(run_id, safe='')}/stop")

    async def list_jobs(self, slug: str) -> list[dict[str, Any]]:
        payload = await self.request("GET", slug, "/api/jobs", params={"include_disabled": "true"})
        return list(payload.get("jobs") or [])

    async def create_job(self, slug: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = await self.request("POST", slug, "/api/jobs", json=body)
        return dict(payload.get("job") or {})

    async def get_job(self, slug: str, job_id: str) -> dict[str, Any]:
        payload = await self.request("GET", slug, f"/api/jobs/{quote(job_id, safe='')}")
        return dict(payload.get("job") or {})

    async def update_job(self, slug: str, job_id: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = await self.request("PATCH", slug, f"/api/jobs/{quote(job_id, safe='')}", json=body)
        return dict(payload.get("job") or {})

    async def delete_job(self, slug: str, job_id: str) -> None:
        await self.request("DELETE", slug, f"/api/jobs/{quote(job_id, safe='')}")

    async def job_action(self, slug: str, job_id: str, action: str) -> dict[str, Any]:
        payload = await self.request("POST", slug, f"/api/jobs/{quote(job_id, safe='')}/{action}")
        return dict(payload.get("job") or {})
