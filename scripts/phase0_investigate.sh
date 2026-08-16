#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_BIN="${HERMES_BIN:-$HOME/.local/bin/hermes}"
BASE="http://127.0.0.1:8642"
PROFILE="botter-scratch"
FIXTURES="${BOTTER_FIXTURES:-$REPO_ROOT/backend/fixtures}"
ENV_FILE="$HERMES_HOME/.env"
KEY="$(awk -F= '$1=="API_SERVER_KEY" {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE" 2>/dev/null || true)"
PROFILE_KEY=""
PROFILE_CREATED=0
mkdir -p "$FIXTURES"

log() { echo "==> $*"; }
redact_file() {
  local file="$1"
  [ -n "$KEY" ] && sed -i '' "s/${KEY}/[REDACTED_API_SERVER_KEY]/g" "$file" 2>/dev/null || true
  [ -n "$PROFILE_KEY" ] && sed -i '' "s/${PROFILE_KEY}/[REDACTED_PROFILE_API_SERVER_KEY]/g" "$file" 2>/dev/null || true
}
record() {
  local file="$1"; shift
  "$@" >> "$file" 2>&1 || true
  redact_file "$file"
}
api() {
  local method="$1" url="$2" body="${3:-}" out="$4" key="${5:-${PROFILE_KEY:-$KEY}}"
  {
    printf 'REQUEST %s %s\n' "$method" "$url"
    if [ -n "$body" ]; then
      curl -sS -N --connect-timeout 10 --max-time 900 -X "$method" \
        -H "Authorization: Bearer $key" -H 'Content-Type: application/json' \
        -d "$body" -w '\nHTTP_STATUS %{http_code}\n' "$url"
    else
      curl -sS -N --connect-timeout 10 --max-time 900 -X "$method" \
        -H "Authorization: Bearer $key" -w '\nHTTP_STATUS %{http_code}\n' "$url"
    fi
  } >> "$out" 2>&1 || true
  redact_file "$out"
}
json_field() {
  "$HERMES_HOME/hermes-agent/venv/bin/python" - "$1" "$2" <<'PY'
import json, sys

def find(obj, key):
    if isinstance(obj, dict):
        if obj.get(key):
            return obj[key]
        for v in obj.values():
            r = find(v, key)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find(v, key)
            if r:
                return r
    return None

key = sys.argv[2]
result = None
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    r = find(obj, key)
    if r:
        result = r  # keep the most recent match in the transcript
if result:
    print(result)
PY
}
remove_profile_completely() {
  # 1. Stop any docker sandbox container whose mounts reference the profile —
  #    a live container blocks removal and its VirtioFS mounts stamp
  #    "deny delete" ACLs on the shared cache dirs.
  for cid in $(docker ps -q --filter "name=hermes-" 2>/dev/null); do
    if docker inspect "$cid" --format '{{range .Mounts}}{{.Source}} {{end}}' 2>/dev/null | rg -q "profiles/$PROFILE"; then
      echo "Stopping sandbox container $cid for $PROFILE" >> "$FIXTURES/teardown.txt"
      docker stop "$cid" >/dev/null 2>&1 || true
    fi
  done
  # 2. Documented delete path (also clears wrapper alias + cron).
  "$HERMES_BIN" profile delete "$PROFILE" --yes >> "$FIXTURES/teardown.txt" 2>&1 || true
  # 3. Fallback: hermes delete crashes on the ACL-protected sandbox dirs
  #    (upstream shutil.rmtree onexc bug on py3.11) — strip ACLs and remove.
  if [ -d "$HERMES_HOME/profiles/$PROFILE" ]; then
    chmod -R -N "$HERMES_HOME/profiles/$PROFILE" 2>/dev/null || true
    chmod -R u+rwX "$HERMES_HOME/profiles/$PROFILE" 2>/dev/null || true
    rm -rf "$HERMES_HOME/profiles/$PROFILE" 2>>"$FIXTURES/teardown.txt" || true
  fi
  rm -f "$HERMES_HOME/../.local/bin/$PROFILE" 2>/dev/null || true
  # 4. The running gateway resurrects served-profile dirs (its per-profile
  #    cron ticker and log handles recreate paths), and the next boot rescans
  #    whatever exists on disk. Restart the gateway, then sweep any skeleton
  #    it re-created, so the new instance serves only real profiles.
  launchctl kickstart -k "gui/${UID}/ai.hermes.gateway" >/dev/null 2>&1 || true
  for i in $(seq 1 30); do
    curl -fsS --max-time 2 "$BASE/health" >/dev/null 2>&1 && break
    sleep 1
  done
  if [ -d "$HERMES_HOME/profiles/$PROFILE" ]; then
    chmod -R -N "$HERMES_HOME/profiles/$PROFILE" 2>/dev/null || true
    rm -rf "$HERMES_HOME/profiles/$PROFILE" 2>>"$FIXTURES/teardown.txt" || true
  fi
}
cleanup_profile() {
  if [ "$PROFILE_CREATED" -eq 1 ] || [ -d "$HERMES_HOME/profiles/$PROFILE" ]; then
    log "Tearing down $PROFILE"
    remove_profile_completely
    redact_file "$FIXTURES/teardown.txt"
  fi
  if [ -d "$HERMES_HOME/profiles/$PROFILE" ]; then
    echo "RESIDUE profile directory remains: $HERMES_HOME/profiles/$PROFILE" >> "$FIXTURES/teardown.txt"
  else
    echo "No profile directory residue for $PROFILE" >> "$FIXTURES/teardown.txt"
  fi
}
trap cleanup_profile EXIT

log "Cleaning up any previous scratch profile and stale fixtures"
mkdir -p "$FIXTURES"
if [ -d "$HERMES_HOME/profiles/$PROFILE" ]; then
  remove_profile_completely
fi
[ -d "$HERMES_HOME/profiles/$PROFILE" ] && { echo "FATAL: could not remove leftover $PROFILE profile"; exit 1; }
# Observed: the multiplexed gateway keeps deleted profiles' session/title
# state in memory (recreating the slug then hits "Title already in use").
# Restart unconditionally so every investigation run starts from clean state.
launchctl kickstart -k "gui/${UID}/ai.hermes.gateway"
for i in $(seq 1 60); do
  curl -fsS --max-time 2 "$BASE/health" >/dev/null 2>&1 && break
  sleep 1
done
rm -f "$FIXTURES"/*.txt "$FIXTURES"/*.sse
log "Creating cloned scratch profile"
record "$FIXTURES/phase0_setup.txt" "$HERMES_BIN" profile create "$PROFILE" --clone --description "scratch"
PROFILE_CREATED=1
PROFILE_KEY="$(awk -F= '$1=="API_SERVER_KEY" {sub(/^[^=]*=/, ""); print; exit}' "$HERMES_HOME/profiles/$PROFILE/.env" 2>/dev/null || true)"
# Cloned profiles may not carry their own .env; the multiplexed gateway
# resolves profile-scoped keys with a fallback, so use the main key then.
[ -n "$PROFILE_KEY" ] || PROFILE_KEY="$KEY"

log "Investigation (a): probe profile route without gateway restart for ~30s"
: > "$FIXTURES/investigation_a_no_restart.txt"
for i in $(seq 1 30); do
  {
    printf '\nATTEMPT %s %s\n' "$i" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    curl -sS --connect-timeout 2 --max-time 3 -H "Authorization: Bearer $PROFILE_KEY" \
      -w '\nHTTP_STATUS %{http_code}\n' "$BASE/p/$PROFILE/api/sessions"
  } >> "$FIXTURES/investigation_a_no_restart.txt" 2>&1 || true
  redact_file "$FIXTURES/investigation_a_no_restart.txt"
  tail -n 2 "$FIXTURES/investigation_a_no_restart.txt" | rg -q 'HTTP_STATUS 2' && break
  sleep 1
done
if ! rg -q 'HTTP_STATUS 2' "$FIXTURES/investigation_a_no_restart.txt"; then
  log "No-restart probe failed; restarting gateway for the second probe"
  launchctl kickstart -k "gui/${UID}/ai.hermes.gateway" >> "$FIXTURES/investigation_a_no_restart.txt" 2>&1 || true
  for i in $(seq 1 60); do
    {
      printf '\nPOST_RESTART_ATTEMPT %s %s\n' "$i" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      curl -sS --connect-timeout 2 --max-time 3 -H "Authorization: Bearer $PROFILE_KEY" \
        -w '\nHTTP_STATUS %{http_code}\n' "$BASE/p/$PROFILE/api/sessions"
    } >> "$FIXTURES/investigation_a_no_restart.txt" 2>&1 || true
    redact_file "$FIXTURES/investigation_a_no_restart.txt"
    tail -n 2 "$FIXTURES/investigation_a_no_restart.txt" | rg -q 'HTTP_STATUS 2' && break
    sleep 1
  done
else
  echo "RESULT profile visible without restart" >> "$FIXTURES/investigation_a_no_restart.txt"
fi

log "Creating a scratch session"
SESSION_FILE="$FIXTURES/session_create.txt"
# POST /api/sessions defaults model to the literal "hermes-agent" alias, which
# the session chat path forwards verbatim to the provider (HTTP 400). Pass the
# profile's real default model explicitly — botterd must do the same.
MAIN_MODEL="$("$HERMES_HOME/hermes-agent/venv/bin/python" - "$HERMES_HOME/config.yaml" <<'PY'
import sys
from pathlib import Path
from ruamel.yaml import YAML
cfg = YAML(typ="safe").load(Path(sys.argv[1]).read_text()) or {}
m = cfg.get("model") or {}
print(m.get("default") if isinstance(m, dict) else m or "")
PY
)"
api POST "$BASE/p/$PROFILE/api/sessions" '{"title":"Botter Phase 0 scratch '"$(date +%s)"'","model":"'"$MAIN_MODEL"'"}' "$SESSION_FILE"
SESSION_ID="$(json_field "$SESSION_FILE" id || true)"
[ -n "$SESSION_ID" ] || { echo "Could not extract session id; see $SESSION_FILE"; exit 1; }
echo "SESSION_ID $SESSION_ID" >> "$SESSION_FILE"

capture_chat() {
  local prompt="$1" output="$2"
  log "Capturing session chat stream: $output"
  {
    printf 'START_UTC %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    curl -sS -N --connect-timeout 10 --max-time 900 \
      -X POST -H "Authorization: Bearer $PROFILE_KEY" -H 'Content-Type: application/json' \
      -d "$("$HERMES_HOME/hermes-agent/venv/bin/python" - "$prompt" <<'PY'
import json, sys
print(json.dumps({"message": sys.argv[1]}))
PY
)" "$BASE/p/$PROFILE/api/sessions/$SESSION_ID/chat/stream" 2>&1 | \
      "$HERMES_HOME/hermes-agent/venv/bin/python" -u -c 'import sys,datetime
for line in sys.stdin:
    sys.stdout.write(datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ ") + line)
    sys.stdout.flush()'
    printf 'END_UTC %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$output" || true
  redact_file "$output"
}
capture_chat 'Reply with exactly: hi' "$FIXTURES/chat_stream_notool.sse"
TOOL_PROMPT=$'Run \x60echo hello\x60 in your terminal and report the output'
capture_chat "$TOOL_PROMPT" "$FIXTURES/chat_stream_tool.sse"

log "Starting a run through /v1/runs and capturing its event stream"
RUN_START="$FIXTURES/run_start.txt"
api POST "$BASE/p/$PROFILE/v1/runs" '{"input":"Reply with exactly: hi","session_id":"'"$SESSION_ID"'"}' "$RUN_START"
RUN_ID="$(json_field "$RUN_START" run_id || true)"
if [ -n "$RUN_ID" ]; then
  {
    printf 'START_UTC %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    curl -sS -N --connect-timeout 10 --max-time 900 -H "Authorization: Bearer $PROFILE_KEY" \
      "$BASE/p/$PROFILE/v1/runs/$RUN_ID/events" 2>&1 | \
      "$HERMES_HOME/hermes-agent/venv/bin/python" -u -c 'import sys,datetime
for line in sys.stdin:
    sys.stdout.write(datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ ") + line)
    sys.stdout.flush()'
    printf 'END_UTC %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$FIXTURES/run_events.sse" || true
else
  cp "$RUN_START" "$FIXTURES/run_events.sse"
  echo "No run_id returned; event stream not available" >> "$FIXTURES/run_events.sse"
fi
redact_file "$FIXTURES/run_events.sse"

log "Investigation (c): create and manually fire an approval-sensitive cron job"
CRON_FILE="$FIXTURES/investigation_c_cron_approval.txt"
: > "$CRON_FILE"
cron_body='{"name":"botter-phase0-approval","schedule":"0 0 1 1 *","prompt":"Use your terminal tool to run: touch /tmp/botter-approval-probe — if this action requires approval, request it and wait; do not attempt any workaround.","deliver":"local"}'
api POST "$BASE/p/$PROFILE/api/jobs" "$cron_body" "$CRON_FILE"
JOB_ID="$(json_field "$CRON_FILE" id || true)"
[ -n "$JOB_ID" ] || JOB_ID="$(json_field "$CRON_FILE" job_id || true)"
if [ -n "$JOB_ID" ]; then
  api POST "$BASE/p/$PROFILE/api/jobs/$JOB_ID/run" "" "$CRON_FILE"
  # /run only moves next_run_at to now; the cron scheduler fires the job
  # asynchronously — poll up to 3 min for last_run_at/last_status to appear.
  for i in $(seq 1 36); do
    poll="$(curl -sS --connect-timeout 3 --max-time 10 -H "Authorization: Bearer $PROFILE_KEY" "$BASE/p/$PROFILE/api/jobs/$JOB_ID" 2>&1 || true)"
    {
      printf '\nPOLL %s %s\n' "$i" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      printf '%s\n' "$poll" | "$HERMES_HOME/hermes-agent/venv/bin/python" -c 'import json,sys
try:
    j = json.load(sys.stdin).get("job", {})
    print(json.dumps({k: j.get(k) for k in ("state","last_run_at","last_status","last_error","next_run_at")}))
except Exception:
    sys.stdout.write(sys.stdin.read())'
      # Real run ids are run_<32 hex>; the short pattern previously matched
      # the literal "run_a" inside the "last_run_at" JSON key.
      for candidate in $(printf '%s\n' "$poll" | rg -o 'run_[0-9a-f]{16,}' | sort -u; tail -n 80 "$HERMES_HOME/logs/gateway.log" 2>/dev/null | rg -o 'run_[0-9a-f]{16,}' | sort -u); do
        printf 'RUN_STATUS_CANDIDATE %s\n' "$candidate"
        curl -sS --connect-timeout 3 --max-time 10 -H "Authorization: Bearer $PROFILE_KEY" "$BASE/p/$PROFILE/v1/runs/$candidate"
        printf '\n'
      done
      tail -n 50 "$HERMES_HOME/logs/gateway.log" 2>/dev/null | rg -i 'approval' || true
    } >> "$CRON_FILE" 2>&1
    redact_file "$CRON_FILE"
    printf '%s' "$poll" | rg -q '"last_run_at": *"' && { echo "JOB_FIRED after poll $i" >> "$CRON_FILE"; break; }
    sleep 5
  done
  # If the job fired, watch another ~90s for the execution's run to surface
  # and for any approval request it raises.
  for i in $(seq 1 6); do
    sleep 15
    {
      printf '\nPOST_FIRE_SWEEP %s %s\n' "$i" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      curl -sS --connect-timeout 3 --max-time 10 -H "Authorization: Bearer $PROFILE_KEY" "$BASE/p/$PROFILE/api/jobs/$JOB_ID"
      printf '\n'
      for candidate in $(tail -n 200 "$HERMES_HOME/logs/gateway.log" 2>/dev/null | rg -o 'run_[0-9a-f]{16,}' | sort -u); do
        printf 'RUN_STATUS_CANDIDATE %s\n' "$candidate"
        curl -sS --connect-timeout 3 --max-time 10 -H "Authorization: Bearer $PROFILE_KEY" "$BASE/p/$PROFILE/v1/runs/$candidate"
        printf '\n'
      done
      tail -n 100 "$HERMES_HOME/logs/gateway.log" 2>/dev/null | rg -i 'approval|cron.*run|job.*ddbf' || true
    } >> "$CRON_FILE" 2>&1
    redact_file "$CRON_FILE"
  done
  printf '\nFULL_JOB_STATE_FINAL\n%s\n' "$poll" >> "$CRON_FILE"
  api DELETE "$BASE/p/$PROFILE/api/jobs/$JOB_ID" "" "$CRON_FILE"
else
  echo "Could not extract job id; job cleanup requires manual supervisor review" >> "$CRON_FILE"
fi
redact_file "$CRON_FILE"
log "Investigation complete; teardown runs automatically"
