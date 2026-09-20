# Centron

The Android client for the Sentinel engine. An ambient voice SOC for a home
lab: the phone watches the engine's event stream, speaks what matters, and is
the only place an action can be approved.

Kotlin, Jetpack Compose, `minSdk 26`, `targetSdk 35`.

---

## The idea

A home lab has no security operations centre. It has one person who is
sometimes looking at a screen. The product assumption is that the phone talks
so the person does not have to watch, and that anything destructive stops and
asks for a fingerprint.

Two rules follow from that, and most of this app is downstream of them:

**Never block an event on speech.** An event reaches the phone whether or not
its audio is ready. Audio catches up afterwards.

**Nothing acts without a fresh unlock.** Approval and device power both run
through a Keystore key that is unusable until the OS has authenticated the
person for that specific operation.

---

## Screens

### Sign in

OAuth buttons for Google, Microsoft and GitHub, plus email.

**None of them are wired.** Each says so on screen rather than spinning: Google
sign-in needs an OAuth client and a backend to exchange the code, and neither
exists. "Skip — connect to an engine directly" is the working path, and is
what the security model actually depends on.

### Pairing

The engine prints a pairing code:

```
centron://<host>:<port>/<sha256-of-cert>/<token>
```

Pasting it configures the address, the certificate pin and the bearer token in
one step. The screen explains what pinning means, including that regenerating
the engine's certificate forces re-pairing — so that refusal reads as the
protection working rather than a bug.

"Use the local stub instead" points the app at `127.0.0.1:8765` over an
`adb reverse` tunnel, plaintext, no pinning. Development only.

### Home

Greeting, a fleet-wide chat box, and the device list. Each device row shows
its live dot, role, and the stats it is configured to show, with a power
toggle for devices that have one.

### Device detail

Ordered by blast radius ascending — read-only facts first, then per-container
controls, then device power last. The most destructive control on the screen
is never the first thing under your thumb.

- **Graphs.** CPU, memory, disk and temperature as series rather than four
  numbers that could be a minute stale. Percentages are pinned to 0–100, so
  idle noise does not read as a spike; temperature autoscales, because it has
  no natural ceiling. A gap in reporting breaks the line instead of drawing a
  cliff to zero that never happened.
- **Containers.** Name, image, health, CPU and memory, with start/stop.
  Anything the engine refuses to stop — such as the engine itself — renders as
  `pinned` instead of a control.
- **Power.** Off and wake, behind the fingerprint gate.
- **Ask Centron** about the device, or about a single container.

### Device settings

The gear in the top right. Per device:

- Name, address, description
- Which stats appear — turning one off hides it here, it does not stop the
  engine collecting it
- Show power controls at all. For a box you cannot afford to lose, removing
  the button beats trusting yourself not to press it
- Require fingerprint for power changes
- Remove the device, with a confirm step

SSH user, port and credentials are **not** editable. The phone never holds
credentials, so a screen that appeared to edit them would lie about where the
authority lives.

### Alerts

A panel from the bell, and a full screen. High and critical push a system
notification; the lock screen only ever shows `tts_summary`, which is
value-free by construction.

### Approval

What the engine wants to do, what it will run, and the escalation state.
There is deliberately no `diagnose()` call: `escalation.verdict` already is
the diagnosis, and a late verdict re-emits the same `event_id` with a fresh
seq, so the screen re-renders on its own.

Approve or deny both require a fingerprint.

### Chat

Four scopes: the whole fleet, one device, one container, one event.
Device and container scopes pin the conversation to that target.

Answers come from Claude via the engine. It is told to refuse state changes
and point back at the approval flow, so chat cannot become a second, unlocked
path to acting on the lab.

### Settings

- **Appearance.** Dark or creme.
- **Alerts.** Speak alerts on or off, history retention.
- **Security.** Strict approval — require re-auth for every action, not only
  destructive ones. A per-device fingerprint waiver cannot override this.
- **Site.** Re-pair.
- **Danger.** Forget this site, with a confirm step.

---

## How it behaves

### Severity

Defined by what the phone does, not by colour:

| | |
|---|---|
| `info` | Never spoken. Not queued, not fetched. |
| `low` | Queued behind whatever is playing. |
| `high` | Interrupts and plays now. |
| `critical` | Interrupts, plus alert tone and haptic. |

An interrupt **clears** the backlog rather than jumping the queue. Four
minutes of stale low-severity narration ahead of a critical is the wrong
behaviour.

### Speech

Audio is fetched with `OkHttpDataSource` built from the same pinned client as
the socket — the audio URL sits behind the same certificate and token, and
ExoPlayer's default HTTP stack knows about neither.

ExoPlayer is pinned to the main looper and every call hops there. It is
single-threaded and throws if touched from anywhere else; frames arrive on the
socket's coroutine.

**Only `tts_summary` is ever spoken or shown on a lock screen.**
`internal_log` carries real hostnames and addresses and reaches neither.

**Dedupe is on `cache_key`, not `event_id`.** A late verdict re-emits the same
event with the same phrase, and the operator should not hear the same sentence
twice because a cloud model answered slowly. Consequence worth knowing: the
same phrase only speaks once per app session.

The critical tone is a synthesised falling minor third, G3 to E3, at fixed
length. Synthesised rather than taken from `RingtoneManager` because system
alarm tones loop until told to stop, and because whatever the owner has chosen
is often cheerful — the wrong register for a message about someone attacking
their network.

### Connection state

Driven by the heartbeat, never by socket callbacks:

| | |
|---|---|
| Green | Heartbeat within 15s |
| Amber | No heartbeat for 15s |
| Red | No heartbeat for 25s — pipeline untrusted |

Red also posts a notification. The engine runs on the home lab, so if the home
lab is down it cannot tell you; noticing the silence is the phone's job.

### Storage

Room, inserting with `OnConflictStrategy.REPLACE`. A late verdict arrives as
the same `event_id` with a new seq and must update the row, not be dropped.

---

## Security

**Pinned TLS.** `PinnedTrust` replaces the trust manager with one that accepts
exactly one certificate, matched on SHA-256 of its DER encoding. No certificate
authority can issue a substitute.

`CertificatePinner` does not work here — it runs after chain validation, which
a self-signed certificate fails. If TLS is requested and no pin is configured,
the client throws rather than falling back to trusting anything.

**Bearer token** on the socket upgrade, audio fetches and chat. One way in, no
laxer second path.

**Biometric gate.** Approvals are signed by an EC key in the Android Keystore
created with `setUserAuthenticationRequired(true)`,
`setInvalidatedByBiometricEnrollment(true)` and an auth window of zero, so
every use needs a fresh unlock. Enrolling a new fingerprint destroys the key:
previously issued approval authority dies rather than silently transferring.

The payload binds approval id, event id, decision and timestamp, so a
signature cannot be replayed onto a different action. Power changes bind device
id, host and direction.

**What this is worth, precisely:** `biometric: true` on the wire is a claim by
the app. The pinned certificate plus the pre-shared token is what actually
authenticates the client. The signature is local evidence and is not currently
sent.

---

## Known gaps

Listed because a demo that hides them is worse than one that does not.

- **OAuth is not wired.** All four sign-in methods report it.
- **Device probing, alert history and revert** report unreachable. They need
  engine features that do not exist, and a plausible fake would be worse than
  an honest refusal.
- **Device telemetry has no live source.** The graphs are real code against
  `applyTelemetry`, which nothing calls — the contract carries no device
  telemetry frame. Seeded series are marked placeholder in source, and a real
  paired site shows "no history yet".
- **The device list is seeded.** Marked in `DeviceRegistry`, and goes before
  this is shown as a real inventory.
- **Pairing persists but the UI still cold-starts to the pairing screen.**
  The stored pairing is loaded and the service reconnects with it; the route
  does not yet reflect that.
- **The bearer token is stored in app-private plaintext.** It wants to be a
  Keystore-wrapped blob.
- **Settings other than the pairing are in memory** and reset on restart.

---

## Building

```bash
cd android
./gradlew assembleDebug
./gradlew installDebug
```

Needs JDK 17.

Do not build inside a synced folder. OneDrive holds handles on files under
`build/` while Gradle is writing them, which surfaces as
`AccessDeniedException` on unrelated files.

For the local stub instead of a paired engine:

```bash
adb reverse tcp:8765 tcp:8765
```

then choose "Use the local stub instead" on the pairing screen.

Disable battery optimisation for Centron on any device used for a demo. The
foreground service is what keeps the stream alive with the screen off, and
Android will eventually throttle it otherwise.

---

## Layout

```
audio/      SpeechQueue — severity contract, playback, alert tone
contract/   Frame types mirroring the engine's schema
data/       Room entities, DAO, repository
device/     Device model, registry, telemetry history
engine/     CentronEngine, HttpEngine (chat), chat scopes
net/        EngineConfig, PinnedTrust, SentinelClient, connection state
notify/     AlertCenter and notification channels
security/   BiometricGate
service/    SentinelService — the foreground stream
settings/   AppSettings, sites, persisted pairing
ui/         Screens, theme, navigation
```

`contract/Frames.kt` mirrors the engine's `schemas.py`. That file and
`contract.json` belong to the engine's author; if a field is needed, ask for
it rather than adding one.
