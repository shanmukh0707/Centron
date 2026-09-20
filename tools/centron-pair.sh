#!/usr/bin/env bash
# Print the pairing code for the Android app.
#
# The code is one line:
#
#     centron://<host>:<port>/<sha256-of-cert>/<token>
#
# It carries the address, the certificate pin, and the pre-shared token — which
# together are what contracts.md says actually authenticates the client. It
# carries no private key and no Anthropic key, so it is safe to read aloud
# across a room or type into a phone, but it IS a credential: anyone holding it
# can drive the engine. Treat it like a password.
#
#   ./tools/centron-pair.sh              # print the code
#   ./tools/centron-pair.sh --new-token  # rotate the token first
#
# Rotating the token invalidates every previously paired phone.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${CERT_DIR:-$ROOT/backend/certs}"
ENV_FILE="${ENV_FILE:-$ROOT/backend/.env}"
PORT="${PORT:-8765}"
NEW_TOKEN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --new-token) NEW_TOKEN=1; shift ;;
    --port) PORT="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

if [[ ! -f "$CERT_DIR/cert.pem" ]]; then
  echo "No certificate at $CERT_DIR/cert.pem" >&2
  echo "Run ./tools/gen-cert.sh first." >&2
  exit 1
fi

# --- token -----------------------------------------------------------------
touch "$ENV_FILE"
chmod 600 "$ENV_FILE"

current_token="$(grep -E '^SENTINEL_TOKEN=' "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"

if [[ $NEW_TOKEN -eq 1 || -z "$current_token" ]]; then
  # 32 bytes of urandom, hex. Long enough that guessing is not a strategy.
  current_token="$(openssl rand -hex 32)"
  # Rewrite rather than append, so the file does not accumulate dead tokens
  # that someone later mistakes for the live one.
  if grep -qE '^SENTINEL_TOKEN=' "$ENV_FILE"; then
    sed -i "s|^SENTINEL_TOKEN=.*|SENTINEL_TOKEN=${current_token}|" "$ENV_FILE"
  else
    echo "SENTINEL_TOKEN=${current_token}" >> "$ENV_FILE"
  fi
  echo "Generated a new token. Previously paired phones are now rejected." >&2
  echo >&2
fi

# --- host ------------------------------------------------------------------
HOST="${HOST:-}"
if [[ -z "$HOST" ]] && command -v tailscale >/dev/null 2>&1; then
  HOST="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
fi
HOST="${HOST:-100.110.30.122}"

# The pin must match what the app computes: SHA-256 over the DER encoding.
PIN="$(openssl x509 -in "$CERT_DIR/cert.pem" -outform DER | openssl dgst -sha256 | awk '{print $NF}')"

# Warn loudly if the cert does not actually cover the address we are handing
# out, because the failure otherwise appears only at the venue.
if ! openssl x509 -in "$CERT_DIR/cert.pem" -noout -text | grep -q "$HOST"; then
  echo "WARNING: $HOST is not in the certificate SAN." >&2
  echo "         The app will reject this connection. Re-run gen-cert.sh." >&2
  echo >&2
fi

echo "Pairing code — paste into Centron on the phone:"
echo
echo "  centron://${HOST}:${PORT}/${PIN}/${current_token}"
echo
echo "Certificate expires: $(openssl x509 -in "$CERT_DIR/cert.pem" -noout -enddate | cut -d= -f2)"
