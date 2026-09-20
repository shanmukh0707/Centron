# Deploying the engine to serverpi

Everything here runs on serverpi unless it says otherwise. Ordered the way you
would actually do it.

## Once, on serverpi

```bash
git clone <remote> ~/sentinel && cd ~/sentinel
./tools/deploy-serverpi.sh
```

That script installs the venv, runs the 173 tests and refuses to continue if
they fail, prompts for `ANTHROPIC_API_KEY` and `OLLAMA_BASE_URL`, generates the
TLS certificate, generates the token, installs and starts the systemd unit,
masks suspend, and finally prints the pairing code.

It will not overwrite an existing `.env` or certificate. Regenerating either
invalidates a phone that is already paired, so that has to be deliberate.

## Pairing the phone

On serverpi:

```bash
./tools/centron-pair.sh
```

It prints one line:

```
centron://100.110.30.122:8765/<64 hex chars>/<token>
```

In the app: **Skip → Pair**, paste, done. The app stores the address, the
certificate's SHA-256 and the token.

That line is a credential. Anyone holding it can drive the engine. It contains
no private key and no Anthropic key, so it is safe to type across a room, but
not safe to put in a screenshot.

To rotate: `./tools/centron-pair.sh --new-token`, then re-pair every phone.

## What authenticates what

From `docs/contracts.md`: *"the pinned cert plus the pre-shared token is what
actually authenticates the client."*

| Layer | Answers |
|---|---|
| Pinned certificate | Is this the engine I paired with? |
| Pre-shared token | Is this client allowed to talk to it? |
| `biometric: true` | Did the operator unlock the phone before approving? |

The third is a **claim by the app**, not proof to the engine. The app does back
it with a Keystore key that requires a fresh unlock, but that signature never
goes on the wire — Contract 1 carries a bool. Do not treat it as authentication.

Token checking is off when `SENTINEL_TOKEN` is unset, so fixture runs, the test
suite and UI work against the local stub are unaffected. `deploy-serverpi.sh`
always sets it.

## The certificate

`gen-cert.sh` puts the Tailscale IP, the Tailscale hostname, the LAN IP and
localhost in the SAN. contracts.md calls out the failure this avoids: a
certificate issued only for the LAN address is rejected the moment the phone
connects from the venue.

Run it **after** Tailscale is up. It reads the real address from
`tailscale ip -4` and warns if that differs from the one in contracts.md.

The app trusts this certificate and no other. Not "this certificate or anything
a CA signed" — no certificate authority can issue a substitute. If the engine's
certificate is regenerated, the phone refuses to connect until it is re-paired.
That refusal is the pin working.

## Running it

The unit runs:

```
stub_server.py --source pipeline --syslog --live-ollama \
  --db sentinel.db --certfile certs/cert.pem --keyfile certs/key.pem
```

`--live` is deliberately absent. Invariant 10: the executor stays in dry run
through the demo.

```bash
systemctl status sentinel
journalctl -u sentinel -f
sudo systemctl restart sentinel
```

## Still manual

1. **`backend/protected_assets.yaml`** is filled from the live tailnet, but the
   gateway is marked `ASSUMED`. Confirm it before `--live` is ever used.
2. **Ollama `keep_alive`** on the PC, high, so the model does not unload while
   the machines idle before the demo.
3. **Log forwarding.** See *Adding a machine* below. Loopback is excluded by
   default — rsyslog forwarding to `127.0.0.1:5514` on the same box is a
   feedback loop. `--syslog-allow-local` overrides it for dev.

## Adding a machine

On serverpi run `./tools/add-log-source.sh <that machine's LAN IP> <auth|firewall|dns>`; it registers the sender in `backend/source_map.yaml` and prints the one rsyslog line to paste on that machine (or copy `tools/rsyslog-client.conf` to its `/etc/rsyslog.d/`, then `systemctl restart rsyslog`).
Forwarded lines only arrive when the unit runs `--syslog` instead of `--tail` (edit `tools/sentinel.service`, re-run the deploy script); the traditional-timestamp template in that line is mandatory, Debian 13's RFC 3339 default parses as nothing.

## Before filming, before the stage

```bash
bash tools/preflight.sh
```

Nine PASS/FAIL lines in under ten seconds: service, websocket hello, keys in
the *service's* environment, Ollama model loaded with its GPU/CPU split, a real
audio render fetched and checked for silence, the last heartbeat's pipeline
block verbatim, the database, the protected-assets file. Exit status is the
number of failures. The audio check is also what proves the ElevenLabs
renderer to the heartbeat: `tts` stays `degraded` until a render has actually
succeeded.

## Local development, unchanged

None of the above is needed to work against the stub:

```bash
cd backend
python stub_server.py --port 8765 --scenario calm --quiet
```

On the host, with the phone on USB:

```bash
adb reverse tcp:8765 tcp:8765
```

The app defaults to `ws://127.0.0.1:8765/ws`, plaintext, no pin, no token.
Choose **Use the local stub instead** on the pairing screen to get back to it.
