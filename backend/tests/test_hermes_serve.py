"""Supervision of the `hermes serve` child.

The spawn is the slow part of every MCP OAuth authorization, so these cover the
pre-warm that takes it off the click path.
"""

from __future__ import annotations

import asyncio
import os
import stat
from pathlib import Path

import pytest

from botterd.config import Settings
from botterd.hermes_serve import HermesServe


FAKE_HERMES = """\
#!/bin/sh
echo "HERMES_BACKEND_READY port=54321"
# Stay alive so the parent keeps treating the child as ready.
exec sleep 30
"""


def settings_for(tmp_path: Path, *, script: str = FAKE_HERMES) -> Settings:
    hermes_home = tmp_path / "hermes"
    (hermes_home / "profiles").mkdir(parents=True)
    (hermes_home / "config.yaml").write_text("model:\n  default: provider/model\n", encoding="utf-8")
    binary = tmp_path / "bin/hermes"
    binary.parent.mkdir(parents=True)
    binary.write_text(script, encoding="utf-8")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return Settings(
        state_dir=tmp_path / "state",
        hermes_home=hermes_home,
        hermes_bin=binary,
        token_override="serve-token",
        api_server_key_override="api-server-key",
    )


async def wait_for(predicate, *, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never held")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_prewarm_spawns_so_the_next_request_does_not_have_to(tmp_path):
    serve = HermesServe(settings_for(tmp_path))
    try:
        serve.prewarm()
        await wait_for(lambda: serve.base_url is not None)
        warmed = serve.base_url
        first_pid = serve._process.pid  # type: ignore[union-attr]

        # The request that follows finds the child already up.
        assert await serve.ensure_ready() == warmed
        assert serve._process.pid == first_pid  # type: ignore[union-attr]

        # And warming an already-live child costs nothing.
        serve.prewarm()
        await asyncio.sleep(0.05)
        assert serve._process.pid == first_pid  # type: ignore[union-attr]
    finally:
        await serve.close()


@pytest.mark.asyncio
async def test_a_failing_prewarm_stays_out_of_the_callers_way(tmp_path):
    """A background warm-up must never be the error a user sees."""
    serve = HermesServe(settings_for(tmp_path, script="#!/bin/sh\nexit 1\n"))
    try:
        serve.prewarm()
        await wait_for(lambda: serve._prewarm_task is not None and serve._prewarm_task.done())
        assert serve._prewarm_task.exception() is None  # type: ignore[union-attr]
        assert serve.base_url is None
    finally:
        await serve.close()


@pytest.mark.asyncio
async def test_close_stops_an_in_flight_prewarm_without_orphaning_it(tmp_path):
    """Shutdown must not block on a spawn that holds the lock, nor leak it.

    A child part-way through its readiness handshake is the case to get right:
    it is serving nobody, and only the cancelled prewarm knows it exists.
    """
    never_ready = "#!/bin/sh\nexec sleep 30\n"
    serve = HermesServe(settings_for(tmp_path, script=never_ready))
    serve.prewarm()
    await wait_for(lambda: _child_pids() != [])
    spawned = _child_pids()

    await asyncio.wait_for(serve.close(), timeout=5)

    assert serve.base_url is None
    # The child is not recorded on the object at this point, so ask the OS.
    await wait_for(lambda: not set(spawned) & set(_child_pids()))


def _child_pids() -> list[int]:
    with os.popen(f"pgrep -P {os.getpid()}") as pgrep:
        return [int(line) for line in pgrep.read().split()]
