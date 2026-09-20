from datetime import datetime, timedelta, timezone

import pytest

from aggregator import AggregatedEvent, Aggregator
from parsers import ParsedLine

T0 = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)


def ssh_fail(ip="185.220.101.34", user="root", offset=0, port=22, host="bastion"):
    return ParsedLine(
        ts=T0 + timedelta(seconds=offset), source_ip=ip, dst_host=host, user=user, port=port,
        action="failed_password", signature="ssh.auth.brute_force",
        raw=f"Failed password for {user} from {ip} port 5{offset:04d} ssh2",
    )


def test_window_size_is_bounded_30_to_60():
    Aggregator(window_sec=30)
    Aggregator(window_sec=60)
    with pytest.raises(ValueError):
        Aggregator(window_sec=29)
    with pytest.raises(ValueError):
        Aggregator(window_sec=61)


def test_47_identical_failures_collapse_to_one_event_with_raw_count_47():
    agg = Aggregator(window_sec=30)
    emitted = []
    for i in range(47):
        emitted += agg.add(ssh_fail(offset=i % 30))
    emitted += agg.flush()
    assert len(emitted) == 1
    ev = emitted[0]
    assert isinstance(ev, AggregatedEvent)
    assert ev.raw_count == 47
    assert ev.signature == "ssh.auth.brute_force"
    assert ev.source_ip == "185.220.101.34"
    assert ev.dst_host == "bastion"
    assert ev.window_start == T0
    assert ev.window_end == T0 + timedelta(seconds=30)
    assert ev.first_ts == T0
    assert ev.last_ts == T0 + timedelta(seconds=29)


def test_distinct_entity_sets_are_carried():
    agg = Aggregator(window_sec=30)
    agg.add(ssh_fail(user="root", port=22))
    agg.add(ssh_fail(user="admin", port=22))
    agg.add(ssh_fail(user="root", port=22))
    (ev,) = agg.flush()
    assert ev.users == frozenset({"root", "admin"})
    assert ev.ports == frozenset({22})
    assert ev.actions == frozenset({"failed_password"})
    assert ev.source_ips == frozenset({"185.220.101.34"})
    assert 1 <= len(ev.raw_samples) <= 3


def test_different_keys_emit_separate_events():
    agg = Aggregator(window_sec=30)
    agg.add(ssh_fail(ip="1.1.1.1"))
    agg.add(ssh_fail(ip="2.2.2.2"))
    agg.add(ssh_fail(ip="1.1.1.1", host="other"))
    out = agg.flush()
    assert {(e.signature, e.source_ip, e.dst_host) for e in out} == {
        ("ssh.auth.brute_force", "1.1.1.1", "bastion"),
        ("ssh.auth.brute_force", "2.2.2.2", "bastion"),
        ("ssh.auth.brute_force", "1.1.1.1", "other"),
    }


def test_window_boundary_splits_events_and_emits_in_order():
    agg = Aggregator(window_sec=30)
    out = []
    out += agg.add(ssh_fail(offset=0))
    out += agg.add(ssh_fail(offset=29))
    assert out == []
    out += agg.add(ssh_fail(offset=30))  # closes the first window
    assert len(out) == 1 and out[0].raw_count == 2
    out += agg.flush()
    assert len(out) == 2 and out[1].raw_count == 1
    assert out[1].window_start == T0 + timedelta(seconds=30)


def test_correlation_flag_fires_on_three_distinct_sources_in_a_window():
    agg = Aggregator(window_sec=30)
    agg.add(ssh_fail(ip="1.1.1.1"))
    agg.add(ssh_fail(ip="2.2.2.2"))
    agg.add(ssh_fail(ip="3.3.3.3"))
    out = agg.flush()
    assert len(out) == 3
    assert all(e.correlated for e in out)
    assert all(e.window_source_count == 3 for e in out)


def test_correlation_flag_stays_off_below_three_sources():
    agg = Aggregator(window_sec=30)
    agg.add(ssh_fail(ip="1.1.1.1"))
    agg.add(ssh_fail(ip="2.2.2.2"))
    out = agg.flush()
    assert not any(e.correlated for e in out)
    assert all(e.window_source_count == 2 for e in out)
