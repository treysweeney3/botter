from __future__ import annotations

import json

import httpx
import pytest

from botterd.models import NormalizedMessage
from mockserver.main import create_app


AUTH = {"Authorization": "Bearer contract-token"}


@pytest.mark.asyncio
async def test_mockserver_contract_smoke_hits_every_spec_route():
    app = create_app(token="contract-token")

    async def finite_events(*, heartbeat_seconds=15.0):
        yield 'event: feed_updated\ndata: {"bot_id":null}\n\n'

    app.state.mock.events.stream = finite_events
    async with app.router.lifespan_context(app):
        # Lifespan reinstalls the same state object; make the firehose finite for ASGI transport.
        app.state.mock.events.stream = finite_events
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://mock"
        ) as client:
            health = await client.get("/v1/health")
            assert health.status_code == 200
            assert health.json()["status"] == "ok"

            roster = await client.get("/v1/bots", headers=AUTH)
            assert roster.status_code == 200
            assert len(roster.json()["bots"]) == 6
            assert all(not item["avatar_glyph"].startswith(":") for item in roster.json()["bots"])

            bot = await client.get("/v1/bots/bot-1", headers=AUTH)
            assert bot.status_code == 200
            assert "memory_summary" in bot.json()["bot"]
            patched = await client.patch(
                "/v1/bots/bot-1", headers=AUTH, json={"display_name": "Outbound Desk"}
            )
            assert patched.json()["bot"]["display_name"] == "Outbound Desk"

            sessions = await client.get("/v1/bots/bot-1/sessions", headers=AUTH)
            assert sessions.status_code == 200
            created_session = await client.post(
                "/v1/bots/bot-1/sessions", headers=AUTH, json={"title": "Prospecting"}
            )
            assert created_session.status_code == 201
            new_session_id = created_session.json()["session"]["id"]

            history = await client.get("/v1/sessions/session-1/messages?limit=20", headers=AUTH)
            assert history.status_code == 200
            for message in history.json()["messages"]:
                NormalizedMessage.model_validate(message)

            streamed = await client.post(
                "/v1/sessions/session-1/chat", headers=AUTH, json={"message": "Give me an update"}
            )
            assert streamed.status_code == 200
            assert streamed.headers["content-type"].startswith("text/event-stream")
            assert streamed.text.count("event: delta") == 3
            assert "event: message_complete" in streamed.text

            task_stream = await client.post(
                "/v1/sessions/session-1/chat", headers=AUTH, json={"message": "Show a task report"}
            )
            assert "event: tool_event" in task_stream.text
            assert '"kind":"task_report"' in task_stream.text

            approval_stream = await client.post(
                "/v1/sessions/session-1/chat", headers=AUTH, json={"message": "Send this with approval"}
            )
            assert "event: approval_required" in approval_stream.text
            approval_frame = next(
                line for line in approval_stream.text.splitlines() if line.startswith("data:")
            )
            run_id = json.loads(approval_frame.removeprefix("data: "))["run_id"]

            read = await client.post("/v1/sessions/session-1/read", headers=AUTH, json={})
            assert read.status_code == 200
            stopped = await client.post("/v1/sessions/session-1/stop", headers=AUTH)
            assert stopped.json()["stopped"] is True

            routine_list = await client.get("/v1/bots/bot-1/routines", headers=AUTH)
            assert routine_list.status_code == 200
            created_routine = await client.post(
                "/v1/bots/bot-1/routines",
                headers=AUTH,
                json={"name": "Daily review", "schedule": "0 9 * * *", "prompt": "Review pipeline"},
            )
            assert created_routine.status_code == 201
            routine_id = created_routine.json()["routine"]["id"]
            assert (
                await client.patch(
                    f"/v1/routines/{routine_id}", headers=AUTH, json={"name": "Morning review"}
                )
            ).status_code == 200
            assert (await client.post(f"/v1/routines/{routine_id}/run", headers=AUTH)).status_code == 202
            assert (await client.post(f"/v1/routines/{routine_id}/pause", headers=AUTH)).status_code == 200
            assert (await client.post(f"/v1/routines/{routine_id}/resume", headers=AUTH)).status_code == 200
            assert (
                await client.get(f"/v1/routines/{routine_id}/executions?limit=10", headers=AUTH)
            ).status_code == 200
            assert (await client.delete(f"/v1/routines/{routine_id}", headers=AUTH)).status_code == 200

            approvals = await client.get("/v1/approvals", headers=AUTH)
            assert any(item["run_id"] == run_id for item in approvals.json()["approvals"])
            decision = await client.post(
                f"/v1/approvals/{run_id}", headers=AUTH, json={"decision": "once"}
            )
            assert decision.json() == {"run_id": run_id, "decision": "once", "resolved": True}

            memory = await client.get("/v1/bots/bot-1/memory", headers=AUTH)
            assert memory.status_code == 200
            google = await client.post("/v1/auth/google", headers=AUTH)
            assert google.status_code == 200
            assert google.json()["authorization"]["code_entry"] is True
            google_done = await client.post(
                "/v1/auth/google",
                headers=AUTH,
                json={"code": "https://localhost:1/?code=mock&state=mock"},
            )
            assert google_done.json()["integration"]["status"] == "connected"
            google_off = await client.delete("/v1/auth/google", headers=AUTH)
            assert google_off.json()["integration"]["status"] == "not_connected"
            integrations = await client.get("/v1/integrations", headers=AUTH)
            assert integrations.status_code == 200
            # 6 generic keys + 8 curated + Slack + Google, all on one surface.
            rows = integrations.json()["integrations"]
            assert len(rows) == 16
            assert {item["status"] for item in rows} == {"connected", "not_connected"}
            groups = {item["group"] for item in rows if item["group"]}
            assert {"github", "vercel", "supabase", "slack", "google"} <= groups
            integration_set = await client.put(
                "/v1/integrations/NOTION_API_KEY", headers=AUTH, json={"value": "mock-key"}
            )
            assert integration_set.json()["integration"]["is_set"] is True
            integration_removed = await client.delete("/v1/integrations/NOTION_API_KEY", headers=AUTH)
            assert integration_removed.json()["integration"]["is_set"] is False
            custom_config = await client.put(
                "/v1/integrations/MY_FEATURE_FLAG", headers=AUTH, json={"value": "true"}
            )
            assert custom_config.json()["integration"]["kind"] == "config"
            custom_config_removed = await client.delete(
                "/v1/integrations/MY_FEATURE_FLAG", headers=AUTH
            )
            assert custom_config_removed.json()["integration"]["kind"] == "config"
            mcp = await client.get("/v1/mcp", headers=AUTH)
            assert mcp.status_code == 200
            assert mcp.json()["servers"] == []
            assert [p["name"] for p in mcp.json()["presets"]] == ["composio"]
            mcp_added = await client.put(
                "/v1/mcp/composio",
                headers=AUTH,
                json={"url": "https://connect.composio.dev/mcp", "auth": "oauth"},
            )
            assert mcp_added.status_code == 200
            assert mcp_added.json()["server"]["preset"] == "composio"
            assert mcp_added.json()["restarted"] is True
            # A preset drops off the offer list once it is installed.
            assert (await client.get("/v1/mcp", headers=AUTH)).json()["presets"] == []
            assert (
                await client.put("/v1/mcp/bad", headers=AUTH, json={"url": "http://x/mcp"})
            ).status_code == 422
            started = await client.post("/v1/mcp/composio/authorize", headers=AUTH)
            assert started.status_code == 200
            flow = started.json()["authorization"]
            assert flow["status"] == "authorization_required"
            assert flow["url"].startswith("https://")
            # Waiting on the browser, then on botterd's own fan-out and restart.
            polled = await client.get(f"/v1/mcp/authorizations/{flow['flow_id']}", headers=AUTH)
            assert polled.json()["authorization"]["status"] == "authorization_required"
            polled = await client.get(f"/v1/mcp/authorizations/{flow['flow_id']}", headers=AUTH)
            assert polled.json()["authorization"]["status"] == "finishing"
            settled = await client.get(f"/v1/mcp/authorizations/{flow['flow_id']}", headers=AUTH)
            assert settled.json()["authorization"]["status"] == "approved"
            assert settled.json()["server_state"]["authorized"] is True
            assert (
                await client.post("/v1/mcp/nope/authorize", headers=AUTH)
            ).status_code == 404
            assert (
                await client.get("/v1/mcp/authorizations/missing", headers=AUTH)
            ).status_code == 404
            mcp_removed = await client.delete("/v1/mcp/composio", headers=AUTH)
            assert mcp_removed.json()["server"]["status"] == "not_connected"
            assert (await client.delete("/v1/mcp/composio", headers=AUTH)).status_code == 404

            connected = await client.put(
                "/v1/integrations/VERCEL_TOKEN", headers=AUTH, json={"value": "mock-secret"}
            )
            assert connected.json()["integration"]["status"] == "connected"
            assert connected.json()["integration"]["group"] == "vercel"
            disconnected = await client.delete("/v1/integrations/VERCEL_TOKEN", headers=AUTH)
            assert disconnected.json()["integration"]["status"] == "not_connected"
            slack_put = await client.put(
                "/v1/integrations/SLACK", headers=AUTH, json={"value": "nope"}
            )
            slack_delete = await client.delete("/v1/integrations/SLACK", headers=AUTH)
            assert slack_put.status_code == slack_delete.status_code == 403
            assert slack_put.json()["error"]["code"] == "integration_not_managed"
            search = await client.get("/v1/search", headers=AUTH, params={"q": "context", "bot_id": "bot-1"})
            assert search.status_code == 200
            events = await client.get("/v1/events", headers=AUTH)
            assert events.status_code == 200
            assert "event: feed_updated" in events.text

            created_bot = await client.post(
                "/v1/bots",
                headers=AUTH,
                json={
                    "slug": "ops-helper",
                    "display_name": "Ops Helper",
                    "title": "Operations",
                    "description": "Keeps operations organized.",
                    "avatar_color": "#EAB308",
                    "avatar_glyph": "gearshape",
                    "approval_boundary": "Ask before external changes.",
                },
            )
            assert created_bot.status_code == 201
            created_bot_id = created_bot.json()["bot"]["id"]
            archive = await client.delete(f"/v1/bots/{created_bot_id}", headers=AUTH)
            assert archive.json()["archived"] is True
            purge = await client.delete(f"/v1/bots/{created_bot_id}?purge=true", headers=AUTH)
            assert purge.json()["purged"] is True
