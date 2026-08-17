#!/usr/bin/env bash
set -euo pipefail

# Botter Phase 0 setup. This script is intentionally explicit about every
# Hermes path and never edits the Hermes source checkout.
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
HERMES_PYTHON="${HERMES_PYTHON:-$HERMES_HOME/hermes-agent/venv/bin/python}"
CONFIG="$HERMES_HOME/config.yaml"
PROXY_CONFIG="$HERMES_HOME/proxy/proxy.yaml"
ENV_FILE="$HERMES_HOME/.env"
TODAY="$(date +%Y%m%d)"

die() { echo "ERROR: $*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"; }
need openssl
need launchctl
need curl
need grep

# Hermes is absent from $HERMES_HOME entirely, or points at the wrong path.
hermes_missing() {
  cat >&2 <<EOF
ERROR: $1

Botter manages an existing Hermes agent; it does not install one. Expected a
Hermes install at:

  HERMES_HOME=$HERMES_HOME

If Hermes is installed somewhere else, re-run with an explicit path:

  HERMES_HOME=/path/to/.hermes scripts/setup_hermes.sh

If you do not have Hermes yet, install it first and complete its own setup
(model + provider API key), then re-run this script. See docs/SETUP.md.
EOF
  exit 1
}

# Hermes is installed, but one of its own setup steps has not been run yet.
# $1 = what is missing, $2 = the Hermes command that creates it.
hermes_incomplete() {
  cat >&2 <<EOF
ERROR: $1

Hermes is installed at $HERMES_HOME, but this part of its setup has not run
yet. Botter does not create Hermes configuration on your behalf. Run:

  $2

then re-run this script. See docs/SETUP.md.
EOF
  exit 1
}

[ -d "$HERMES_HOME" ] || hermes_missing "no Hermes install found at $HERMES_HOME"
[ -x "$HERMES_PYTHON" ] || hermes_missing "Hermes venv Python not found: $HERMES_PYTHON"
[ -f "$CONFIG" ] || hermes_incomplete "missing Hermes config: $CONFIG" "hermes setup"
[ -f "$PROXY_CONFIG" ] || hermes_incomplete \
  "missing iron-proxy config: $PROXY_CONFIG" \
  "hermes egress setup"

backup_once() {
  local src="$1" dst="${1}.bak.botter-${TODAY}"
  if [ -e "$dst" ]; then
    echo "BACKUP SKIP $dst (already exists)"
  else
    cp -p "$src" "$dst"
    echo "BACKUP $src -> $dst"
  fi
}
backup_once "$CONFIG"
backup_once "$PROXY_CONFIG"

mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"
if grep -q '^API_SERVER_KEY=' "$ENV_FILE"; then
  echo "ENV API_SERVER_KEY already present"
else
  printf 'API_SERVER_KEY=%s\n' "$(openssl rand -hex 32)" >> "$ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  echo "ENV API_SERVER_KEY added"
fi

"$HERMES_PYTHON" - "$CONFIG" "$PROXY_CONFIG" <<'PY'
import json
import io
import sys
from pathlib import Path
from ruamel.yaml import YAML

config_path = Path(sys.argv[1])
proxy_path = Path(sys.argv[2])
yaml = YAML()
yaml.preserve_quotes = True
yaml.width = 4096

config_text = config_path.read_text()
config = yaml.load(config_text) or {}
platforms = config.setdefault("platforms", {})
api = platforms.setdefault("api_server", {})
api["enabled"] = True
extra = api.setdefault("extra", {})
extra["host"] = "127.0.0.1"
extra["port"] = 8642
gateway = config.setdefault("gateway", {})
gateway["multiplex_profiles"] = True
rendered = io.StringIO()
yaml.dump(config, rendered)
if rendered.getvalue() != config_text:
    config_path.write_text(rendered.getvalue())
    print("CONFIG config.yaml updated")
else:
    print("CONFIG config.yaml already current")

hosts_raw = (config.get("proxy") or {}).get("extra_allowed_hosts", [])
if isinstance(hosts_raw, str):
    try:
        hosts = json.loads(hosts_raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"proxy.extra_allowed_hosts is not valid JSON: {exc}")
else:
    hosts = hosts_raw
if not isinstance(hosts, list) or not all(isinstance(x, str) for x in hosts):
    raise SystemExit("proxy.extra_allowed_hosts must be a JSON/list of strings")

proxy_text = proxy_path.read_text()
proxy = yaml.load(proxy_text) or {}
transforms = proxy.get("transforms") or []
allow = next((x for x in transforms if x.get("name") == "allowlist"), None)
if allow is None:
    raise SystemExit("proxy.yaml has no transforms entry named allowlist")
allow_cfg = allow.setdefault("config", {})
domains = allow_cfg.get("domains") or []
if not isinstance(domains, list):
    raise SystemExit("proxy.yaml allowlist domains is not a list")
# The pre-existing serializer bug emits the JSON string one character per
# list item. Retain legitimate defaults, discard those fragments, and add the
# actual configured host strings exactly once.
clean = [d for d in domains if not (isinstance(d, str) and len(d) == 1)]
for host in hosts:
    if host not in clean:
        clean.append(host)
allow_cfg["domains"] = clean
rendered_proxy = io.StringIO()
yaml.dump(proxy, rendered_proxy)
if rendered_proxy.getvalue() != proxy_text:
    proxy_path.write_text(rendered_proxy.getvalue())
    print("PROXY proxy.yaml updated")
else:
    print("PROXY proxy.yaml already current")
print("CONFIG platforms.api_server enabled with extra host/port; gateway multiplex_profiles enabled")
print(f"PROXY allowlist repaired with {len(hosts)} configured extra hosts")
PY

echo "Detecting iron-proxy supervision (read-only)"
ps -axo pid=,command= 2>/dev/null | grep '[i]ron-proxy' || echo "No iron-proxy process found"

# Hermes owns iron-proxy as a managed subprocess (verified: `hermes egress
# status` shows a PID, and no hermes/iron launchd label exists). Restart it
# only if egress is enabled and running, so the edited proxy.yaml is applied.
HERMES_CLI="${HERMES_BIN:-$HOME/.local/bin/hermes}"
[ -x "$HERMES_CLI" ] || HERMES_CLI="$(command -v hermes)" || die "hermes CLI not found"
if "$HERMES_CLI" egress status 2>&1 | grep -qi 'Enabled.*yes'; then  # status table prints to stderr
  "$HERMES_CLI" egress restart
  echo "iron-proxy restarted via hermes egress restart"
else
  echo "iron-proxy egress not enabled; skipping restart"
fi

launchctl kickstart -k "gui/${UID}/ai.hermes.gateway"
deadline=$((SECONDS + 60))
until curl -fsS --max-time 3 http://127.0.0.1:8642/health >/tmp/botter-hermes-health.$$.json; do
  [ "$SECONDS" -ge "$deadline" ] && { rm -f /tmp/botter-hermes-health.$$.json; die "gateway health did not become ready within 60s"; }
  sleep 1
done
cat /tmp/botter-hermes-health.$$.json
rm -f /tmp/botter-hermes-health.$$.json
echo "Gateway restarted and health check passed"
