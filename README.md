<p align="center">
  <img src="docs/assets/centron-logo.png" alt="Centron — ambient voice security" width="460">
</p>

<p align="center">
  An ambient voice SOC for a home lab. The phone watches, speaks, and asks
  before anything touches a machine.
</p>

---

A home lab has no security operations centre. It has one person who is
sometimes looking at a screen.

Centron reads real logs, decides what matters with a local model, escalates
the hard calls to Claude, and **says the answer out loud** so nobody has to be
watching. Anything destructive stops and asks for a fingerprint.

## How it fits together

```
logs ──▶ parser ──▶ local model ──▶ gates ──▶ event ──▶ phone (speaks)
                         │                                  │
                         └──▶ Claude, when it is hard       └──▶ approve
                                                                  with a
                                                                  fingerprint
                                                                     │
                                                            executor (dry run)
```

Three pieces:

| | |
|---|---|
| **[`backend/`](backend)** | The engine. Log ingestion, Ollama, Claude escalation, TTS, the WebSocket the phone lives on. Python. |
| **[`android/`](android)** | The Centron app. Kotlin and Compose. **[Full feature docs →](android/README.md)** |
| **[`tools/`](tools)** | Deployment: certificate generation, pairing, the systemd unit, preflight. |

## What it does

**Speaks.** ElevenLabs when a key is present, espeak-ng locally otherwise, so
the phone still talks with no API key at all. Phrases are cached, so a
repeated sentence costs nothing.

**Never blocks an event on speech.** The event ships immediately with the
audio marked pending; the render follows and the phone catches up. A TTS
failure degrades the audio, never the alert.

**Decides locally first.** `qwen2.5:14b` over Ollama handles the ordinary
cases. Claude is asked only when the local model is out of its depth, and its
verdict re-emits the same event rather than arriving as a separate thing to
reconcile.

**Refuses to act on its own.** The executor runs in dry run. Nothing
auto-executes against an internal address, and protected assets are refused
outright.

**Asks with a fingerprint.** Approvals are signed by a key in the Android
Keystore that is unusable until the OS has authenticated the person for that
specific operation.

**Says nothing it should not.** The spoken summary is value-free by
construction — no addresses, usernames, ports or tokens — and there is a test
enforcing it. The detailed log stays on the device screen.

## Security model

The phone reaches the engine over Tailscale. That connection is authenticated
by **a pinned self-signed certificate plus a pre-shared token**, and pairing is
a single line the engine prints:

```
centron://<host>:<port>/<sha256-of-cert>/<token>
```

The app trusts exactly that one certificate, matched on the SHA-256 of its DER
encoding. No certificate authority can issue a substitute. If TLS is requested
and no pin is configured, the client refuses to connect rather than falling
back to trusting anything.

The engine holds every credential. **The phone never holds an SSH key or a
password for any machine** — it describes devices, and asks the engine to act.

To be precise about one thing: `biometric: true` on an approval is a claim by
the app. The pinned certificate and the token are what actually authenticate
the client.

## Running it

Engine, on the box that will host it:

```bash
git clone <remote> ~/sentinel && cd ~/sentinel
./tools/deploy-serverpi.sh
```

That builds the virtualenv, runs the tests, generates a certificate, installs
the systemd unit and prints a pairing code. It will not turn on live execution
and it will not fill your protected-asset list, both deliberately.

App:

```bash
cd android && ./gradlew installDebug
```

Paste the pairing code on first launch.

Keys go in `backend/.env` — `ANTHROPIC_API_KEY` for escalation and chat,
`ELEVENLABS_API_KEY` for the intended voice. Both are optional: without them
escalation reports unreachable and the voice falls back to espeak, and
everything else works.

## State of it

Working and verified on real hardware: the event stream, speech end to end,
the severity behaviour, notifications, the fingerprint gate, pinned TLS and
token auth, and chat.

Not working, and honest about it in the UI: OAuth sign-in, device probing over
SSH, alert history, revert, and live device telemetry. The
**[app README](android/README.md)** has the full list with reasons.

The executor stays in dry run.
