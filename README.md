# Sentinel

An ambient voice SOC for small businesses and solo IT admins who cannot afford a security operations center. Sentinel tails network logs locally, narrates events in plain language using a local LLM, selects and executes remediation playbooks, and speaks alerts to a native Android app. No raw logs ever leave the network.

Think SOAR for people who cannot afford SOAR.

## Problem statement

A real SOC — 24/7 analysts triaging alerts and pulling the trigger on remediation — runs small businesses tens of thousands of dollars a year, and most SMBs don't have one. Managed security services aren't much cheaper: small-business MSSP packages commonly run **$24,000+ a year** even at the low end, with basic monitoring retainers starting around **$5,000/month**, and per-user pricing landing anywhere from **$15 to $250+ per user per month** depending on service depth ([RSI Security](https://blog.rsisecurity.com/how-much-does-managed-security-services-cost/), [MSSPProviders.io](https://msspproviders.io/resources/how-much-does-an-mssp-cost/)).

So the businesses that most need visibility into their own network — the ones without a dedicated IT security hire — are the ones priced out of getting it. What they have instead is a firewall, maybe a Pi-hole, and logs nobody reads until after the incident. Attacks and misconfigurations sit unnoticed for hours or days, and log text itself is attacker-controlled, so even the tools that do exist are exposed to prompt-injection-style manipulation once an LLM is anywhere in the loop.

## Solution

Sentinel is a local-first pipeline that gives a solo admin the reflexes of a SOC analyst without the payroll:

1. **Log source → regex parse.** Strict, deterministic extraction of timestamp, source, and action. Log text is attacker-controlled, so it never reaches a model unparsed.
2. **Aggregation.** A 30–60s buffer collapses duplicate source/action pairs so a thousand failed SSH attempts become one alert, not a thousand notifications.
3. **Deterministic redaction.** Python regex replaces IPs, hostnames, usernames, and MACs with tokens (`HOST_A`, `USER_1`) before any model sees the text. The model never redacts its own input — that would be circular trust.
4. **Local inference.** Qwen2.5-Coder-14B via Ollama, running entirely on a single consumer GPU, returns structured JSON: a plain-language summary, a confidence score, and — critically — a **playbook selection**, not a generated command.
5. **Playbook execution, not generated code.** The model picks from a fixed, typed catalog (`watch`, `notify_only`, `rate_limit`, `block_ip`, `isolate_host`) and fills parameters. Python validates against an allowlist and hard caps, resolves tokens back to real values, and executes. Reversible, low-blast-radius actions can auto-execute and auto-expire; anything destructive requires a biometric approval on the phone. This mirrors how real SOAR platforms (XSOAR, Splunk SOAR, Tines) work, and it closes the obvious attack path: a model that authors free-form commands from attacker-controlled log text can be steered into blocking a legitimate host.
6. **Escalation, gated by deterministic rules first.** A 14B model is confidently wrong sometimes, so its self-reported confidence is one input among several. Novelty, correlation across sources, schema failures, and blast-radius violations all force a second opinion from the Claude API on a **redacted** payload — real values never leave the network.
7. **Delivery.** JSON ships immediately over an encrypted WebSocket; narration is spoken through ElevenLabs. The Android app plays audio through a severity-aware queue and requires a biometric tap before any destructive action executes.

The result: an admin who's away from their desk still knows, in plain English and out loud, that something is happening — and Sentinel has often already handled it by the time they check their phone.

## Ideal customer profile

Small businesses and solo IT operators who:

- Run **on-prem or hybrid infrastructure** with real assets to protect (a file server, a VPN gateway, internal DNS) — not just a handful of SaaS logins
- Have **no dedicated security hire** and no budget for a 24/7 SOC or a full MSSP retainer
- Are already running or willing to run basic infrastructure like a firewall and Pi-hole, but nobody is watching the logs those tools produce
- Value **not carrying a laptop everywhere** — a solo admin needs to know something's wrong whether they're in the server closet, on the shop floor, or off-site

Concretely: regional MSPs' smallest and most underserved clients, independent professional practices (legal, medical, dental) running their own network gear, small manufacturers and logistics operators with OT/IT overlap, and self-hosting-minded IT generalists at 10–200 person companies.

## Total addressable market (TAM)

There are **36.2 million small businesses in the United States** as of the SBA Office of Advocacy's 2025 report ([SBA Office of Advocacy](https://advocacy.sba.gov/2025/06/30/new-advocacy-report-shows-the-number-of-small-businesses-in-the-u-s-exceeds-36-million/)). The large majority of that figure is non-employer sole proprietorships with no real network to protect; the commonly-cited employer-firm subset — businesses that actually run staff, servers, and internal infrastructure — is roughly **6–7 million** in the US.

Sentinel's pricing lane sits well under the MSSP floor found above (~$24,000+/year for even basic monitoring): a realistic subscription range of **$600–$1,800/year** ($50–$150/month) for a self-hosted, no-payroll alternative.

```
TAM = addressable employer small businesses × annual contract value
    ≈ 6.5M businesses × $1,200/year (midpoint)
    ≈ $7.8B  (range: $3.6B at 6M×$600, to $12.6B at 7M×$1,800)
```

This is a back-of-envelope estimate, not audited market research — it exists to show the shape of the opportunity, not to claim precision. The relevant comparison is qualitative: Sentinel targets a price point roughly **15–40x cheaper** than the cheapest MSSP retainer found in the sources above, aimed squarely at the segment currently priced out of any managed security option at all.

**Sources:**
- [New Advocacy Report Shows the Number of Small Businesses in the U.S. Exceeds 36 million — SBA Office of Advocacy](https://advocacy.sba.gov/2025/06/30/new-advocacy-report-shows-the-number-of-small-businesses-in-the-u-s-exceeds-36-million/)
- [How Much Does Managed Security Services Cost? — RSI Security](https://blog.rsisecurity.com/how-much-does-managed-security-services-cost/)
- [How Much Does an MSSP Cost in 2026? — MSSPProviders.io](https://msspproviders.io/resources/how-much-does-an-mssp-cost/)

## Try the prototype

No GPU or phone required to see the wire protocol and mock data in action:

```bash
# Terminal 1 — fake WebSocket event stream (what the Android app talks to)
python stub_server.py --port 8765 --rate 6 --scenario scripted

# Terminal 2 — live raw log traffic (what the backend's parsers tail)
python mock_log_generator.py --out logs --rate 6 --scenario scripted
```

See [docs/contracts.md](docs/contracts.md) for the full event schema and playbook catalog, and [docs/demo-script.md](docs/demo-script.md) for the staged walkthrough.
