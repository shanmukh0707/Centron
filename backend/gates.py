"""Deterministic escalation gates and db-backed rate control.

Gates run in a fixed order and the first one that fires wins. They are
evaluated in Python from parser facts, DB history and the executor's cap
decision; the model's own opinion is consulted last. None of this consults
Claude. Deciding *whether* to consult Claude is the whole job.

Order:
  1. unknown_pattern   signature not in the 7-playbook catalog (parsers.SIGNATURE_META)
  2. novelty           db.signature_seen_before() is False
  3. correlation       aggregator flagged 3+ distinct sources in one window
  4. schema_failure    llm raised SchemaFailure or OllamaUnreachable
  5. blast_radius      validate_and_classify() rejected the params, or demanded
                       approval for a cap reason (breadth, RFC1918, duration)
                       rather than the playbook's normal approval requirement
  6. severity_floor    the model returned "critical"
Then the self-report gates:
  7. low_confidence    confidence < CONFIDENCE_FLOOR
  8. model_self_report the model set escalate=true

Rate control lives beside the gates, not inside them: a gate says "this
deserves a second opinion", the RateController says whether we can afford
one right now. Past the cooldown or the global ceiling the event is still
escalated on the wire (state pending, reviewed false) but recorded in the
DB as queued_digest and Claude is not called.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from db import Database
from parsers import SIGNATURE_META
from schemas import OllamaOutput

CONFIDENCE_FLOOR = 0.70
PER_SIGNATURE_PER_HOUR = 1
GLOBAL_PER_HOUR = 20
COOLDOWN = timedelta(hours=1)

# DB-only state: escalated, Claude deliberately not called. On the wire this
# is state "pending" because EscalationState is a locked contract enum.
QUEUED_DIGEST = "queued_digest"


@dataclass(frozen=True)
class GateInput:
    """Facts the gates need, gathered before EventData exists."""

    signature: str
    correlated: bool = False
    window_source_count: int = 1
    seen_before: bool = True
    llm_error: str | None = None          # SchemaFailure / OllamaUnreachable message
    param_rejection: str | None = None    # validate_and_classify allowed=False reason
    cap_reason: str | None = None         # requires_approval for a cap reason (not "always")


@dataclass(frozen=True)
class EscalationDecision:
    escalate: bool
    gate: str | None = None
    reason: str | None = None

    @classmethod
    def no(cls) -> "EscalationDecision":
        return cls(escalate=False)

    @classmethod
    def yes(cls, gate: str, reason: str | None) -> "EscalationDecision":
        return cls(escalate=True, gate=gate, reason=(reason or gate)[:300])


# ------------------------------------------------------------ the gates
Gate = Callable[[GateInput, OllamaOutput | None], tuple[str, str] | None]


def gate_unknown_pattern(gi: GateInput, out: OllamaOutput | None) -> tuple[str, str] | None:
    if gi.signature not in SIGNATURE_META:
        return "unknown_pattern", f"signature {gi.signature} not in catalog"
    return None


def gate_novelty(gi: GateInput, out: OllamaOutput | None) -> tuple[str, str] | None:
    if not gi.seen_before:
        return "novelty", f"first occurrence of {gi.signature} on this network"
    return None


def gate_correlation(gi: GateInput, out: OllamaOutput | None) -> tuple[str, str] | None:
    if gi.correlated:
        return "correlation", f"{gi.window_source_count} distinct sources in one window"
    return None


def gate_schema_failure(gi: GateInput, out: OllamaOutput | None) -> tuple[str, str] | None:
    if gi.llm_error is not None or out is None:
        return "schema_failure", gi.llm_error or "local model produced no usable output"
    return None


def gate_blast_radius(gi: GateInput, out: OllamaOutput | None) -> tuple[str, str] | None:
    if gi.param_rejection:
        return "blast_radius", gi.param_rejection
    if gi.cap_reason:
        return "blast_radius", gi.cap_reason
    return None


def gate_severity_floor(gi: GateInput, out: OllamaOutput | None) -> tuple[str, str] | None:
    if out is not None and out.severity == "critical":
        return "severity_floor", "model returned critical"
    return None


def gate_low_confidence(gi: GateInput, out: OllamaOutput | None) -> tuple[str, str] | None:
    if out is not None and out.confidence < CONFIDENCE_FLOOR:
        return "low_confidence", f"confidence {out.confidence:.2f} below {CONFIDENCE_FLOOR:.2f}"
    return None


def gate_model_self_report(gi: GateInput, out: OllamaOutput | None) -> tuple[str, str] | None:
    if out is not None and out.escalate:
        return "model_self_report", out.escalate_reason or "model requested escalation"
    return None


GATES: list[Gate] = [
    gate_unknown_pattern,
    gate_novelty,
    gate_correlation,
    gate_schema_failure,
    gate_blast_radius,
    gate_severity_floor,
    gate_low_confidence,
    gate_model_self_report,
]


def combine_gates(event: GateInput, ollama_output: OllamaOutput | None) -> EscalationDecision:
    for gate in GATES:
        hit = gate(event, ollama_output)
        if hit is not None:
            return EscalationDecision.yes(*hit)
    return EscalationDecision.no()


def is_cap_reason(requires_approval: bool, reason: str) -> str | None:
    """Split validate_and_classify's approval verdict into 'normal' vs 'cap'.

    isolate_host / revoke_session always need approval; that is the playbook's
    nature, not a blast-radius finding. block_ip on a /24 or an RFC1918 host,
    or a duration past the auto cap, is.
    """
    if not requires_approval:
        return None
    if "always requires approval" in reason:
        return None
    return reason


# --------------------------------------------------------- rate control
class RateController:
    """1 escalation per signature per hour, 20 per hour globally, both from the DB.

    `clock` returns epoch seconds and is injectable for tests.
    """

    def __init__(
        self,
        db: Database,
        clock: Callable[[], float] = time.time,
        per_signature_per_hour: int = PER_SIGNATURE_PER_HOUR,
        global_per_hour: int = GLOBAL_PER_HOUR,
    ) -> None:
        self.db = db
        self.clock = clock
        self.per_signature = per_signature_per_hour
        self.global_ceiling = global_per_hour
        self.queued = 0

    def _since(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc) - COOLDOWN

    def admit(self, signature: str) -> tuple[str, str | None]:
        """('pending', None) if Claude may be called now, else ('queued_digest', why)."""
        since = self._since()
        if self.db.escalations_since_global(since) >= self.global_ceiling:
            self.queued += 1
            return QUEUED_DIGEST, f"global ceiling {self.global_ceiling}/h reached"
        if self.db.escalations_since(signature, since) >= self.per_signature:
            self.queued += 1
            return QUEUED_DIGEST, f"{signature} already escalated within the last hour"
        return "pending", None
