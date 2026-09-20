We are building Sentinel during Hack ITP, a hackathon ending Sunday 20 September at 3:00 PM.

WHAT SENTINEL IS
An ambient voice SOC for small businesses and solo IT admins who cannot afford a security
operations center. It tails network logs locally, narrates events in plain language using a
local LLM, selects and executes remediation playbooks, and speaks alerts to a native Android
app. No raw logs leave the network. Think SOAR for people who cannot afford SOAR.

ARCHITECTURE
Log source -> regex parse -> aggregation buffer (30-60s, collapse duplicates by source and
action) -> deterministic redaction in Python (IPs, hostnames, usernames, MACs become tokens
like HOST_A and USER_1, mapping table stays local) -> Ollama (Qwen2.5-Coder-14B Q4_K_M)
returns JSON with fields internal_log, tts_summary, confidence, escalate, escalate_reason,
action, action_params -> Python validates the action against the playbook allowlist, checks
parameter caps, resolves tokens to real values, executes -> if an escalation gate fires, the
redacted payload goes to the Claude API for reasoning and the response tokens are re-localized
against the local mapping table -> internal_log to SQLite, tts_summary to ElevenLabs -> audio
and JSON pushed over encrypted WebSocket -> Android app plays audio and logs the event.

REMEDIATION MODEL
The local model does NOT author commands. It selects a playbook from a fixed catalog and fills
its parameters, e.g. {"action": "block_ip", "target": "HOST_A", "duration_sec": 3600}. Python
validates and executes. This mirrors how real SOAR platforms work (XSOAR, Splunk SOAR, Tines):
parameterized playbooks, never improvised commands. It also closes a real attack path, since log
text is attacker-controlled and a model authoring free-form commands can be steered into
blocking a legitimate host.

Playbook catalog: block_ip, rate_limit, isolate_host, watch, notify_only. Each has typed
parameters and hard caps (max duration, max CIDR breadth). Reversible low-blast-radius actions
may auto-execute and must auto-expire. Anything destructive requires biometric approval on the
phone.

If the model is to produce actual code, it goes in a non-executing lane only: it drafts a
remediation script, the app displays it, a human reviews and runs it. Generated text never
executes autonomously.

ESCALATION GATES
A 14B is overconfident about what it knows, so its self-report is one input among several, not
the decision. Deterministic gates are checked in Python before and after inference:
- Unknown pattern: event signature not in the playbook catalog
- Novelty: signature absent from SQLite history
- Correlation: 3+ distinct sources inside one window
- Schema failure: invalid JSON or action outside the allowlist after 2 retries
- Blast radius: chosen action exceeds its parameter cap
- Severity floor: model returns critical
Model self-report: confidence below ~0.7 escalates; escalate=true requires a non-empty
escalate_reason so the model must articulate what it cannot resolve.
Rate control: cooldown of one escalation per event signature per hour, plus a global hourly
ceiling; past the ceiling, queue for the digest instead of escalating live.
Fail closed: if Claude is unreachable the event still narrates and still logs, flagged unreviewed
in the app. Never silently drop it.

STACK
Backend: Python 3.11+, FastAPI, Uvicorn, websockets, SQLite.
Local AI: Ollama running Qwen2.5-Coder-14B at Q4_K_M (~9GB), sized to fit entirely in VRAM.
  Hardware is an i7-14700KF, 32GB DDR5, RTX 4070 Super 12GB. Do not propose 32B or 70B models:
  anything above ~14B spills into system RAM and drops inference to a few tokens per second,
  which breaks the live demo. Fallbacks if latency disappoints: Mistral Nemo 12B, Llama 3.1 8B.
  Call Ollama with format: "json" and keep_alive set so the model stays warm.
Escalation: Claude API via the Anthropic Python SDK, redacted payloads only.
Voice: ElevenLabs Python SDK with streaming, cache repeated phrases to conserve credits.
Android: Kotlin, minSdk 26+, OkHttp WebSocket inside a foreground service, ExoPlayer with a
severity-aware playback queue, Room for local history, CameraX plus ML Kit for QR pairing,
AndroidX Biometric for the action gate, EncryptedSharedPreferences for the token.
Transport: WebSocket over TLS on the LAN, self-signed cert pinned in the app.

NON-NEGOTIABLES
- Redaction is deterministic Python regex and runs BEFORE any model sees log text. The local
  model never does the redacting.
- Log text is attacker-controlled. Treat injection as a live threat regardless of model size.
- The model selects playbooks and fills parameters. It never authors commands that execute.
- Every action is validated against an allowlist and parameter caps before execution.
- Destructive actions require biometric approval. Auto-executed actions must be reversible and
  must auto-expire.
- Escalation is decided by deterministic gates first, model self-report second.
- The Android WebSocket lives in a foreground service with a declared foregroundServiceType,
  ping interval set, and exponential backoff reconnect.
- A heartbeat drives the UI. If the server link drops, the app must visibly go red, never sit
  on a stale all-quiet screen.
- Two separate databases: SQLite on the server holds the full internal_log, Room on the phone
  caches event history for the UI. Do not collapse them.

HOW TO HELP US — THIS PROJECT IS FOR THINKING, NOT IMPLEMENTATION
Do not write implementation code here. We build in Claude Code; this project is where we decide
what to build and check whether it was built right. When a request would end in a file, stop at
the spec and hand us a prompt to paste into Claude Code instead.

Belongs here:
- Architecture and design decisions, and the tradeoffs behind them
- Contract design: event schema fields and types, playbook catalog definitions and caps, the
  Ollama JSON output shape — described as specs, not as .py files
- System prompt and few-shot example authoring for the local model (prose, not plumbing)
- Escalation gate logic: which gates, what thresholds, why
- Reviewing code we paste in, and reasoning about bugs we describe
- README, pitch, ICP and TAM writing
- Demo sequencing and rubric strategy
- Writing the prompts we hand to Claude Code

Does not belong here:
- Writing modules, scaffolding projects, or producing runnable files
- Anything that ends in "here is the full implementation"
Short illustrative snippets are fine — a JSON example, three lines showing a pattern. A file is not.

We are graded on business validation, technical quality, demo-ability, and creativity, 5 points
each. When reviewing our work, flag anything that weakens one of those. We are time-constrained,
so lead with the answer.
