#!/usr/bin/env bash
# Register another machine as a Sentinel log source.
#
#   ./tools/add-log-source.sh <host-ip> <kind>       kind: auth | firewall | dns
#
# Appends the sender to backend/source_map.yaml (or updates it if already
# listed) and prints the exact rsyslog line to paste on that host. Run this on
# serverpi. The engine reads source_map.yaml at startup, so restart it after.
#
# Why the sender's IP and not its hostname: the map is keyed on the peer
# address the listener sees, because the hostname inside a syslog line is
# attacker-influenced text.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAP="$ROOT/backend/source_map.yaml"
PORT=5514

usage() { echo "usage: $0 <host-ip> <auth|firewall|dns>" >&2; exit 2; }
[[ $# -eq 2 ]] || usage
ip="$1" kind="$2"

case "$kind" in auth|firewall|dns) ;; *) echo "kind must be auth, firewall or dns (got '$kind')" >&2; usage ;; esac
if ! [[ "$ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  echo "host-ip must be a dotted IPv4 address (got '$ip')" >&2; usage
fi

# serverpi's own address, for the line the other host needs. Override with
# SENTINEL_HOST when the LAN address the sender should use is not the first one.
me="${SENTINEL_HOST:-$(hostname -I 2>/dev/null | awk '{print $1}' || true)}"
me="${me:-192.168.1.8}"

# --- source_map.yaml ----------------------------------------------------------
[[ -f "$MAP" ]] || { echo "sources:" > "$MAP"; }
grep -q '^sources:' "$MAP" || printf '\nsources:\n' >> "$MAP"
if grep -qE "^\s+${ip//./\\.}:" "$MAP"; then
  sed -i -E "s|^(\s+)${ip//./\\.}:.*$|\1${ip}: ${kind}|" "$MAP"
  echo "updated  $ip -> $kind in ${MAP#$ROOT/}"
else
  printf '  %s: %s\n' "$ip" "$kind" >> "$MAP"
  echo "added    $ip -> $kind to ${MAP#$ROOT/}"
fi

# Prove the file still loads, so a typo here never takes the engine down at boot.
PY="${SENTINEL_PYTHON:-$ROOT/backend/.venv/bin/python}"; [[ -x "$PY" ]] || PY="$(command -v python3 || command -v python)"
( cd "$ROOT/backend" && "$PY" - "$MAP" <<'PY'
import sys
from ingest import load_source_map
m = load_source_map(sys.argv[1])
print(f"source_map ok: {len(m)} sender(s): " + ", ".join(f"{k}={v}" for k, v in m.items()))
PY
)

# --- what to paste on the other host ------------------------------------------
cat <<EOF

On $ip, as root, paste this ONE line into /etc/rsyslog.d/10-sentinel-forward.conf, then: systemctl restart rsyslog

*.* action(type="omfwd" target="$me" port="$PORT" protocol="tcp" template="RSYSLOG_TraditionalForwardFormat" action.resumeRetryCount="-1" queue.type="LinkedList" queue.size="10000")

(The template is not optional: Debian 13 forwards RFC 3339 timestamps by default and the engine parses none of them.
 tools/rsyslog-client.conf is the same thing with comments.)

Then here:  sudo systemctl restart sentinel        # source_map.yaml is read at startup
Test from $ip:  logger -p auth.info 'sentinel forward test'   and watch  journalctl -u sentinel -f  for sender=$ip
EOF

# The unit ships with --tail (serverpi's own auth.log). Forwarded lines only
# arrive on --syslog. Say so, loudly, rather than let someone wonder why
# nothing shows up.
if systemctl show -p ExecStart --value sentinel 2>/dev/null | grep -q -- '--tail'; then
  echo
  echo "NOTE: sentinel.service is running with --tail, which ignores :$PORT. Switch ExecStart to --syslog"
  echo "      (tools/sentinel.service, then ./tools/deploy-serverpi.sh) before expecting events from $ip."
fi
