from datetime import datetime, timedelta, timezone

import pytest

from db import Database
from gates import (
    QUEUED_DIGEST,
    GateInput,
    RateController,
    combine_gates,
    gate_blast_radius,
    gate_correlation,
    gate_low_confidence,
    gate_model_self_report,
    gate_novelty,
    gate_schema_failure,
    gate_severity_floor,
    gate_unknown_pattern,
    is_cap_reason,
)
from schemas import EventData, OllamaOutput, uuid7
from schemas import Entities, Escalation, Source, Window


def out(**kw) -> OllamaOutput:
    base = dict(
        internal_log="HOST_A did a thing to HOST_B.", tts_summary="An outside address did a thing.",
        severity="high", confidence=0.9, escalate=False, escalate_reason=None,
        action="watch", action_params={"target": "HOST_A", "duration_sec": 3600},
    )
    base.update(kw)
    return OllamaOutput.model_validate(base)


def gi(**kw) -> GateInput:
    base = dict(signature="ssh.auth.brute_force", correlated=False, window_source_count=1, seen_before=True)
    base.update(kw)
    return GateInput(**base)


# ------------------------------------------------- each gate in isolation
def test_unknown_pattern_fires_for_uncatalogued_signature():
    assert gate_unknown_pattern(gi(signature="zz.mystery"), out())[0] == "unknown_pattern"
    assert gate_unknown_pattern(gi(), out()) is None


def test_novelty_fires_when_signature_never_seen():
    assert gate_novelty(gi(seen_before=False), out())[0] == "novelty"
    assert gate_novelty(gi(seen_before=True), out()) is None


def test_correlation_fires_on_correlated_window():
    hit = gate_correlation(gi(correlated=True, window_source_count=3), out())
    assert hit[0] == "correlation" and "3 distinct sources" in hit[1]
    assert gate_correlation(gi(), out()) is None


def test_schema_failure_fires_on_llm_error_or_missing_output():
    assert gate_schema_failure(gi(llm_error="SchemaFailure: bad json"), None)[0] == "schema_failure"
    assert gate_schema_failure(gi(), None)[0] == "schema_failure"
    assert gate_schema_failure(gi(), out()) is None


def test_blast_radius_fires_on_rejection_and_on_cap_reason_only():
    assert gate_blast_radius(gi(param_rejection="block_ip: prefix /8 broader than allowed floor /24"), out())[0] == "blast_radius"
    assert gate_blast_radius(gi(cap_reason="block_ip: 10.0.0.5 is not a public address; approval required"), out())[0] == "blast_radius"
    assert gate_blast_radius(gi(), out()) is None
    # a playbook that always needs approval is NOT a blast radius finding
    assert is_cap_reason(True, "isolate_host: always requires approval") is None
    assert is_cap_reason(True, "block_ip: prefix /24 is not a single host; approval required") is not None
    assert is_cap_reason(False, "block_ip: single public host for 3600s within auto cap") is None


def test_severity_floor_fires_on_critical():
    assert gate_severity_floor(gi(), out(severity="critical"))[0] == "severity_floor"
    assert gate_severity_floor(gi(), out(severity="high")) is None


def test_low_confidence_fires_below_0_70():
    assert gate_low_confidence(gi(), out(confidence=0.69))[0] == "low_confidence"
    assert gate_low_confidence(gi(), out(confidence=0.70)) is None


def test_model_self_report_fires_when_model_asks():
    hit = gate_model_self_report(gi(), out(escalate=True, escalate_reason="no playbook fits"))
    assert hit == ("model_self_report", "no playbook fits")
    assert gate_model_self_report(gi(), out()) is None


# ---------------------------------------------------------- combination
def test_combine_gates_respects_order_and_returns_first_hit():
    d = combine_gates(gi(signature="zz.new", seen_before=False, correlated=True), out(severity="critical", confidence=0.1))
    assert d.escalate and d.gate == "unknown_pattern"
    d = combine_gates(gi(seen_before=False, correlated=True), out(severity="critical"))
    assert d.gate == "novelty"
    d = combine_gates(gi(correlated=True), out(severity="critical"))
    assert d.gate == "correlation"
    d = combine_gates(gi(cap_reason="cap"), out(severity="critical"))
    assert d.gate == "blast_radius"
    d = combine_gates(gi(), out(severity="critical", confidence=0.1))
    assert d.gate == "severity_floor"
    d = combine_gates(gi(), out(confidence=0.1, escalate=True, escalate_reason="x"))
    assert d.gate == "low_confidence"
    d = combine_gates(gi(), out(escalate=True, escalate_reason="x"))
    assert d.gate == "model_self_report"


def test_combine_gates_quiet_on_a_clean_known_event():
    d = combine_gates(gi(), out())
    assert d.escalate is False and d.gate is None and d.reason is None


def test_every_gate_name_is_a_valid_contract_value():
    from schemas import EscalationGate
    for g in ("unknown_pattern", "novelty", "correlation", "schema_failure", "blast_radius",
              "severity_floor", "low_confidence", "model_self_report"):
        assert g in EscalationGate.__args__


# ---------------------------------------------------------- rate control
def _event(sig: str, state: str, db: Database, created_at: datetime) -> None:
    ev = EventData(
        event_id=uuid7(), signature=sig, severity="high", title="t", internal_log="log", tts_summary="Something happened.",
        confidence=0.9, entities=Entities(), source=Source(kind="auth", collector="sshd", raw_count=1),
        window=Window(start=created_at, end=created_at),
        escalation=Escalation(escalated=True, gate="novelty", reason="r", state="pending"),
    )
    db.insert_event(ev, escalation_state=state)
    # backdate created_at so the injected clock can move past it
    from schemas import format_ts
    db._conn.execute("UPDATE events SET created_at = ? WHERE event_id = ?", (format_ts(created_at), str(ev.event_id)))


class Clock:
    def __init__(self, t0: datetime) -> None:
        self.t = t0

    def __call__(self) -> float:
        return self.t.timestamp()

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


T0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def test_cooldown_one_escalation_per_signature_per_hour():
    db, clock = Database(), Clock(T0)
    rc = RateController(db, clock=clock)
    assert rc.admit("ssh.auth.brute_force") == ("pending", None)
    _event("ssh.auth.brute_force", "pending", db, clock.t)
    state, why = rc.admit("ssh.auth.brute_force")
    assert state == QUEUED_DIGEST and "within the last hour" in why
    assert rc.admit("net.portscan") == ("pending", None)   # other signatures unaffected
    clock.advance(minutes=59)
    assert rc.admit("ssh.auth.brute_force")[0] == QUEUED_DIGEST
    clock.advance(minutes=2)
    assert rc.admit("ssh.auth.brute_force") == ("pending", None)
    assert rc.queued == 2


def test_global_ceiling_20_per_hour_then_queued_digest():
    db, clock = Database(), Clock(T0)
    rc = RateController(db, clock=clock)
    for i in range(20):
        _event(f"sig.{i}", "pending", db, clock.t)
    state, why = rc.admit("sig.fresh")
    assert state == QUEUED_DIGEST and "global ceiling" in why
    clock.advance(hours=1, seconds=1)
    assert rc.admit("sig.fresh") == ("pending", None)


def test_queued_digest_rows_do_not_count_toward_the_ceiling():
    db, clock = Database(), Clock(T0)
    rc = RateController(db, clock=clock)
    for i in range(25):
        _event(f"sig.{i}", QUEUED_DIGEST, db, clock.t)
    assert rc.admit("sig.other") == ("pending", None)
    assert db.escalations_since_global(clock.t - timedelta(hours=1)) == 0


def test_unreachable_and_answered_count_as_consulted():
    db, clock = Database(), Clock(T0)
    _event("a", "answered", db, clock.t)
    _event("a", "unreachable", db, clock.t)
    assert db.escalations_since("a", clock.t - timedelta(hours=1)) == 2
