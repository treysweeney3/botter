#!/usr/bin/env bash
set -euo pipefail

label="io.github.treysweeney3.botterd"
user_id="$(id -u)"
service="gui/${user_id}/${label}"
plist_path="${HOME}/Library/LaunchAgents/${label}.plist"

if launchctl print "${service}" >/dev/null 2>&1; then
  launchctl bootout "${service}"
fi

rm -f "${plist_path}"
printf 'botterd launch agent removed. Data and logs under %s were left intact.\n' "${HOME}/.botter"
