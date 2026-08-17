#!/usr/bin/env bash
set -euo pipefail

HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
BASE="http://127.0.0.1:8642"
ENV_FILE="$HERMES_HOME/.env"
CONFIG="$HERMES_HOME/config.yaml"
PROXY_CONFIG="$HERMES_HOME/proxy/proxy.yaml"
ERROR_LOG="$HERMES_HOME/logs/gateway.error.log"
GATEWAY_LABEL="ai.hermes.gateway"
FAILURES=0

pass() { echo "PASS $*"; }
fail() { echo "FAIL $*"; FAILURES=$((FAILURES + 1)); }

api_key() { awk -F= '$1=="API_SERVER_KEY" {sub(/^[^=]*=/, ""); print; exit}' "$1"; }
MAIN_KEY="$(api_key "$ENV_FILE" 2>/dev/null || true)"
[ -n "$MAIN_KEY" ] && pass "API_SERVER_KEY present in ~/.hermes/.env" || fail "API_SERVER_KEY present in ~/.hermes/.env"

if launchctl print "gui/${UID}/${GATEWAY_LABEL}" >/dev/null 2>&1; then
  pass "gateway launchd job loaded"
else
  fail "gateway launchd job loaded"
fi

if curl -fsS --max-time 5 "$BASE/health" >/dev/null 2>&1; then
  pass "gateway /health OK"
else
  fail "gateway /health OK"
fi

for endpoint in /v1/models /v1/capabilities; do
  if [ -n "$MAIN_KEY" ] && body="$(curl -fsS --max-time 10 -H "Authorization: Bearer $MAIN_KEY" "$BASE$endpoint" 2>/dev/null)" && [ -n "$body" ] && [ "$body" != "{}" ] && [ "$body" != '[]' ]; then
    pass "authenticated GET $endpoint returns data"
  else
    fail "authenticated GET $endpoint returns data"
  fi
done

listeners="$(lsof -nP -a -iTCP:8642 -sTCP:LISTEN 2>/dev/null || true)"
if printf '%s\n' "$listeners" | rg -q '127\.0\.0\.1:8642|localhost:8642' && ! printf '%s\n' "$listeners" | rg -q '(^|[[:space:]])(0\.0\.0\.0|\*:|\[::\]):8642'; then
  pass "port 8642 bound to loopback only"
else
  fail "port 8642 bound to loopback only"
fi

profiles_dir="$HERMES_HOME/profiles"
profile_count=0
if [ -d "$profiles_dir" ]; then
  while IFS= read -r profile_dir; do
    [ -d "$profile_dir" ] || continue
    profile_count=$((profile_count + 1))
    profile="${profile_dir##*/}"
    key="$(api_key "$profile_dir/.env" 2>/dev/null || true)"
    [ -n "$key" ] || key="$MAIN_KEY"
    if [ -n "$key" ] && curl -fsS --max-time 10 -H "Authorization: Bearer $key" "$BASE/p/$profile/api/sessions" >/dev/null 2>&1; then
      pass "profile route /p/$profile/api/sessions"
    else
      fail "profile route /p/$profile/api/sessions"
    fi
  done < <(find "$profiles_dir" -mindepth 1 -maxdepth 1 -type d -print | sort)
fi
[ "$profile_count" -gt 0 ] && echo "INFO checked $profile_count profile route(s)" || echo "PASS no named profile directories to check"

# Two benign traceback classes are expected around deliberate restarts and
# profile purges: (1) BrokenPipe/APIConnectionError from aborted in-flight LLM
# streams; (2) "--- Logging error ---" FileNotFoundError when the old gateway
# instance tries to roll over a deleted profile's log file. Only flag others.
if [ -f "$ERROR_LOG" ] && tail -n 100 "$ERROR_LOG" | rg -qi 'traceback \(most recent call last\)' && \
   ! tail -n 100 "$ERROR_LOG" | rg -qi 'Broken pipe|APIConnectionError|httpx\.ReadError|--- Logging error ---'; then
  fail "last 100 gateway.error.log lines free of unexpected tracebacks"
else
  pass "last 100 gateway.error.log lines free of unexpected tracebacks"
fi

expected_hosts="$("$HERMES_HOME/hermes-agent/venv/bin/python" - "$CONFIG" <<'PY'
import json, sys
from pathlib import Path
from ruamel.yaml import YAML
cfg = YAML(typ="safe").load(Path(sys.argv[1]).read_text()) or {}
v = (cfg.get("proxy") or {}).get("extra_allowed_hosts", [])
if isinstance(v, str):
    v = json.loads(v)
for h in v or []:
    print(h)
PY
)"
bad_domains="$("$HERMES_HOME/hermes-agent/venv/bin/python" - "$PROXY_CONFIG" <<'PY'
import sys
from pathlib import Path
from ruamel.yaml import YAML
cfg = YAML(typ="safe").load(Path(sys.argv[1]).read_text()) or {}
for t in cfg.get("transforms") or []:
    if t.get("name") == "allowlist":
        print(sum(1 for d in (t.get("config") or {}).get("domains", []) if isinstance(d, str) and len(d) == 1))
        break
else:
    print(999999)
PY
)"
[ "$bad_domains" = "0" ] && pass "proxy allowlist has no single-character entries" || fail "proxy allowlist has no single-character entries"
# Compare against the parsed YAML list (entries may be quoted or unquoted).
actual_domains="$("$HERMES_HOME/hermes-agent/venv/bin/python" - "$PROXY_CONFIG" <<'PY'
import sys
from pathlib import Path
from ruamel.yaml import YAML
cfg = YAML(typ="safe").load(Path(sys.argv[1]).read_text()) or {}
for t in cfg.get("transforms") or []:
    if t.get("name") == "allowlist":
        for d in (t.get("config") or {}).get("domains", []):
            print(d)
PY
)"
while IFS= read -r host; do
  [ -n "$host" ] || continue
  if printf '%s\n' "$actual_domains" | rg -qxF -- "$host"; then
    pass "proxy allowlist contains $host"
  else
    fail "proxy allowlist contains $host"
  fi
done <<< "$expected_hosts"

# Creating a bot links the new profile to main's iron-proxy and refuses to
# continue when that daemon is not listening, so a stopped proxy shows up as a
# bot that cannot be created. Catch it here instead.
HERMES_CLI="${HERMES_BIN:-$HOME/.local/bin/hermes}"
[ -x "$HERMES_CLI" ] || HERMES_CLI="$(command -v hermes || true)"
egress_status="$("$HERMES_CLI" egress status 2>&1 || true)"  # the status table prints to stderr
if printf '%s\n' "$egress_status" | rg -qi 'Listening\s+yes'; then
  pass "iron-proxy egress is listening"
else
  fail "iron-proxy egress is listening (run: hermes egress setup, or hermes egress start)"
fi
missing_proxy_state=0
for artifact in proxy.yaml ca.crt mappings.json iron-proxy.pid; do
  [ -f "$HERMES_HOME/proxy/$artifact" ] || missing_proxy_state=1
done
if [ "$missing_proxy_state" = "0" ] && rg -q '"proxy_token"' "$HERMES_HOME/proxy/mappings.json" 2>/dev/null; then
  pass "iron-proxy state is complete and has provider token mappings"
else
  fail "iron-proxy state is complete and has provider token mappings"
fi

SLACK_LOG="$HERMES_HOME/logs/gateway.log"
if [ -f "$SLACK_LOG" ] && tail -n 200 "$SLACK_LOG" | rg -qi 'slack.*(connected|socket|started)|socket.*slack|connected.*slack'; then
  pass "Slack platform on main has recent connected/socket evidence"
else
  fail "Slack platform on main has recent connected/socket evidence"
fi

[ "$FAILURES" -eq 0 ] || exit 1
echo "All Hermes verification checks passed"
