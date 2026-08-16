#!/usr/bin/env bash
set -euo pipefail

label="io.github.treysweeney3.botterd"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_dir="${repo_root}/backend"

# uv lives in different places depending on how it was installed:
# Homebrew on Apple Silicon (/opt/homebrew), Homebrew on Intel (/usr/local),
# or the standalone installer (~/.local/bin). launchd runs with a minimal PATH
# and cannot resolve it, so the plist needs the absolute path resolved here.
uv_bin="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "${uv_bin}" ]]; then
  for candidate in /opt/homebrew/bin/uv /usr/local/bin/uv "${HOME}/.local/bin/uv"; do
    if [[ -x "${candidate}" ]]; then
      uv_bin="${candidate}"
      break
    fi
  done
fi
if [[ -z "${uv_bin}" || ! -x "${uv_bin}" ]]; then
  printf 'botterd install aborted: could not find the "uv" executable. Install uv (https://docs.astral.sh/uv/) or set UV_BIN=/path/to/uv.\n' >&2
  exit 1
fi
if [[ ! -f "${backend_dir}/pyproject.toml" ]]; then
  printf 'botterd install aborted: no backend found at %s. Run this script from a full checkout of the repository.\n' "${backend_dir}" >&2
  exit 1
fi

user_id="$(id -u)"
domain="gui/${user_id}"
service="${domain}/${label}"
launch_agents_dir="${HOME}/Library/LaunchAgents"
plist_path="${launch_agents_dir}/${label}.plist"
state_dir="${HOME}/.botter"
log_path="${state_dir}/botterd.log"
health_url="http://127.0.0.1:8674/v1/health"

port_is_listening() {
  /usr/sbin/lsof -nP -iTCP:8674 -sTCP:LISTEN >/dev/null 2>&1
}

managed_loaded=false
if launchctl print "${service}" >/dev/null 2>&1; then
  managed_loaded=true
fi

if port_is_listening && [[ "${managed_loaded}" != "true" ]]; then
  owner="$(/usr/sbin/lsof -nP -iTCP:8674 -sTCP:LISTEN 2>/dev/null | awk 'NR == 2 { print $1 " (pid " $2 ")" }')"
  printf 'botterd install aborted: port 8674 is already held by %s. Stop the development server, then rerun this script.\n' "${owner:-another process}" >&2
  exit 1
fi

if [[ "${managed_loaded}" == "true" ]]; then
  launchctl bootout "${service}"
  for _ in {1..50}; do
    if ! port_is_listening; then
      break
    fi
    sleep 0.2
  done
  if port_is_listening; then
    printf 'botterd install aborted: port 8674 remained occupied after the existing launch agent was stopped.\n' >&2
    exit 1
  fi
fi

mkdir -p "${launch_agents_dir}" "${state_dir}"
touch "${log_path}"
chmod 700 "${state_dir}"

plist_tmp="$(mktemp "${TMPDIR:-/tmp}/botterd-plist.XXXXXX")"
cleanup() {
  rm -f "${plist_tmp}"
}
trap cleanup EXIT

sed \
  -e "s|__LABEL__|${label}|g" \
  -e "s|__UV_BIN__|${uv_bin}|g" \
  -e "s|__PROJECT_DIR__|${backend_dir}|g" \
  -e "s|__LOG_PATH__|${log_path}|g" \
  >"${plist_tmp}" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>__LABEL__</string>
  <key>ProgramArguments</key>
  <array>
    <string>__UV_BIN__</string>
    <string>run</string>
    <string>--project</string>
    <string>__PROJECT_DIR__</string>
    <string>uvicorn</string>
    <string>botterd.main:app</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8674</string>
  </array>
  <key>WorkingDirectory</key>
  <string>__PROJECT_DIR__</string>
  <key>KeepAlive</key>
  <true/>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>__LOG_PATH__</string>
  <key>StandardErrorPath</key>
  <string>__LOG_PATH__</string>
</dict>
</plist>
PLIST

/usr/bin/plutil -lint "${plist_tmp}" >/dev/null
/usr/bin/install -m 0644 "${plist_tmp}" "${plist_path}"
launchctl bootstrap "${domain}" "${plist_path}"
launchctl kickstart -k "${service}"

for _ in {1..60}; do
  if /usr/bin/curl --fail --silent --max-time 1 "${health_url}" >/dev/null; then
    printf 'botterd is installed and healthy at %s\n' "${health_url}"
    exit 0
  fi
  sleep 0.5
done

printf 'botterd launch agent started but did not become healthy at %s. Check %s\n' "${health_url}" "${log_path}" >&2
exit 1
