"""Bot registry and Hermes profile lifecycle management."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .yaml_io import YAMLError, load_yaml, write_yaml_atomic

from .config import Settings
from .db import Database
from .errors import APIError
from .events import EventBus
from .hermes import HermesClient
from .models import Bot, BotCreate, BotPatch

if TYPE_CHECKING:
    from .global_auth import GlobalAuth


logger = logging.getLogger(__name__)
SLUG_PATTERN = re.compile(r"^[a-z0-9-]{1,32}$")
RESERVED_PROFILES = frozenset({"main", "default"})
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SHARED_EGRESS_ARTIFACTS = ("proxy.yaml", "ca.crt", "mappings.json", "iron-proxy.pid")


@dataclass(slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    async def run(self, args: Sequence[str], *, timeout: float = 120, check: bool = True) -> CommandResult: ...


class SubprocessRunner:
    async def run(self, args: Sequence[str], *, timeout: float = 120, check: bool = True) -> CommandResult:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError(f"Command timed out: {args[0]}") from None
        result = CommandResult(
            returncode=process.returncode or 0,
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"Command failed ({result.returncode}): {args[0]}")
        return result


def validate_slug(slug: str) -> str:
    if slug in RESERVED_PROFILES:
        raise APIError(400, "reserved_slug", "main and default profiles cannot be managed as bots")
    if not SLUG_PATTERN.fullmatch(slug):
        raise APIError(422, "invalid_slug", "slug must match [a-z0-9-]{1,32}")
    return slug


def render_soul(bot: BotCreate | Bot) -> str:
    return (
        f"# {bot.display_name} — {bot.title}\n\n"
        # The owner's identity is deliberately absent: Hermes already maintains
        # USER.md per profile, so naming the user here would duplicate it and
        # go stale. The persona states the role; Hermes supplies who it is for.
        f"You are {bot.display_name}, {bot.title}. {bot.description}\n\n"
        "## Working style\n"
        "- Answer in one message. Do not announce a step before you take it, and do not\n"
        "  post progress updates between tool calls. Do the work, then report once.\n"
        "- Report finished work as a short summary followed by a checklist of steps taken\n"
        "  in the form \"✓ <system> → <action> · <result>\".\n"
        "- Keep long-term notes about this role in memory; cite live data for decisions.\n\n"
        "## Approval boundary\n"
        f"{bot.approval_boundary}\n"
        "Never take actions beyond this boundary without asking for approval first.\n"
    )


async def restart_gateway(
    runner: CommandRunner,
    health_check: Callable[[], Awaitable[bool]],
) -> None:
    """Restart Hermes and survive the observed dying-listener bind race."""
    healthy = False
    for attempt in range(3):
        await runner.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/ai.hermes.gateway"],
            check=False,
        )
        deadline = 30 if attempt == 0 else 45
        for _ in range(deadline):
            if await health_check():
                healthy = True
                break
            await asyncio.sleep(1)
        if healthy:
            break
    if not healthy:
        raise RuntimeError("Hermes gateway did not become healthy after restart")


# Platforms that bind a port. With `gateway.multiplex_profiles` on, only the
# default profile may enable these. A clone that inherits one is skipped by the
# gateway entirely — the bot then exists but is never served.
PORT_BINDING_PLATFORMS = ("api_server", "webhook", "msgraph_webhook", "wecom_callback")


def apply_profile_config_deltas(config_path: Path) -> None:
    """Disable inherited Slack and port-binding platforms, and remove cloned host mounts.

    Everything else the clone inherited is left alone on purpose — `mcp_servers`
    in particular, so a new bot starts with the same MCP tools as the rest.
    The edit is comment-preserving, so a bot's config stays as readable as the
    main one it was cloned from.
    """
    try:
        config = load_yaml(config_path)
    except (OSError, UnicodeError, YAMLError) as exc:
        raise RuntimeError(f"Unable to read cloned profile config: {config_path}") from exc
    if not isinstance(config, dict):
        raise RuntimeError("Cloned profile config must be a YAML mapping")
    platforms = config.setdefault("platforms", {})
    if not isinstance(platforms, dict):
        platforms = config["platforms"] = {}
    slack = platforms.setdefault("slack", {})
    if not isinstance(slack, dict):
        slack = platforms["slack"] = {}
    slack["enabled"] = False
    # Main owns the single shared HTTP listener and serves each bot under its
    # /p/<slug>/ prefix. A clone that keeps these inherited entries makes the
    # gateway skip the whole profile, so the bot never answers.
    for name in PORT_BINDING_PLATFORMS:
        entry = platforms.get(name)
        if isinstance(entry, dict):
            entry["enabled"] = False
    terminal = config.setdefault("terminal", {})
    if not isinstance(terminal, dict):
        terminal = config["terminal"] = {}
    # Clone evidence showed a company-vault host mount. Botter has no basis to
    # infer role-specific host access, so a new bot starts with no host mounts.
    terminal["docker_volumes"] = []
    proxy = config.setdefault("proxy", {})
    if not isinstance(proxy, dict):
        proxy = config["proxy"] = {}
    # Bot profiles fail closed through the shared main iron-proxy. Make this
    # explicit instead of relying on whatever the cloned source config held.
    proxy["enabled"] = True
    proxy["enforce_on_docker"] = True
    write_yaml_atomic(config_path, config)


def _egress_links_match(target_dir: Path, source_dir: Path) -> bool:
    if not target_dir.is_dir() or target_dir.is_symlink():
        return False
    for name in SHARED_EGRESS_ARTIFACTS:
        link = target_dir / name
        if not link.is_symlink():
            return False
        try:
            if link.resolve(strict=True) != (source_dir / name).resolve(strict=True):
                return False
        except OSError:
            return False
    return True


async def provision_profile_egress(
    slug: str,
    runner: CommandRunner,
    *,
    hermes_home: Path,
    hermes_bin: Path,
) -> None:
    """Give a bot read-through access to the main profile's iron-proxy.

    Hermes scopes proxy state beneath ``HERMES_HOME`` and has no shared-state
    config key. One daemon can serve every sandbox, however, because its token
    mappings are provider-scoped rather than profile-scoped. A real profile
    ``proxy`` directory with individual links keeps main-owned rotations and
    restarts visible without exposing the rest of main's proxy state through a
    directory symlink.
    """
    validate_slug(slug)
    source_dir = hermes_home / "proxy"
    profile_dir = hermes_home / "profiles" / slug
    target_dir = profile_dir / "proxy"

    status = await runner.run(
        [str(hermes_bin), "-p", "default", "egress", "status"],
        timeout=30,
        check=False,
    )
    status_text = ANSI_ESCAPE.sub("", f"{status.stdout}\n{status.stderr}")
    if status.returncode != 0 or not re.search(r"\bListening\s+yes\b", status_text, re.IGNORECASE):
        raise RuntimeError("Main iron-proxy is not healthy and listening; refusing direct bot egress")

    missing = [name for name in SHARED_EGRESS_ARTIFACTS if not (source_dir / name).is_file()]
    if missing:
        raise RuntimeError(f"Main iron-proxy is missing required state: {', '.join(missing)}")
    try:
        mappings = json.loads((source_dir / "mappings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Main iron-proxy mappings are unreadable") from exc
    tokens = mappings.get("tokens") if isinstance(mappings, dict) else None
    if not isinstance(tokens, list) or not tokens:
        raise RuntimeError("Main iron-proxy has no provider token mappings")

    if target_dir.exists() or target_dir.is_symlink():
        if _egress_links_match(target_dir, source_dir):
            return
        raise RuntimeError(f"Refusing to replace unexpected profile proxy state: {target_dir}")

    staging = profile_dir / f".proxy.botter-{uuid.uuid4().hex}"
    created_links: list[Path] = []
    try:
        staging.mkdir(mode=0o700)
        for name in SHARED_EGRESS_ARTIFACTS:
            link = staging / name
            os.symlink(source_dir / name, link)
            created_links.append(link)
        os.replace(staging, target_dir)
    except Exception:
        for link in reversed(created_links):
            link.unlink(missing_ok=True)
        try:
            staging.rmdir()
        except FileNotFoundError:
            pass
        raise


class Registry:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        hermes: HermesClient,
        events: EventBus,
        *,
        runner: CommandRunner | None = None,
        health_check: Callable[[], Awaitable[bool]] | None = None,
        global_auth: GlobalAuth | None = None,
    ):
        self.settings = settings
        self.db = db
        self.hermes = hermes
        self.events = events
        self.runner = runner or SubprocessRunner()
        self.health_check = health_check or self._gateway_healthy
        self.global_auth = global_auth

    async def _gateway_healthy(self) -> bool:
        try:
            await self.hermes.health()
            return True
        except Exception:
            return False

    async def list(self, *, include_archived: bool = True) -> list[Bot]:
        return [Bot.model_validate(row) for row in await self.db.list_bots(include_archived=include_archived)]

    async def get(self, bot_id: str) -> Bot:
        row = await self.db.get_bot(bot_id)
        if row is None:
            raise APIError(404, "bot_not_found", f"Bot not found: {bot_id}")
        return Bot.model_validate(row)

    async def create(self, request: BotCreate) -> Bot:
        slug = validate_slug(request.slug)
        profile_path = self.settings.profiles_dir / slug
        auth_lock_acquired = False
        if self.global_auth is not None:
            await self.global_auth.lock.acquire()
            auth_lock_acquired = True
        created_profile = False
        target_available = False
        try:
            if await self.db.get_bot_by_slug(slug):
                raise APIError(409, "slug_exists", f"A bot already uses slug: {slug}")
            if profile_path.exists():
                raise APIError(409, "profile_exists", f"A Hermes profile already exists: {slug}")
            target_available = True
            await self.runner.run(
                [str(self.settings.hermes_bin), "profile", "create", slug, "--clone", "--description", request.description],
                timeout=180,
            )
            created_profile = True
            (profile_path / "SOUL.md").write_text(render_soul(request), encoding="utf-8")
            apply_profile_config_deltas(profile_path / "config.yaml")
            await provision_profile_egress(
                slug,
                self.runner,
                hermes_home=self.settings.hermes_home,
                hermes_bin=self.settings.hermes_bin,
            )
            if self.global_auth is not None:
                await self.global_auth.reconcile_new_profile_locked(slug)
            session = await self.hermes.create_session(
                slug,
                title=f"{request.display_name} main",
                model=request.model or self.hermes.default_model,
            )
            now = datetime.now(timezone.utc).isoformat()
            values = request.model_dump(exclude={"model"})
            values.update(
                id=str(uuid.uuid4()),
                default_session_id=str(session["id"]),
                archived=0,
                created_at=now,
                updated_at=now,
            )
            await self.db.insert_bot(values)
            bot = await self.get(values["id"])
            await self.events.publish("bot_updated", {"bot_id": bot.id})
            return bot
        except APIError:
            if target_available and (created_profile or profile_path.exists()):
                await self.purge_profile(slug)
            raise
        except Exception as exc:
            if target_available and (created_profile or profile_path.exists()):
                try:
                    await self.purge_profile(slug)
                except Exception:
                    logger.exception("profile rollback failed for slug=%s", slug)
            raise APIError(502, "bot_create_failed", f"Unable to create bot: {exc}") from exc
        finally:
            if auth_lock_acquired:
                self.global_auth.lock.release()

    async def patch(self, bot_id: str, request: BotPatch) -> Bot:
        bot = await self.get(bot_id)
        values = request.model_dump(exclude_none=True)
        if not values:
            return bot
        description = values.get("description")
        if description is not None and description != bot.description:
            # `profile.yaml`'s description is what the Hermes kanban orchestrator
            # routes work with. `profile create --description` only seeds it, so
            # without this the Hermes-side copy freezes at the create-time text.
            # Run it before any local write: a failure here leaves SOUL.md, the
            # profile, and the row all untouched.
            try:
                await self.runner.run(
                    [str(self.settings.hermes_bin), "profile", "describe", bot.slug, "--text", description],
                    timeout=60,
                )
            except Exception as exc:
                raise APIError(
                    502, "bot_update_failed", f"Unable to update the Hermes profile description: {exc}"
                ) from exc
        persona_fields = {"display_name", "title", "description", "approval_boundary"}
        if persona_fields.intersection(values):
            candidate = bot.model_copy(update=values)
            soul_path = self.settings.profiles_dir / bot.slug / "SOUL.md"
            temporary = soul_path.with_name(".SOUL.md.botter.tmp")
            temporary.write_text(render_soul(candidate), encoding="utf-8")
            os.replace(temporary, soul_path)
        values["updated_at"] = datetime.now(timezone.utc).isoformat()
        row = await self.db.update_bot(bot_id, values)
        updated = Bot.model_validate(row)
        await self.events.publish("bot_updated", {"bot_id": bot_id})
        return updated

    async def archive(self, bot_id: str) -> Bot:
        return await self.patch(bot_id, BotPatch(archived=True))

    async def purge(self, bot_id: str) -> None:
        if self.global_auth is None:
            bot = await self.get(bot_id)
            await self.purge_profile(bot.slug)
            await self.db.delete_bot(bot_id)
            await self.events.publish("bot_updated", {"bot_id": bot_id})
            return
        async with self.global_auth.lock:
            bot = await self.get(bot_id)
            await self.purge_profile(bot.slug)
            await self.db.delete_bot(bot_id)
            await self.events.publish("bot_updated", {"bot_id": bot_id})

    async def purge_profile(self, slug: str) -> None:
        """Perform the verified six-step destructive Hermes profile purge."""
        validate_slug(slug)
        profile_path = self.settings.profiles_dir / slug
        wrapper_path = self.settings.wrapper_dir / slug

        # 1. Stop sandbox containers whose mounts reference this exact profile.
        docker = str(self.settings.docker_bin)
        listed = await self.runner.run(
            [docker, "ps", "-q", "--filter", "name=hermes-"], check=False
        )
        for container_id in listed.stdout.split():
            inspected = await self.runner.run(
                [docker, "inspect", container_id, "--format", "{{range .Mounts}}{{.Source}} {{end}}"],
                check=False,
            )
            expected_profile = profile_path.resolve(strict=False)
            mounted_paths = [Path(value).resolve(strict=False) for value in inspected.stdout.split()]
            if any(path == expected_profile or path.is_relative_to(expected_profile) for path in mounted_paths):
                await self.runner.run([docker, "stop", container_id], check=False)

        # 2. Use Hermes' documented deletion path first.
        await self.runner.run(
            [str(self.settings.hermes_bin), "profile", "delete", slug, "--yes"], timeout=180, check=False
        )

        # 3. Strip VirtioFS ACLs and remove any CLI-delete remainder.
        if profile_path.exists():
            await self.runner.run(["chmod", "-R", "-N", str(profile_path)], check=False)
            await self.runner.run(["chmod", "-R", "u+rwX", str(profile_path)], check=False)
            await self.runner.run(["rm", "-rf", str(profile_path)], check=False)
        # 4. Remove a wrapper even when the CLI stopped halfway through.
        await self.runner.run(["rm", "-f", str(wrapper_path)], check=False)

        # 5. Restart the gateway to clear in-memory title/session state, then wait.
        # kickstart -k can race: if the dying instance still holds port 8642 when
        # the new one boots, the api_server fails to bind and the gateway runs
        # without it until the NEXT restart (observed live 2026-08-13). One more
        # kickstart after the port is free resolves it, so try up to three times.
        await restart_gateway(self.runner, self.health_check)

        # 6. Sweep a skeleton resurrected by the prior gateway process.
        if profile_path.exists():
            await self.runner.run(["chmod", "-R", "-N", str(profile_path)], check=False)
            await self.runner.run(["chmod", "-R", "u+rwX", str(profile_path)], check=False)
            await self.runner.run(["rm", "-rf", str(profile_path)], check=False)
        if profile_path.exists():
            raise RuntimeError(f"Hermes profile residue remains after purge: {slug}")
        if wrapper_path.exists():
            raise RuntimeError(f"Hermes wrapper residue remains after purge: {slug}")
