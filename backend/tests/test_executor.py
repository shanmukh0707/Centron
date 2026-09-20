import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import executor as executor_mod
from assets import AssetRegistry, ProtectedListEmpty
from db import Database
from executor import Executor
from schemas import requires_approval, validate_and_classify

T0 = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> float:
        return self.t.timestamp()

    def advance(self, **kw) -> None:
        self.t += timedelta(**kw)


def make(protected=(), dry_run=True, **kw):
    db = Database()
    reg = AssetRegistry.of(protected)
    clock = Clock()
    return Executor(db, reg, dry_run=dry_run, clock=clock, **kw), db, clock


# ------------------------------------------------------- protected first
def test_protected_asset_is_refused_before_caps_are_consulted(monkeypatch):
    ex, db, _ = make(protected={"185.220.101.9"})
    called = []
    monkeypatch.setattr(executor_mod, "validate_and_classify", lambda *a, **k: called.append(1) or (True, False, "x"))
    res = ex.execute("block_ip", {"target": "185.220.101.9", "duration_sec": 60})
    assert res.status == "denied" and "protected asset" in res.reason
    assert called == [], "caps were consulted for a protected target"
    row = next(db.iter_actions())
    assert row["status"] == "denied"


def test_protected_cidr_and_host_id_are_refused():
    ex, _, _ = make(protected={"10.0.0.0/24", "nas01"})
    assert ex.execute("watch", {"target": "10.0.0.77", "duration_sec": 60}).status == "denied"
    assert ex.execute("isolate_host", {"host_id": "NAS01", "duration_sec": 60}).status == "denied"
    assert ex.execute("snapshot_evidence", {"scope": "host", "target": "nas01", "window_sec": 60}).status == "denied"


def test_local_floor_loopback_always_protected():
    ex, _, _ = make()
    assert ex.execute("block_ip", {"target": "127.0.0.1", "duration_sec": 60}).status == "denied"


# ------------------------------------------------------------ dry run
def test_dry_run_is_default_and_never_touches_a_syscall(monkeypatch, caplog):
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("syscall")))
    monkeypatch.setattr(os, "system", lambda *a, **k: (_ for _ in ()).throw(AssertionError("syscall")))
    ex, _, _ = make()
    assert ex.dry_run is True
    with caplog.at_level("INFO", logger="sentinel.executor"):
        res = ex.execute("block_ip", {"target": "185.220.101.9", "duration_sec": 600})
    assert res.status == "auto_executed"
    assert "[DRY RUN] would execute block_ip" in caplog.text
    assert res.expires_at == T0 + timedelta(seconds=600)


# --------------------------------------------------- status == classify
@pytest.mark.parametrize("playbook,params", [
    ("block_ip", {"target": "185.220.101.9", "duration_sec": 600}),          # auto
    ("block_ip", {"target": "185.220.101.9", "duration_sec": 7200}),         # over auto cap
    ("block_ip", {"target": "192.168.1.9", "duration_sec": 600}),          # RFC1918 never auto
    ("block_ip", {"target": "185.220.101.0/24", "duration_sec": 600}),      # breadth
    ("rate_limit", {"target": "185.220.101.9", "limit_conn_per_min": 20, "duration_sec": 600}),
    ("rate_limit", {"target": "192.168.1.9", "limit_conn_per_min": 20, "duration_sec": 600}),
    ("isolate_host", {"host_id": "ws-07", "duration_sec": 600}),
    ("revoke_session", {"user": "j.doe"}),
    ("watch", {"target": "185.220.101.9", "duration_sec": 600}),
    ("notify_only", {"channel": "app"}),
    ("snapshot_evidence", {"scope": "auth", "target": "bastion", "window_sec": 600}),
])
def test_status_always_matches_classify_truth(playbook, params):
    ex, db, _ = make()
    allowed, needs, _ = validate_and_classify(params, playbook)
    assert allowed
    res = ex.execute(playbook, params)
    if needs:
        assert res.status == "pending_approval" and res.approval_id and res.expires_at
        assert res.requires_approval is True
    else:
        assert res.status == "auto_executed" and res.approval_id is None
        assert res.requires_approval is False
    assert requires_approval({**params, "playbook": playbook}) == needs


def test_invalid_params_are_denied_not_guessed():
    ex, _, _ = make()
    res = ex.execute("block_ip", {"target": "185.0.0.0/8", "duration_sec": 600})
    assert res.status == "denied" and "broader" in res.reason


# ---------------------------------------------------- circuit breaker
def test_breaker_trips_at_more_than_5_in_10_minutes_and_degrades():
    ex, _, clock = make()
    statuses = []
    for i in range(7):
        statuses.append(ex.execute("block_ip", {"target": f"185.220.101.{i + 1}", "duration_sec": 60}).status)
        clock.advance(seconds=30)
    assert statuses[:5] == ["auto_executed"] * 5
    assert statuses[5:] == ["pending_approval"] * 2
    assert ex.breaker_tripped is True
    assert ex.breaker_state()["auto_last_10m"] == 5


def test_breaker_stays_tripped_until_reset_even_after_window_passes():
    ex, _, clock = make()
    for i in range(6):
        ex.execute("block_ip", {"target": f"185.220.101.{i + 1}", "duration_sec": 60})
    clock.advance(hours=2)
    assert ex.execute("block_ip", {"target": "185.220.101.99", "duration_sec": 60}).status == "pending_approval"
    ex.reset()
    assert ex.breaker_tripped is False
    assert ex.execute("block_ip", {"target": "185.220.101.99", "duration_sec": 60}).status == "auto_executed"


def test_breaker_does_not_count_or_degrade_read_only_playbooks():
    ex, _, _ = make()
    for i in range(20):
        assert ex.execute("watch", {"target": f"185.220.101.{i + 1}", "duration_sec": 60}).status == "auto_executed"
    assert ex.breaker_tripped is False
    for i in range(6):
        ex.execute("block_ip", {"target": f"185.220.101.{i + 1}", "duration_sec": 60})
    assert ex.breaker_tripped
    assert ex.execute("notify_only", {"channel": "app"}).status == "auto_executed"


def test_auto_budget_10_per_hour():
    ex, _, clock = make(breaker_threshold=100)
    for i in range(10):
        assert ex.execute("rate_limit", {"target": f"185.220.101.{i + 1}", "limit_conn_per_min": 20,
                                          "duration_sec": 60}).status == "auto_executed"
        clock.advance(minutes=3)
    res = ex.execute("rate_limit", {"target": "185.220.101.200", "limit_conn_per_min": 20, "duration_sec": 60})
    assert res.status == "pending_approval" and "budget" in res.reason
    clock.advance(minutes=35)  # first one is now > 1h old
    assert ex.execute("rate_limit", {"target": "185.220.101.201", "limit_conn_per_min": 20,
                                      "duration_sec": 60}).status == "auto_executed"


# ------------------------------------------------------------- approvals
def test_approve_runs_recorded_action_and_reports_real_outcome():
    ex, db, clock = make()
    res = ex.execute("isolate_host", {"host_id": "ws-07", "duration_sec": 600})
    assert res.status == "pending_approval"
    ok = ex.approve(res.approval_id)
    assert ok.status == "approved" and ok.expires_at == clock.t + timedelta(seconds=600)
    assert db.get_action(res.approval_id)["status"] == "approved"
    assert ex.approve(res.approval_id).status == "failed"  # cannot approve twice


def test_late_approval_is_expired():
    ex, _, clock = make(approval_ttl_sec=60)
    res = ex.execute("isolate_host", {"host_id": "ws-07", "duration_sec": 600})
    clock.advance(seconds=61)
    assert ex.approve(res.approval_id).status == "expired"


def test_deny_and_unknown():
    ex, db, _ = make()
    res = ex.execute("revoke_session", {"user": "j.doe"})
    assert ex.deny(res.approval_id).status == "denied"
    assert ex.approve("apr_nope").status == "failed"


# ------------------------------------------------------------- live mode
def test_live_mode_refuses_to_start_with_unconfigured_protected_list():
    with pytest.raises(ProtectedListEmpty) as ei:
        Executor(Database(), AssetRegistry.of(()), dry_run=False)
    assert "protected_assets.yaml" in str(ei.value)


def test_live_enforcement_reports_failed_not_optimistic():
    ex, _, _ = make(protected={"192.168.1.1"}, dry_run=False)
    res = ex.execute("block_ip", {"target": "185.220.101.9", "duration_sec": 60})
    assert res.status == "failed" and "nft add element" in res.reason and "timeout 60s" in res.reason
    assert ex.breaker_state()["auto_last_10m"] == 0  # a failure is not an execution


def test_live_read_only_playbooks_really_run():
    ex, _, _ = make(protected={"192.168.1.1"}, dry_run=False)
    assert ex.execute("watch", {"target": "185.220.101.9", "duration_sec": 60}).status == "auto_executed"
    assert "185.220.101.9" in ex.watchlist
    assert ex.execute("snapshot_evidence", {"scope": "auth", "target": "bastion", "window_sec": 60}).status == "auto_executed"
    assert ex.snapshots and ex.snapshots[0]["target"] == "bastion"


# ------------------------------------------------------------ expiring
def test_list_expiring_includes_auto_rules_and_pending_approvals():
    ex, _, clock = make(approval_ttl_sec=120)
    ex.execute("block_ip", {"target": "185.220.101.9", "duration_sec": 60})
    ex.execute("isolate_host", {"host_id": "ws-07", "duration_sec": 600})
    assert ex.list_expiring() == []
    clock.advance(seconds=61)
    assert [r["playbook"] for r in ex.list_expiring()] == ["block_ip"]
    assert [r["playbook"] for r in ex.list_expiring(within=timedelta(seconds=120))] == ["block_ip", "isolate_host"]


def test_caps_are_not_loosened_for_rfc1918():
    ex, _, _ = make()
    for target in ("10.1.2.3", "172.16.5.5", "192.168.0.7"):
        assert ex.execute("block_ip", {"target": target, "duration_sec": 60}).status == "pending_approval"
