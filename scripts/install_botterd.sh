#!/usr/bin/env bash
set -euo pipefail

label="com.treysweeney.botterd"
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
  -e "s|__LOG_PATH__|${log_path}|g" \
  >"${plist_tmp}" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.treysweeney.botterd</string>
  <key>ProgramArguments</key>
  <array>
    <string>/opt/homebrew/bin/uv</string>
    <string>run</string>
    <string>--project</string>
    <string>/Users/treysweeney/projects/botter/backend</string>
    <string>uvicorn</string>
    <string>botterd.main:app</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>8674</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/treysweeney/projects/botter/backend</string>
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
