# Fallback audio clips — DELIVERABLES TRACK item 4

Venue wifi is the single most likely thing to fail on stage (see
`build-plan.md`'s "Known failure modes" table). If it does, the phone can't
reach ElevenLabs or the backend, so it can't speak live. These are the exact
lines to pre-record — read them yourself, or generate them once through the
real ElevenLabs voice ahead of time and save the MP3s — so the demo can keep
narrating from a local file instead of going silent.

Each line follows the same constraints CONTRACT 3 in `docs/contracts.md`
puts on the model's real `tts_summary` output, so a fallback clip sounds
exactly like what the live system would have said: exactly one sentence,
max 20 words / 140 characters, no IPs, MACs, usernames, `@`, or port numbers,
role nouns only ("a workstation", "your file server", "an external address").

| # | Beat | Line to record |
|---|---|---|
| 1 | Beat 1 — auto-executed block | "An external address is repeatedly failing logins on your file server, and I'm blocking it automatically for one hour." |
| 2 | Beat 2 — approval required | "Three separate external addresses are hitting your file server at once, and I need your approval before I act." |
| 3 | Beat 3 — injection defense | "A login attempt just tried to talk me into ignoring my instructions, and I'm ignoring it instead." |
| 4 | Wifi loss, any time | "Connection to the server was lost, so I'm falling back to locally cached alerts until it reconnects." |

## Recording checklist

- [ ] Record (or ElevenLabs-generate) all four lines as individual MP3s
- [ ] Name them so they're obvious under stage pressure: `01_auto_block.mp3`, `02_approval_required.mp3`, `03_injection_defense.mp3`, `04_wifi_lost.mp3`
- [ ] Load them onto the demo phone AND the laptop, so either device can play them if the other's audio path is what failed
- [ ] Test playback at actual demo volume in a noisy room, not headphones at your desk

This doc only gets you the script — the actual recording is a physical step
that has to happen with a real mic (or a real ElevenLabs call), not something
this repo can do on its own.
