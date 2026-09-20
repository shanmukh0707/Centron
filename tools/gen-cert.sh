#!/usr/bin/env bash
# Generate the engine's self-signed TLS certificate.
#
# contracts.md, DEPLOYMENT: "The self-signed cert needs serverpi's Tailscale
# hostname or IP in its SAN. The app pins the cert, so one generated only for
# the LAN address is rejected once the phone connects from the venue."
#
# So the SAN covers every address the phone might legitimately use:
#   - the Tailscale IP          (venue, and anywhere else)
#   - the Tailscale hostname    (same, by name)
#   - the LAN IP                (apartment, filming the video)
#   - localhost                 (curl on the box itself)
#
# Run this AFTER Tailscale is up, so the addresses are real.
#
#   ./tools/gen-cert.sh
#   ./tools/gen-cert.sh --lan 192.168.1.42        # override detection
#
# Regenerating the certificate invalidates the pin. Re-run centron-pair.sh and
# re-pair the phone, or it will refuse the connection — which is the pin doing
# its job, not a bug.

set -euo pipefail

CERT_DIR="${CERT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backend/certs}"
DAYS="${DAYS:-825}"
TS_IP="100.110.30.122"
TS_NAME="serverpi"
LAN_IP=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --lan) LAN_IP="$2"; shift 2 ;;
    --ts-ip) TS_IP="$2"; shift 2 ;;
    --ts-name) TS_NAME="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

# Detect the Tailscale address rather than trusting the default, because a
# tailnet can hand out a different one after a re-auth.
if command -v tailscale >/dev/null 2>&1; then
  detected="$(tailscale ip -4 2>/dev/null | head -n1 || true)"
  if [[ -n "$detected" ]]; then
    if [[ "$detected" != "$TS_IP" ]]; then
      echo "note: tailscale reports $detected, not $TS_IP. Using $detected." >&2
      echo "      Update docs/contracts.md and the app if this is permanent." >&2
    fi
    TS_IP="$detected"
  fi
else
  echo "warning: tailscale not found. Using $TS_IP from contracts.md." >&2
  echo "         If that is wrong, the phone will fail hostname verification." >&2
fi

if [[ -z "$LAN_IP" ]]; then
  LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -n1 || true)"
fi

mkdir -p "$CERT_DIR"
chmod 700 "$CERT_DIR"

SAN="DNS:${TS_NAME},DNS:localhost,IP:${TS_IP},IP:127.0.0.1"
[[ -n "$LAN_IP" ]] && SAN="${SAN},IP:${LAN_IP}"

echo "Generating certificate"
echo "  SAN:  $SAN"
echo "  days: $DAYS"
echo "  into: $CERT_DIR"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "$CERT_DIR/key.pem" \
  -out "$CERT_DIR/cert.pem" \
  -days "$DAYS" \
  -subj "/CN=${TS_NAME}" \
  -addext "subjectAltName=${SAN}" \
  -addext "basicConstraints=critical,CA:FALSE" \
  -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
  -addext "extendedKeyUsage=serverAuth" \
  2>/dev/null

chmod 600 "$CERT_DIR/key.pem"
chmod 644 "$CERT_DIR/cert.pem"

FPR="$(openssl x509 -in "$CERT_DIR/cert.pem" -outform DER | openssl dgst -sha256 | awk '{print $NF}')"

echo
echo "Done."
echo "  cert:        $CERT_DIR/cert.pem"
echo "  key:         $CERT_DIR/key.pem   (never leaves this machine)"
echo "  SHA-256 pin: $FPR"
echo
echo "Next: ./tools/centron-pair.sh   to print the pairing code for the app."
