#!/usr/bin/env bash
# Install the Sentinel engine on serverpi.
#
# Run this ON serverpi, from the repo root, after cloning:
#
#   git clone <remote> ~/sentinel && cd ~/sentinel
#   ./tools/deploy-serverpi.sh
#
# Idempotent: safe to re-run after a pull. It will not overwrite an existing
# .env or certificate, because doing so would silently invalidate the pin on a
# phone that is already paired.
#
# What it does NOT do:
#   - turn on --live execution. Dry run through the demo, invariant 10.
#   - fill protected_assets.yaml. That list is specific to your network and
#     --live refuses to start while it is empty, which is intentional.
#   - set ANTHROPIC_API_KEY for you. It prompts if missing.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
ENV_FILE="$BACKEND/.env"
SERVICE_NAME="sentinel"
RUN_USER="${SUDO_USER:-$USER}"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[33mwarning: %s\033[0m\n' "$1" >&2; }

# --- sanity ----------------------------------------------------------------
if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This script targets serverpi. Run it there, not on the dev machine." >&2
  exit 1
fi

command -v python3 >/dev/null || { echo "python3 not found" >&2; exit 1; }
command -v openssl >/dev/null || { echo "openssl not found" >&2; exit 1; }

say "Python environment"
if [[ ! -d "$BACKEND/.venv" ]]; then
  python3 -m venv "$BACKEND/.venv"
fi
"$BACKEND/.venv/bin/python" -m pip install --quiet --upgrade pip
"$BACKEND/.venv/bin/python" -m pip install --quiet -r "$BACKEND/requirements.txt"
echo "installed $("$BACKEND/.venv/bin/python" --version)"

say "Tests"
# The handoff is explicit: green before handing work back. If the engine is
# broken, finding out here beats finding out at the 09:00 checkpoint.
( cd "$BACKEND" && "$BACKEND/.venv/bin/python" -m pytest -q ) || {
  warn "tests failed. Deploying anyway would be deploying something known broken."
  exit 1
}

say "Environment file"
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

ensure_var() {
  local key="$1" prompt="$2" default="${3:-}"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    echo "$key already set, leaving it"
    return
  fi
  local value=""
  if [[ -n "$default" ]]; then
    read -rp "$prompt [$default]: " value || true
    value="${value:-$default}"
  else
    read -rsp "$prompt: " value || true
    echo
  fi
  if [[ -z "$value" ]]; then
    warn "$key left unset"
    return
  fi
  echo "${key}=${value}" >> "$ENV_FILE"
}

# Without this, escalation degrades to "unreachable" and everything else still
# works — fail closed, invariant 8. So it is a warning, not a hard stop.
ensure_var ANTHROPIC_API_KEY "ANTHROPIC_API_KEY (input hidden)"

# Ollama runs on the PC, reached over the LAN. serverpi and the PC are on the
# same network, so this is a LAN address, not a Tailscale one.
ensure_var OLLAMA_BASE_URL "OLLAMA_BASE_URL (PC LAN address)" "http://192.168.1.7:11434"

say "TLS certificate"
if [[ -f "$BACKEND/certs/cert.pem" ]]; then
  echo "certificate exists, keeping it (regenerating would break paired phones)"
else
  "$ROOT/tools/gen-cert.sh"
fi

say "Pairing token"
"$ROOT/tools/centron-pair.sh" >/dev/null   # creates the token if absent
echo "token present in $ENV_FILE"

say "systemd unit"
UNIT_SRC="$ROOT/tools/sentinel.service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"

sed -e "s|__ROOT__|${ROOT}|g" -e "s|__USER__|${RUN_USER}|g" "$UNIT_SRC" \
  | sudo tee "$UNIT_DST" >/dev/null

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

sleep 3
if systemctl is-active --quiet "$SERVICE_NAME"; then
  echo "sentinel is running"
else
  warn "sentinel did not start. Logs:"
  sudo journalctl -u "$SERVICE_NAME" -n 40 --no-pager
  exit 1
fi

say "Sleep settings"
# contracts.md: these machines sit unattended before the demo.
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target >/dev/null 2>&1 || true
echo "suspend and hibernate masked"

say "Done"
echo
"$ROOT/tools/centron-pair.sh"
echo
echo "Still to do by hand:"
echo "  1. Confirm the gateway in tools/protected_assets.serverpi.yaml (marked ASSUMED)."
echo "  2. Point Ollama's keep_alive high so the model does not unload before the demo."
echo "  3. Forward logs here:  *.*  @@${HOSTNAME}:5514   in rsyslog on the sources."
echo
echo "Follow the engine with:  journalctl -u ${SERVICE_NAME} -f"
