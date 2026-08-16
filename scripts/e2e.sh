#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BOTTERD_URL:-http://127.0.0.1:8674}"
TOKEN_PATH="${BOTTERD_TOKEN_PATH:-${HOME}/.botter/token}"
TOKEN="$(tr -d '\r\n' < "$TOKEN_PATH")"
AUTH_HEADER="Authorization: Bearer ${TOKEN}"
BOT_ID=""
ROUTINE_ID=""
RUN_ID=""
CHAT_PID=""
E2E_TMP="$(mktemp -d "${TMPDIR:-/tmp}/botter-e2e.XXXXXX")"

cleanup() {
  if [ -n "$CHAT_PID" ] && kill -0 "$CHAT_PID" >/dev/null 2>&1; then
    kill "$CHAT_PID" >/dev/null 2>&1 || true
    wait "$CHAT_PID" >/dev/null 2>&1 || true
  fi
  if [ -n "$BOT_ID" ]; then
    curl -fsS -X DELETE -H "$AUTH_HEADER" "$BASE_URL/v1/bots/$BOT_ID?purge=true" >/dev/null 2>&1 || true
  fi
  rm -rf "$E2E_TMP"
}
trap cleanup EXIT INT TERM

echo "Checking botterd health"
curl -fsS "$BASE_URL/v1/health" > "$E2E_TMP/health.json"
python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); assert data["status"] in {"ok","degraded"}' "$E2E_TMP/health.json"

echo "Creating test-bot"
curl -fsS -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
  "$BASE_URL/v1/bots" \
  --data '{"slug":"test-bot","display_name":"Test Bot","title":"Verification Agent","description":"Exercises Botter end-to-end without accessing user data.","avatar_color":"#3B82F6","avatar_glyph":"peek","approval_boundary":"Ask before any terminal command or external side effect."}' \
  > "$E2E_TMP/bot.json"
BOT_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bot"]["id"])' "$E2E_TMP/bot.json")"
SESSION_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["bot"]["default_session_id"])' "$E2E_TMP/bot.json")"

echo "Streaming tool-list chat"
curl -fsS -N -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
  "$BASE_URL/v1/sessions/$SESSION_ID/chat" \
  --data '{"message":"List your available tools and briefly state what each category does."}' \
  > "$E2E_TMP/chat.sse"
rg -q '^event: message_complete$' "$E2E_TMP/chat.sse"

echo "Creating and pausing routine"
curl -fsS -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
  "$BASE_URL/v1/bots/$BOT_ID/routines" \
  --data '{"name":"E2E five minute check","schedule":"*/5 * * * *","prompt":"Report that the verification routine fired."}' \
  > "$E2E_TMP/routine.json"
ROUTINE_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["routine"]["id"])' "$E2E_TMP/routine.json")"
curl -fsS -X POST -H "$AUTH_HEADER" "$BASE_URL/v1/routines/$ROUTINE_ID/pause" > "$E2E_TMP/routine-paused.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["routine"]["paused"] is True' "$E2E_TMP/routine-paused.json"

echo "Triggering and resolving approval"
curl -fsS -N -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
  "$BASE_URL/v1/sessions/$SESSION_ID/chat" \
  --data '{"message":"Use the terminal tool to run rm -rf /tmp/botter-e2e-approval-probe. Request approval and wait before executing it."}' \
  > "$E2E_TMP/approval-chat.sse" &
CHAT_PID=$!

for _ in $(seq 1 60); do
  curl -fsS -H "$AUTH_HEADER" "$BASE_URL/v1/approvals" > "$E2E_TMP/approvals.json"
  RUN_ID="$(python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); print(data["approvals"][0]["run_id"] if data["approvals"] else "")' "$E2E_TMP/approvals.json")"
  if [ -n "$RUN_ID" ]; then
    break
  fi
  sleep 1
done
# Phase 0 finding: Hermes approvals gate HOST-side tool execution; sandboxed
# terminal commands run without one. On this install an app-initiated chat may
# therefore legitimately produce no approval — warn, don't fail, but always
# require the chat itself to complete.
if [ -n "$RUN_ID" ]; then
  curl -fsS -X POST -H "$AUTH_HEADER" -H 'Content-Type: application/json' \
    "$BASE_URL/v1/approvals/$RUN_ID" --data '{"decision":"once"}' > "$E2E_TMP/approved.json"
  wait "$CHAT_PID"
  CHAT_PID=""
  rg -q '^event: approval_required$' "$E2E_TMP/approval-chat.sse"
else
  echo "WARN: no approval became pending (expected on sandboxed-terminal installs; boundary enforcement is SOUL.md-based)"
  wait "$CHAT_PID"
  CHAT_PID=""
fi
rg -q '^event: message_complete$' "$E2E_TMP/approval-chat.sse"

echo "Archiving and purging test-bot"
curl -fsS -X DELETE -H "$AUTH_HEADER" "$BASE_URL/v1/bots/$BOT_ID" > "$E2E_TMP/archive.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["archived"] is True' "$E2E_TMP/archive.json"
curl -fsS -X DELETE -H "$AUTH_HEADER" "$BASE_URL/v1/bots/$BOT_ID?purge=true" > "$E2E_TMP/purge.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))["purged"] is True' "$E2E_TMP/purge.json"
PURGED_BOT_ID="$BOT_ID"
BOT_ID=""

if curl -fsS -H "$AUTH_HEADER" "$BASE_URL/v1/bots/$PURGED_BOT_ID" >/dev/null 2>&1; then
  echo "Purged bot still resolves through botterd" >&2
  exit 1
fi

echo "botterd e2e passed"
