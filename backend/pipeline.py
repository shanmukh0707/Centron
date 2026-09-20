"""Sentinel Phase 1 ingestion pipeline.

    log_source -> parsers -> aggregator -> redaction -> llm -> EventData

Provenance rules (enforced here, not trusted to the model):
  * From PARSER facts:  event_id, signature, title, entities, source.*, window.
  * From the MODEL:     internal_log, tts_summary, severity, confidence,
                        escalate, escalate_reason, action, action_params.
  * The model only ever sees a redacted ModelInput. A leak check runs on the
    prompt before every call and raises instead of sending.
  * internal_log is re-localized with RedactionMap.localize() so the phone
    gets real values; EventData rejects any leftover token. A token whose value
    is not identifier-shaped (an injected "username" with spaces) is replaced
    with a fixed marker first, so attacker free text never rides along.
  * tts_summary must pass validate_tts: one retry, then a deterministic
    template from (signature, severity). The event is never dropped.
  * action_params tokens are resolved locally and run through
    schemas.validate_and_classify before an ActionRef is built. Phase 1 has no
    executor: ActionRef.status records the classification decision
    (auto-eligible vs pending approval), nothing is executed.

CLI (run from backend/ or anywhere; the fixture default is anchored to this file):
    python pipeline.py --fixture --fake-llm --window 30
    python pipeline.py --fixture fixtures/sample_logs.txt --fake-llm
    python pipeline.py --tail /var/log/auth.log --fake-llm
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Protocol

from aggregator import AggregatedEvent, Aggregator
from llm import ModelInput, OllamaClient
from log_source import replay_fixture, tail_file
from parsers import SIGNATURE_META, LineParser, SignatureMeta
from redaction import IDENTIFIER_RE, TOKEN_RE, RedactionMap
from schemas import (
    ActionRef,
    Entities,
    Escalation,
    EventData,
    OllamaOutput,
    PlaybookParamsAdapter,
    Source,
    Window,
    format_ts,
    now_utc,
    uuid7,
    validate_and_classify,
    validate_tts,
)

HERE = Path(__file__).resolve().parent
DEFAULT_FIXTURE = HERE / "fixtures" / "sample_logs.txt"

_USER_ID_RE = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")
_HOST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CONFIDENCE_FLOOR = 0.7
APPROVAL_TTL_SEC = 600
_UNKNOWN_META = SignatureMeta("unknown", "unknown", "Unrecognised activity")
FREE_TEXT_MARKER = "[malformed username]"


class LLM(Protocol):
    def generate(self, mi: ModelInput) -> OllamaOutput: ...


# ------------------------------------------------------------ tts fallback
_TTS_TEMPLATES: dict[str, str] = {
    "ssh.auth.brute_force": "Repeated failed SSH logins from one outside address were detected",
    "ssh.auth.success": "A user signed in successfully over SSH",
    "net.portscan": "An outside address is scanning ports on the network",
    "dns.blocked": "A device on the network had tracker lookups blocked",
}
_SEVERITY_PREFIX: dict[str, str] = {
    "info": "For your information",
    "low": "Low priority",
    "high": "Attention",
    "critical": "Urgent",
}


def fallback_tts(signature: str, severity: str) -> str:
    body = _TTS_TEMPLATES.get(signature, "Unfamiliar activity was seen and is waiting for review")
    return f"{_SEVERITY_PREFIX.get(severity, 'Notice')}: {body}."


# ------------------------------------------------------------------ stats
@dataclass
class PipelineStats:
    lines: int = 0
    parsed: int = 0
    dropped: int = 0
    events: int = 0
    tts_retries: int = 0
    tts_fallbacks: int = 0
    actions_rejected: int = 0
    escalations: dict[str, int] = field(default_factory=dict)

    def as_text(self) -> str:
        esc = ", ".join(f"{k}={v}" for k, v in sorted(self.escalations.items())) or "none"
        return (
            f"lines={self.lines} parsed={self.parsed} dropped={self.dropped} events={self.events} "
            f"tts_retries={self.tts_retries} tts_fallbacks={self.tts_fallbacks} "
            f"actions_rejected={self.actions_rejected} escalations[{esc}]"
        )


class LeakError(RuntimeError):
    """A real value was about to reach the model. Refuse rather than send."""


# --------------------------------------------------------------- pipeline
class Pipeline:
    def __init__(
        self,
        llm: LLM,
        window_sec: int = 30,
        year: int | None = None,
        rmap: RedactionMap | None = None,
    ) -> None:
        self.llm = llm
        self.parser = LineParser(year=year)
        self.aggregator = Aggregator(window_sec=window_sec)
        self.rmap = rmap or RedactionMap()
        self.stats = PipelineStats()

    # ------------------------------------------------------------ driving
    def run(self, lines: Iterable[str]) -> Iterator[EventData]:
        for line in lines:
            self.stats.lines += 1
            parsed = self.parser.parse(line)
            self.stats.parsed, self.stats.dropped = self.parser.parsed, self.parser.dropped
            if parsed is None:
                continue
            for agg in self.aggregator.add(parsed):
                yield self.process(agg)
        for agg in self.aggregator.flush():
            yield self.process(agg)

    # ------------------------------------------------------------ one event
    def process(self, agg: AggregatedEvent) -> EventData:
        meta = SIGNATURE_META.get(agg.signature, _UNKNOWN_META)
        mi = self._model_input(agg)
        out, tts = self._ask_model(mi, agg)

        internal_log = self.rmap.localize(self._neutralise_free_text(out.internal_log))

        action, rejection = self._action(out)
        if rejection:
            self.stats.actions_rejected += 1
        escalation = self._escalation(agg, out, rejection)
        if escalation.escalated:
            self.stats.escalations[escalation.gate or "?"] = self.stats.escalations.get(escalation.gate or "?", 0) + 1

        event = EventData(
            event_id=uuid7(),
            signature=agg.signature,
            severity=out.severity,
            title=meta.title,
            internal_log=internal_log,
            tts_summary=tts,
            confidence=out.confidence,
            entities=self._entities(agg),
            source=Source(kind=meta.kind, collector=meta.collector, raw_count=agg.raw_count),
            window=Window(start=agg.window_start, end=agg.window_end),
            escalation=escalation,
            action=action,
        )
        self.stats.events += 1
        return event

    # ----------------------------------------------------------- redaction
    def _model_input(self, agg: AggregatedEvent) -> ModelInput:
        r = self.rmap
        src_tokens = [r.register_host(ip) for ip in sorted(agg.source_ips)]
        dst_tokens = [r.register_host(h) for h in sorted(agg.dst_hosts)]
        user_tokens = [r.register_user(u) for u in sorted(agg.users)]
        sample_lines = [r.redact(raw) for raw in agg.raw_samples]
        mi = ModelInput(
            signature=agg.signature,
            raw_count=agg.raw_count,
            window_start=format_ts(agg.window_start),
            window_end=format_ts(agg.window_end),
            src_tokens=src_tokens,
            dst_tokens=dst_tokens,
            user_tokens=user_tokens,
            ports=sorted(agg.ports),
            sample_lines=sample_lines,
            correlated=agg.correlated,
            window_source_count=agg.window_source_count,
        )
        self._assert_no_leak(mi, agg)
        return mi

    def _assert_no_leak(self, mi: ModelInput, agg: AggregatedEvent) -> None:
        blob = "\n".join([*mi.src_tokens, *mi.dst_tokens, *mi.user_tokens, *mi.sample_lines])
        for value in (*agg.source_ips, *agg.dst_hosts, *agg.users):
            if value and re.search(r"(?<![\w.-])" + re.escape(value) + r"(?![\w.-])", blob):
                raise LeakError(f"unredacted value would reach the model: {value!r}")
        for tok in (*mi.src_tokens, *mi.dst_tokens, *mi.user_tokens):
            if not TOKEN_RE.fullmatch(tok):
                raise LeakError(f"entity is not a token: {tok!r}")

    def _neutralise_free_text(self, text: str) -> str:
        """Swap tokens whose real value is not identifier-shaped for a marker."""
        def repl(m: re.Match[str]) -> str:
            value = self.rmap.value_for(m.group(0))
            if value is None or IDENTIFIER_RE.match(value):
                return m.group(0)
            return FREE_TEXT_MARKER
        return TOKEN_RE.sub(repl, text)

    # --------------------------------------------------------------- model
    def _ask_model(self, mi: ModelInput, agg: AggregatedEvent) -> tuple[OllamaOutput, str]:
        tokens = self.rmap.tokens()
        out = self.llm.generate(mi)
        problems = validate_tts(out.tts_summary, tokens)
        if not problems:
            return out, out.tts_summary
        self.stats.tts_retries += 1
        retry = self.llm.generate(mi)
        if not validate_tts(retry.tts_summary, tokens):
            return retry, retry.tts_summary
        self.stats.tts_fallbacks += 1
        return retry, fallback_tts(agg.signature, retry.severity)

    # -------------------------------------------------------------- action
    def _resolve_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return {k: self.rmap.localize(v) if isinstance(v, str) else v for k, v in params.items()}

    def _action(self, out: OllamaOutput) -> tuple[ActionRef | None, str | None]:
        resolved = self._resolve_params(dict(out.action_params))
        allowed, needs_approval, reason = validate_and_classify(resolved, out.action)
        if not allowed:
            return None, reason
        resolved.setdefault("playbook", out.action)
        params = PlaybookParamsAdapter.validate_python(resolved)
        if needs_approval:
            now = now_utc()
            return ActionRef(
                playbook=out.action, params=params, status="pending_approval", requires_approval=True,
                approval_id=f"apr_{uuid7().hex[:12]}", expires_at=now + timedelta(seconds=APPROVAL_TTL_SEC),
            ), None
        return ActionRef(playbook=out.action, params=params, status="auto_executed", requires_approval=False), None

    # ----------------------------------------------------------- escalation
    def _escalation(self, agg: AggregatedEvent, out: OllamaOutput, rejection: str | None) -> Escalation:
        gate: str | None = None
        reason: str | None = None
        if agg.signature not in SIGNATURE_META:
            gate, reason = "unknown_pattern", f"signature {agg.signature} not in catalog"
        elif agg.correlated:
            gate, reason = "correlation", f"{agg.window_source_count} distinct sources in one window"
        elif rejection:
            gate, reason = "blast_radius", rejection
        elif out.severity == "critical":
            gate, reason = "severity_floor", "model returned critical"
        elif out.confidence < CONFIDENCE_FLOOR:
            gate, reason = "low_confidence", f"confidence {out.confidence:.2f} below {CONFIDENCE_FLOOR}"
        elif out.escalate:
            gate, reason = "model_self_report", out.escalate_reason
        if gate is None:
            return Escalation(escalated=False)
        return Escalation(escalated=True, gate=gate, reason=(reason or gate)[:300], state="pending")

    # ------------------------------------------------------------- entities
    @staticmethod
    def _entities(agg: AggregatedEvent) -> Entities:
        return Entities(
            src_ips=sorted(agg.source_ips),
            dst_hosts=sorted(h for h in agg.dst_hosts if _HOST_ID_RE.match(h)),
            users=sorted(u for u in agg.users if _USER_ID_RE.match(u)),
            ports=sorted(agg.ports),
        )


# -------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sentinel Phase 1 ingestion pipeline")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--fixture", nargs="?", const=str(DEFAULT_FIXTURE),
                     help=f"replay a static log file (default: {DEFAULT_FIXTURE.relative_to(HERE)})")
    src.add_argument("--tail", help="follow a live log file")
    ap.add_argument("--rate", type=float, default=0.0, help="fixture replay rate, lines/sec (0 = max)")
    ap.add_argument("--window", type=int, default=30, help="aggregation window, 30..60 seconds")
    ap.add_argument("--year", type=int, default=None, help="year for syslog timestamps (default: now)")
    ap.add_argument("--fake-llm", action="store_true", help="canned model output, no network")
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen2.5-coder:14b")
    ap.add_argument("--keep-alive", default="30m")
    ap.add_argument("--pretty", action="store_true", help="indent the JSON output")
    args = ap.parse_args(argv)

    llm = OllamaClient(base_url=args.ollama_url, model=args.model, keep_alive=args.keep_alive, fake=args.fake_llm)
    pipe = Pipeline(llm=llm, window_sec=args.window, year=args.year)
    if args.fixture:
        lines: Iterable[str] = replay_fixture(args.fixture, rate_lps=args.rate)
    else:
        lines = tail_file(args.tail, stop=threading.Event())

    try:
        for ev in pipe.run(lines):
            sys.stdout.write(ev.model_dump_json(indent=2 if args.pretty else None) + "\n")
            sys.stdout.flush()
    except KeyboardInterrupt:
        pass
    sys.stderr.write(pipe.stats.as_text() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
