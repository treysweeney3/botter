"""Pending approval persistence and Hermes decision forwarding."""

from __future__ import annotations

from datetime import datetime, timezone

from .db import Database
from .errors import APIError
from .events import EventBus
from .hermes import HermesClient, HermesError
from .models import Approval, ApprovalDecision, ApprovalResponse, Bot


class ApprovalService:
    def __init__(self, db: Database, hermes: HermesClient, events: EventBus):
        self.db = db
        self.hermes = hermes
        self.events = events

    async def list(self) -> list[Approval]:
        return [Approval.model_validate(row) for row in await self.db.list_pending_approvals()]

    async def decide(self, run_id: str, request: ApprovalDecision) -> ApprovalResponse:
        row = await self.db.get_approval(run_id)
        if row is None or row.get("resolved_at"):
            raise APIError(404, "approval_not_found", f"Pending approval not found: {run_id}")
        bot_row = await self.db.get_bot(str(row["bot_id"]))
        if bot_row is None:
            raise APIError(404, "bot_not_found", f"Bot not found: {row['bot_id']}")
        bot = Bot.model_validate(bot_row)
        try:
            await self.hermes.approve(bot.slug, run_id, request.decision)
        except HermesError as exc:
            raise APIError(exc.status_code if exc.status_code in {404, 409} else 502, exc.code, exc.message) from exc
        resolved_at = datetime.now(timezone.utc).isoformat()
        resolved = await self.db.resolve_approval(run_id, request.decision, resolved_at)
        await self.events.publish(
            "approval_resolved", {"run_id": run_id, "decision": request.decision}
        )
        return ApprovalResponse(run_id=run_id, decision=request.decision, resolved=resolved)

