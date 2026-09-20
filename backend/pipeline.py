"""Sentinel ingestion pipeline (Phase 1 + Phase 2).

    log_source | ingest -> parsers -> aggregator -> redaction -> llm
        -> gates -> executor -> EventData -> db -> [claude_escalation, async]

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
  * action_params tokens are resolved locally, checked with
    schemas.validate_and_classify, then handed to executor.execute(). The
    ActionRef records the executor's REAL outcome.
  * Escalation is decided by gates.combine_gates(); rate control by
    gates.RateController. Claude is consulted fire-and-forget AFTER the event
    has been yielded to the caller; the verdict re-emits through on_update.
  * On SchemaFailure / OllamaUnreachable a deterministic fallback event is
    built (notify_only, escalated on gate schema_failure). Never dropped.
  * Every emitted event, fallbacks included, is written to SQLite first.

CLI (run from backend/ or anywhere; the fixture default is anchored to this file):
    python pipeline.py --fixture --fake-llm --window 30
    python pipeline.py --tail /var/log/auth.log --fake-llm
    python pipeline.py --syslog 5514 --live-ollama --db sentinel.db
    python pipeline.py --fixture --fake-llm --live          # refuses until protected_assets.yaml is filled
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import re
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Protocol

from aggregator import AggregatedEvent, Aggregator
from assets import AssetRegistry
from claude_escalation import ClaudeEscalator, build_payload
from db import Database
from executor import Executor
from gates import QUEUED_DIGEST, EscalationDecision, GateInput, RateController, combine_gates, is_cap_reason
from ingest import DEFAULT_PORT as DEFAULT_SYSLOG_PORT
from ingest import SyslogListener, TaggedLine, load_source_map
from llm import ModelInput, OllamaClient, OllamaUnreachable, SchemaFailure
from log_source import replay_fixture, tail_file
from parsers import SIGNATURE_META, LineParser, SignatureMeta
from redaction import IDENTIFIER_RE, TOKEN_RE, LeakError, RedactionMap
from schemas import (
    ActionRef,
    Entities,
    Escalation,
    EventData,
    NotifyOnlyParams,
    OllamaOutput,
    PipelineStatus,
    PlaybookParamsAdapter,
    Source,
    Window,
    format_ts,
    now_utc,
    uuid7,
    validate_and_classify,
    validate_tts,
)

log = logging.getLogger("sentinel.pipeline")

HERE = Path(__file__).resolve().parent
DEFAULT_FIXTURE = HERE / "fixtures" / "sample_logs.txt"

_USER_ID_RE = re.compile(r"^[A-Za-z0-9._@-]{1,128}$")
_HOST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
CONFIDENCE_FLOOR = 0.7
APPROVAL_TTL_SEC = 600
RECENT_EVENTS = 500
_UNKNOWN_META = SignatureMeta("unknown", "unknown", "Unrecognised activity")
FREE_TEXT_MARKER = "[malformed username]"

UpdateCallback = Callable[[EventData], None]


class LLM(Protocol):
    def generate(self, mi: ModelInput, tokens: set[str] | None = None) -> OllamaOutput: ...


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
# Severity for the deterministic fallback event when the model is unavailable.
_FALLBACK_SEVERITY: dict[str, str] = {
    "ssh.auth.brute_force": "high",
    "ssh.auth.success": "info",
    "net.portscan": "low",
    "dns.blocked": "info",
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
    llm_failures: int = 0
    escalations: dict[str, int] = field(default_factory=dict)
    escalations_queued: int = 0
    escalations_sent: int = 0
    reemits: int = 0

    def as_text(self) -> str:
        esc = ", ".join(f"{k}={v}" for k, v in sorted(self.escalations.items())) or "none"
        return (
            f"lines={self.lines} parsed={self.parsed} dropped={self.dropped} events={self.events} "
            f"tts_retries={self.tts_retries} tts_fallbacks={self.tts_fallbacks} "
            f"actions_rejected={self.actions_rejected} llm_failures={self.llm_failures} "
            f"escalations[{esc}] queued_digest={self.escalations_queued} claude_sent={self.escalations_sent} "
            f"reemits={self.reemits}"
        )


# --------------------------------------------------------------- pipeline
class Pipeline:
    def __init__(
        self,
        llm: LLM,
        window_sec: int = 30,
        year: int | None = None,
        rmap: RedactionMap | None = None,
        db: Database | None = None,
        assets: AssetRegistry | None = None,
        executor: Executor | None = None,
        escalator: ClaudeEscalator | None = None,
        rate: RateController | None = None,
        dry_run: bool = True,
        on_update: UpdateCallback | None = None,
        session_id: str | None = None,
    ) -> None:
        self.llm = llm
        self.parser = LineParser(year=year)
        self.aggregator = Aggregator(window_sec=window_sec)
        self.db = db or Database(":memory:")
        self.assets = assets or AssetRegistry.of(())
        self.executor = executor or Executor(self.db, self.assets, dry_run=dry_run)
        self.rate = rate or RateController(self.db)
        self.escalator = escalator
        if self.escalator is not None and self.escalator.on_verdict is None:
            self.escalator.on_verdict = self.on_verdict
        self.on_update = on_update
        self.session_id = session_id or f"s_{uuid7().hex[:12]}"
        self.stats = PipelineStats()
        # HOMELAB: tokens survive restarts, see db.py.
        self.rmap = rmap or RedactionMap()
        self.rmap.preload(self.db.load_redaction_map())
        self._persisted_tokens: set[str] = set(self.rmap.tokens())
        self._recent: OrderedDict[str, EventData] = OrderedDict()
        self._pending_claude: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.log_source_state = "ok"

    # ------------------------------------------------------------ driving
    def run(self, lines: Iterable[str | TaggedLine | None]) -> Iterator[EventData]:
        """Consume raw lines (or TaggedLines from ingest, or None idle ticks)."""
        for item in lines:
            if item is None:  # idle tick from a live source
                for agg in self.aggregator.flush_before(now_utc()):
                    yield from self._emit(agg)
                continue
            if isinstance(item, TaggedLine):
                line, kind = item.line, item.kind
            else:
                line, kind = item, None
            self.stats.lines += 1
            parsed = self.parser.parse(line, kind)
            self.stats.parsed, self.stats.dropped = self.parser.parsed, self.parser.dropped
            if parsed is None:
                continue
            for agg in self.aggregator.add(parsed):
                yield from self._emit(agg)
        for agg in self.aggregator.flush():
            yield from self._emit(agg)

    def _emit(self, agg: AggregatedEvent) -> Iterator[EventData]:
        event = self.process(agg)
        yield event
        # CRITICAL TIMING: only after the consumer has taken the event.
        self.after_emit(event)

    def after_emit(self, event: EventData) -> None:
        """Kick off the Claude escalation for an event that was just handed over."""
        payload = self._pending_claude.pop(str(event.event_id), None)
        if payload is None or self.escalator is None:
            return
        try:
            self.escalator.submit(str(event.event_id), payload, self.rmap)
            self.stats.escalations_sent += 1
        except LeakError:
            log.exception("refusing to escalate %s: payload would leak", event.event_id)
            self.db.mark_reviewed(str(event.event_id), None, "unreachable")

    # ------------------------------------------------------------ one event
    def process(self, agg: AggregatedEvent) -> EventData:
        meta = SIGNATURE_META.get(agg.signature, _UNKNOWN_META)
        seen_before = self.db.signature_seen_before(agg.signature)
        mi = self._model_input(agg, novel=not seen_before)
        event_id = uuid7()

        out: OllamaOutput | None = None
        tts = ""
        llm_error: str | None = None
        try:
            out, tts = self._ask_model(mi, agg)
        except (SchemaFailure, OllamaUnreachable) as e:
            llm_error = f"{type(e).__name__}: {e}"
            self.stats.llm_failures += 1
            log.warning("local model failed for %s: %s", agg.signature, llm_error)
        self._persist_tokens()

        if out is None:
            event, db_state = self._fallback_event(agg, meta, event_id, mi, llm_error or "no output", seen_before)
        else:
            event, db_state = self._model_event(agg, meta, event_id, mi, out, tts, seen_before)

        self.db.insert_event(event, escalation_state=db_state, correlated=agg.correlated)
        self._remember(event)
        self.stats.events += 1
        return event

    def _model_event(
        self, agg: AggregatedEvent, meta: SignatureMeta, event_id: Any, mi: ModelInput,
        out: OllamaOutput, tts: str, seen_before: bool,
    ) -> tuple[EventData, str]:
        internal_log = self.rmap.localize(self._neutralise_free_text(out.internal_log))
        action, rejection, cap_reason = self._action(out, event_id)
        if rejection:
            self.stats.actions_rejected += 1
        gi = GateInput(
            signature=agg.signature, correlated=agg.correlated, window_source_count=agg.window_source_count,
            seen_before=seen_before, llm_error=None, param_rejection=rejection, cap_reason=cap_reason,
        )
        decision = combine_gates(gi, out)
        escalation, db_state = self._escalation(decision, agg.signature)
        event = EventData(
            event_id=event_id,
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
        if db_state == "pending":
            self._pending_claude[str(event_id)] = build_payload(
                signature=agg.signature, src_tokens=mi.src_tokens, dst_tokens=mi.dst_tokens,
                user_tokens=mi.user_tokens, ports=mi.ports, raw_count=agg.raw_count,
                window_sec=mi.to_dict()["window_sec"], gate=decision.gate or "", reason=decision.reason,
                internal_log=out.internal_log, tts_summary=tts, severity=out.severity,
                confidence=out.confidence, action=out.action, action_params=dict(out.action_params),
            )
        return event, db_state

    def _fallback_event(
        self, agg: AggregatedEvent, meta: SignatureMeta, event_id: Any, mi: ModelInput,
        llm_error: str, seen_before: bool,
    ) -> tuple[EventData, str]:
        """Deterministic event when the local model is unavailable. Never dropped."""
        severity = _FALLBACK_SEVERITY.get(agg.signature, "low")
        tts = fallback_tts(agg.signature, severity)
        srcs = ", ".join(sorted(agg.source_ips)) or "unknown source"
        dsts = ", ".join(sorted(agg.dst_hosts)) or "unknown host"
        internal_log = (
            f"Local model unavailable ({llm_error[:120]}). {agg.raw_count} raw line(s) matching "
            f"{agg.signature} from {srcs} against {dsts} between {format_ts(agg.window_start)} and "
            f"{format_ts(agg.window_end)}. Deterministic fallback: notify only, escalated for review."
        )[:600]
        res = self.executor.execute("notify_only", {"channel": "app"}, event_id=event_id)
        action = ActionRef(
            playbook="notify_only", params=NotifyOnlyParams(channel="app"), status=res.status,
            requires_approval=res.requires_approval, approval_id=res.approval_id, expires_at=res.expires_at,
        )
        gi = GateInput(signature=agg.signature, correlated=agg.correlated,
                       window_source_count=agg.window_source_count, seen_before=seen_before, llm_error=llm_error)
        decision = combine_gates(gi, None)
        if decision.gate not in ("unknown_pattern", "novelty", "correlation"):
            decision = EscalationDecision.yes("schema_failure", llm_error)
        escalation, db_state = self._escalation(decision, agg.signature)
        event = EventData(
            event_id=event_id, signature=agg.signature, severity=severity, title=meta.title,
            internal_log=internal_log, tts_summary=tts, confidence=0.0,
            entities=self._entities(agg),
            source=Source(kind=meta.kind, collector=meta.collector, raw_count=agg.raw_count),
            window=Window(start=agg.window_start, end=agg.window_end),
            escalation=escalation, action=action,
        )
        if db_state == "pending":
            self._pending_claude[str(event_id)] = build_payload(
                signature=agg.signature, src_tokens=mi.src_tokens, dst_tokens=mi.dst_tokens,
                user_tokens=mi.user_tokens, ports=mi.ports, raw_count=agg.raw_count,
                window_sec=mi.to_dict()["window_sec"], gate=decision.gate or "schema_failure",
                reason=decision.reason, internal_log=f"local model unavailable: {llm_error[:200]}",
                tts_summary=tts, severity=severity, confidence=0.0, action="notify_only",
                action_params={"channel": "app"},
            )
        return event, db_state

    # ----------------------------------------------------------- redaction
    def _model_input(self, agg: AggregatedEvent, novel: bool = False) -> ModelInput:
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
            novel_signature=novel,
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

    def _persist_tokens(self) -> None:
        pairs = {t: v for t, v in self.rmap.pairs().items() if t not in self._persisted_tokens}
        if pairs:
            self.db.save_tokens(pairs, self.session_id)
            self._persisted_tokens.update(pairs)

    # --------------------------------------------------------------- model
    def _ask_model(self, mi: ModelInput, agg: AggregatedEvent) -> tuple[OllamaOutput, str]:
        tokens = self.rmap.tokens()
        out = self.llm.generate(mi, tokens)
        problems = validate_tts(out.tts_summary, tokens)
        if not problems:
            return out, out.tts_summary
        self.stats.tts_retries += 1
        retry = self.llm.generate(mi, tokens)
        if not validate_tts(retry.tts_summary, tokens):
            return retry, retry.tts_summary
        self.stats.tts_fallbacks += 1
        return retry, fallback_tts(agg.signature, retry.severity)

    # -------------------------------------------------------------- action
    def _resolve_params(self, params: dict[str, Any]) -> dict[str, Any]:
        return {k: self.rmap.localize(v) if isinstance(v, str) else v for k, v in params.items()}

    def _action(self, out: OllamaOutput, event_id: Any) -> tuple[ActionRef | None, str | None, str | None]:
        """(action_ref, rejection_reason, cap_reason). Runs the executor."""
        resolved = self._resolve_params(dict(out.action_params))
        resolved.pop("playbook", None)
        allowed, needs_approval, reason = validate_and_classify(resolved, out.action)
        if not allowed:
            return None, reason, None
        res = self.executor.execute(out.action, resolved, event_id=event_id)
        if res.status == "denied":
            return None, res.reason, None
        params = PlaybookParamsAdapter.validate_python({**resolved, "playbook": out.action})
        ref = ActionRef(
            playbook=out.action, params=params, status=res.status,
            requires_approval=res.requires_approval or needs_approval,
            approval_id=res.approval_id, expires_at=res.expires_at,
        )
        return ref, None, is_cap_reason(needs_approval, reason)

    # ----------------------------------------------------------- escalation
    def _escalation(self, decision: EscalationDecision, signature: str) -> tuple[Escalation, str]:
        """Wire Escalation plus the DB state (pending | queued_digest | none)."""
        if not decision.escalate:
            return Escalation(escalated=False), "none"
        self.stats.escalations[decision.gate or "?"] = self.stats.escalations.get(decision.gate or "?", 0) + 1
        state, why = self.rate.admit(signature)
        if state == QUEUED_DIGEST:
            self.stats.escalations_queued += 1
            log.info("escalation for %s queued for digest: %s", signature, why)
        # queued_digest is DB-only; the contract enum has no such value, so the
        # frame says pending (escalated, unreviewed) and Claude is simply not called.
        return Escalation(escalated=True, gate=decision.gate, reason=decision.reason, state="pending"), state

    def on_verdict(self, event_id: str, verdict: str | None, state: str) -> None:
        """ClaudeEscalator callback (worker thread). Re-emits via on_update."""
        with self._lock:
            base = self._recent.get(event_id)
        if base is None:
            log.warning("verdict for unknown/evicted event %s dropped", event_id)
            return
        esc = base.escalation.model_copy(update={"state": state, "verdict": verdict if state == "answered" else None})
        updated = base.model_copy(update={"escalation": esc, "reviewed": state == "answered"})
        EventData.model_validate(updated.model_dump(mode="json"))  # re-validate before it goes anywhere
        self._remember(updated)
        self.stats.reemits += 1
        if self.on_update is not None:
            self.on_update(updated)

    def _remember(self, event: EventData) -> None:
        with self._lock:
            self._recent[str(event.event_id)] = event
            while len(self._recent) > RECENT_EVENTS:
                self._recent.popitem(last=False)

    def recent(self, event_id: str) -> EventData | None:
        with self._lock:
            return self._recent.get(event_id)

    # -------------------------------------------------------------- status
    def status(self) -> PipelineStatus:
        return PipelineStatus(
            log_source=self.log_source_state,  # type: ignore[arg-type]
            ollama=getattr(self.llm, "status", "ok"),  # type: ignore[arg-type]
            tts="degraded",  # no TTS renderer in the backend yet; the server serves a silent clip
            claude=self.escalator.status if self.escalator else "unreachable",  # type: ignore[arg-type]
        )

    # ------------------------------------------------------------- entities
    @staticmethod
    def _entities(agg: AggregatedEvent) -> Entities:
        return Entities(
            src_ips=sorted(agg.source_ips),
            dst_hosts=sorted(h for h in agg.dst_hosts if _HOST_ID_RE.match(h)),
            users=sorted(u for u in agg.users if _USER_ID_RE.match(u)),
            ports=sorted(agg.ports),
        )


# ------------------------------------------------------------- line sources
def with_ticks(source: Iterable[str], tick_sec: float = 1.0, stop: threading.Event | None = None) -> Iterator[str | None]:
    """Run a blocking line source on a thread; yield None after `tick_sec` of silence."""
    q: queue.Queue[str | None] = queue.Queue(maxsize=QUEUE_MAX_LINES)
    done = object()

    def _pump() -> None:
        try:
            for line in source:
                q.put(line)
        finally:
            q.put(done)  # type: ignore[arg-type]

    threading.Thread(target=_pump, name="line-source", daemon=True).start()
    while stop is None or not stop.is_set():
        try:
            item = q.get(timeout=tick_sec)
        except queue.Empty:
            yield None
            continue
        if item is done:
            return
        yield item


QUEUE_MAX_LINES = 10_000


# -------------------------------------------------------------------- CLI
def add_pipeline_args(ap: argparse.ArgumentParser) -> None:
    """Shared by pipeline.py and stub_server.py --source pipeline."""
    src = ap.add_argument_group("event source")
    g = src.add_mutually_exclusive_group()
    g.add_argument("--fixture", nargs="?", const=str(DEFAULT_FIXTURE),
                   help=f"replay a static log file (default file: {DEFAULT_FIXTURE.relative_to(HERE)})")
    g.add_argument("--tail", help="follow a live log file")
    g.add_argument("--syslog", nargs="?", const=DEFAULT_SYSLOG_PORT, type=int, metavar="PORT",
                   help=f"listen for syslog on PORT (default {DEFAULT_SYSLOG_PORT}, UDP+TCP)")
    src.add_argument("--syslog-host", default="0.0.0.0")
    src.add_argument("--source-map", default=None, help="sender -> parser kind map (yaml/json)")
    src.add_argument("--syslog-allow-local", action="store_true",
                     help="DEV ONLY: accept syslog from this host's own addresses (disables the feedback-loop guard)")
    src.add_argument("--replay-rate", type=float, default=0.0, help="fixture replay rate, lines/sec (0 = max)")
    src.add_argument("--window", type=int, default=30, help="aggregation window, 30..60 seconds")
    src.add_argument("--year", type=int, default=None, help="year for syslog timestamps (default: now)")

    m = ap.add_argument_group("model")
    mg = m.add_mutually_exclusive_group()
    mg.add_argument("--fake-llm", action="store_true", help="canned model output, no network")
    mg.add_argument("--live-ollama", action="store_true",
                    help="real Ollama; base URL from OLLAMA_BASE_URL (default http://127.0.0.1:11434)")
    m.add_argument("--ollama-url", default=None, help="override OLLAMA_BASE_URL")
    m.add_argument("--model", default=None, help="override the model named in prompts/model-prompt.md")

    x = ap.add_argument_group("execution")
    xg = x.add_mutually_exclusive_group()
    xg.add_argument("--dry-run", action="store_true", default=True, help="log what would run (default)")
    xg.add_argument("--live", action="store_true", help="real execution; refuses to start if no protected assets are configured")
    x.add_argument("--db", default=":memory:", help="SQLite path (default: in-memory)")
    x.add_argument("--assets", default=None, help="protected assets yaml (default backend/protected_assets.yaml)")
    x.add_argument("--no-auto-assets", action="store_true", help="skip auto-detecting local addresses (tests)")


def build_from_args(
    args: argparse.Namespace, on_update: UpdateCallback | None = None,
) -> tuple[Pipeline, Iterable[Any], SyslogListener | None]:
    """Construct the pipeline and its line source from parsed CLI args."""
    if not (args.fixture or args.tail or args.syslog):
        raise SystemExit("one of --fixture / --tail / --syslog is required")
    if not (args.fake_llm or args.live_ollama):
        raise SystemExit("one of --fake-llm / --live-ollama is required")
    live = bool(args.live)
    base_url = args.ollama_url or os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    llm = OllamaClient(base_url=base_url, model=args.model, fake=args.fake_llm)
    db = Database(args.db)
    assets = AssetRegistry.load(args.assets, auto_detect=not args.no_auto_assets)
    executor = Executor(db, assets, dry_run=not live)  # raises ProtectedListEmpty for --live if unconfigured
    escalator = ClaudeEscalator(db)
    pipe = Pipeline(llm=llm, window_sec=args.window, year=args.year, db=db, assets=assets,
                    executor=executor, escalator=escalator, dry_run=not live, on_update=on_update)

    listener: SyslogListener | None = None
    if args.fixture:
        lines: Iterable[Any] = replay_fixture(args.fixture, rate_lps=args.replay_rate)
    elif args.tail:
        lines = with_ticks(tail_file(args.tail, stop=threading.Event()))
    else:
        listener = SyslogListener(host=args.syslog_host, port=args.syslog, source_map=load_source_map(args.source_map),
                                  exclude=set() if args.syslog_allow_local else None)
        if args.syslog_allow_local:
            log.warning("--syslog-allow-local: feedback-loop guard disabled, lines from this host are accepted")
        lines = listener.lines()
    return pipe, lines, listener


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sentinel ingestion pipeline")
    add_pipeline_args(ap)
    ap.add_argument("--pretty", action="store_true", help="indent the JSON output")
    ap.add_argument("--log-level", default="warning")
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        stream=sys.stderr)

    out_lock = threading.Lock()

    def write(ev: EventData) -> None:
        with out_lock:
            sys.stdout.write(ev.model_dump_json(indent=2 if args.pretty else None) + "\n")
            sys.stdout.flush()

    pipe, lines, listener = build_from_args(args, on_update=write)
    if listener is not None:
        listener.run_in_thread()
    try:
        for ev in pipe.run(lines):
            write(ev)
    except KeyboardInterrupt:
        pass
    if pipe.escalator is not None and args.fixture:
        pipe.escalator.drain()
    sys.stderr.write(pipe.stats.as_text() + "\n")
    if listener is not None:
        sys.stderr.write(listener.stats.as_text() + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
