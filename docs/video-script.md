# Video script — DELIVERABLES TRACK item 5

Hard cap: under 3 minutes. Film this once the demo is actually working end to
end (per `build-plan.md`: film while it still works, not at 2 AM when
something breaks). This doc is the shot list and narration — the filming
itself is a physical step, not something this repo can do on its own.

| Time | Shot | Narration / on-screen |
|---|---|---|
| 0:00–0:15 | Cold open: phone on a desk, screen dark/idle. Cut to it lighting up and speaking. | *(let the phone's own audio play first, no voiceover)* "That's Sentinel telling me someone's trying to break into my server. I didn't check a dashboard — it just told me." |
| 0:15–0:40 | Presenter to camera. | "Most small businesses can't afford a SOC or an MSSP retainer — those start around $24,000 a year. So the businesses that most need to know what's happening on their network are the ones with nobody watching it. Sentinel is an ambient voice SOC that runs on a single GPU in the closet and costs a fraction of that." |
| 0:40–1:05 | Architecture diagram on screen (reuse the mermaid flowchart from `build-plan.md`). | "It tails logs locally, redacts anything sensitive before a local model ever sees it, and only calls out to the cloud — Claude — when it's genuinely unsure, on redacted text only. No raw logs ever leave the network." |
| 1:05–1:45 | Live demo cut 1: run `docs/demo-script.md` Beat 1 (`ssh_bruteforce`). Show the laptop terminal briefly, then the phone speaking, then the auto-block confirmation. | "Watch — I trigger a brute-force attempt against my file server. [silence, let the phone speak] It already blocked the source. I didn't touch anything." |
| 1:45–2:15 | Live demo cut 2: Beat 2 (`correlation_burst`) and the biometric prompt. | "When it's not sure enough to act on its own — like three different addresses hitting the same host — it asks me first, on my phone, with a fingerprint." |
| 2:15–2:35 | Live demo cut 3: Beat 3 (`prompt_injection`). Show the raw log line with the injection text on screen. | "And because attackers control the log text a model reads, we tested prompt injection directly — a login attempt telling the system to ignore its instructions. It's neutralized before it ever reaches the model." |
| 2:35–2:55 | Presenter to camera, closing. | "Sentinel: a SOC analyst quietly watching your network, who only interrupts you when it matters, and who you can carry in your pocket." |

## Notes for the edit

- Keep the phone's own audio in the final cut wherever possible — a synthetic voice actually speaking, unprompted, is the single most persuasive thing in this video. Don't voice over it.
- If a live beat doesn't cooperate on the take you're filming, use the fallback clips from `docs/fallback-audio.md` rather than re-recording the whole demo from scratch.
- Total runtime target: ~2:55, leaving a few seconds of margin under the 3:00 cap.
