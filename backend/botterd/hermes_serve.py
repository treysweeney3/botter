"""Supervised `hermes serve` child — Hermes' sanctioned management API.

The Hermes dashboard backend (`hermes serve`) is the same headless FastAPI
process the official desktop app drives. botterd spawns one on demand with a
self-minted session token (`HERMES_DASHBOARD_SESSION_TOKEN`), parses the
`HERMES_BACKEND_READY port=<n>` sentinel from stdout, and proxies a curated
subset of its `/api/*` surface. Hermes core is never patched.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import secrets
from contextlib import suppress
from typing import Any

import httpx

from .config import Settings
from .errors import APIError


logger = logging.getLogger(__name__)

READY_PATTERN = re.compile(r"HERMES_(?:BACKEND|DASHBOARD)_READY port=(\d+)")
READY_TIMEOUT_SECONDS = 120.0
SESSION_HEADER = "X-Hermes-Session-Token"


class HermesServe:
    """Lazily spawns and supervises one `hermes serve --port 0` child."""

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self._token = secrets.token_urlsafe(32)
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(30))
        self._owns_client = client is None
        self._process: asyncio.subprocess.Process | None = None
        self._base_url: str | None = None
        self._drain_tasks: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str | None:
        return self._base_url

    def _alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def _spawn(self) -> asyncio.subprocess.Process:
        environment = dict(os.environ)
        environment["HERMES_HOME"] = str(self.settings.hermes_home)
        environment["HERMES_DASHBOARD_SESSION_TOKEN"] = self._token
        # Hermes' serve-parent watchdog polls this PID and exits when it dies,
        # so a SIGKILLed botterd (launchctl kickstart -k) never leaks a child.
        environment["HERMES_PARENT_PID"] = str(os.getpid())
        return await asyncio.create_subprocess_exec(
            str(self.settings.hermes_bin),
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )

    async def _await_ready(self, process: asyncio.subprocess.Process) -> int:
        assert process.stdout is not None
        tail: list[str] = []
        deadline = asyncio.get_running_loop().time() + READY_TIMEOUT_SECONDS
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise RuntimeError("hermes serve did not report readiness in time")
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                raise RuntimeError("hermes serve did not report readiness in time") from None
            if not raw:
                raise RuntimeError(
                    "hermes serve exited before readiness: " + " | ".join(tail[-5:])
                )
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                tail.append(line)
            match = READY_PATTERN.search(line)
            if match:
                return int(match.group(1))

    async def _drain(self, stream: asyncio.StreamReader | None, label: str) -> None:
        if stream is None:
            return
        while True:
            raw = await stream.readline()
            if not raw:
                return
            logger.debug("hermes serve %s: %s", label, raw.decode("utf-8", errors="replace").rstrip())

    async def ensure_ready(self) -> str:
        async with self._lock:
            if self._alive() and self._base_url:
                return self._base_url
            await self._terminate_locked()
            process = await self._spawn()
            try:
                port = await self._await_ready(process)
            except RuntimeError as exc:
                with suppress(ProcessLookupError):
                    process.kill()
                with suppress(Exception):
                    await process.wait()
                raise APIError(502, "hermes_dashboard_unavailable", str(exc)) from exc
            self._process = process
            self._base_url = f"http://127.0.0.1:{port}"
            self._drain_tasks = [
                asyncio.create_task(self._drain(process.stdout, "stdout")),
                asyncio.create_task(self._drain(process.stderr, "stderr")),
            ]
            logger.info("hermes serve ready on %s (pid %s)", self._base_url, process.pid)
            return self._base_url

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
        retry: bool = True,
    ) -> Any:
        base = await self.ensure_ready()
        try:
            response = await self._client.request(
                method,
                f"{base}{path}",
                json=json,
                params=params,
                headers={SESSION_HEADER: self._token},
            )
        except httpx.HTTPError as exc:
            # A dead child leaves a poisoned base_url; respawn exactly once.
            async with self._lock:
                if self._base_url == base:
                    await self._terminate_locked()
            if retry:
                return await self.request(method, path, json=json, params=params, retry=False)
            raise APIError(502, "hermes_dashboard_unavailable", "Hermes dashboard API is unreachable") from exc
        if response.status_code >= 400:
            detail: Any = None
            with suppress(ValueError):
                detail = response.json().get("detail")
            message = detail if isinstance(detail, str) else "Hermes dashboard API request failed"
            code = {
                404: "channel_not_found",
                409: "channel_conflict",
                400: "invalid_channel_update",
            }.get(response.status_code, "hermes_dashboard_error")
            status = response.status_code if response.status_code in (400, 404, 409) else 502
            raise APIError(status, code, message)
        if not response.content:
            return None
        return response.json()

    async def _terminate_locked(self) -> None:
        for task in self._drain_tasks:
            task.cancel()
        self._drain_tasks = []
        process, self._process, self._base_url = self._process, None, None
        if process is None or process.returncode is not None:
            return
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except asyncio.TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            with suppress(Exception):
                await process.wait()

    async def close(self) -> None:
        async with self._lock:
            await self._terminate_locked()
        if self._owns_client:
            await self._client.aclose()
