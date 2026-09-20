import asyncio
import json
import socket
from pathlib import Path

import pytest

import assets as assets_mod
from assets import AssetRegistry, ProtectedListEmpty, local_addresses
from db import Database
from executor import Executor
from ingest import SyslogListener, load_source_map, normalize_syslog
from parsers import LineParser

BACKEND = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- assets
def test_shipped_config_parses_and_is_empty_by_design():
    reg = AssetRegistry.load(BACKEND / "protected_assets.yaml", auto_detect=False, env=False)
    assert reg.configured == set()
    assert reg.is_protected("127.0.0.1")  # floor
    assert not reg.is_protected("8.8.8.8")


def test_config_file_yaml_and_json_and_env(tmp_path, monkeypatch):
    y = tmp_path / "a.yaml"
    y.write_text("protected_assets:\n  - 192.168.1.1   # gw\n  - 10.0.0.0/24\n  - nas01\n", encoding="utf-8")
    reg = AssetRegistry.load(y, auto_detect=False, env=False)
    assert reg.configured == {"192.168.1.1", "10.0.0.0/24", "nas01"}
    assert reg.is_protected("10.0.0.200") and reg.is_protected("NAS01") and reg.is_protected("192.168.1.1/32")
    assert not reg.is_protected("10.0.1.1")

    j = tmp_path / "b.json"
    j.write_text(json.dumps({"protected_assets": ["172.16.0.1"]}), encoding="utf-8")
    monkeypatch.setenv("SENTINEL_PROTECTED_ASSETS", "hv01, 172.16.0.2")
    reg = AssetRegistry.load(j, auto_detect=False)
    assert reg.configured == {"172.16.0.1", "hv01", "172.16.0.2"}


def test_yaml_lite_fallback_reads_the_shipped_file_without_pyyaml(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def no_yaml(name, *a, **k):
        if name == "yaml":
            raise ImportError
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_yaml)
    reg = AssetRegistry.load(BACKEND / "protected_assets.yaml", auto_detect=False, env=False)
    assert reg.configured == set()


def test_auto_detect_adds_local_addresses_and_gateway(monkeypatch):
    monkeypatch.setattr(assets_mod, "local_addresses", lambda: {"192.168.7.20"})
    monkeypatch.setattr(assets_mod, "default_gateway", lambda: "192.168.7.1")
    reg = AssetRegistry.load(BACKEND / "protected_assets.yaml", auto_detect=True, env=False)
    assert {"192.168.7.20", "192.168.7.1", "127.0.0.0/8", "::1"} <= reg.detected
    assert reg.is_protected("192.168.7.1") and reg.is_protected("192.168.7.20")
    assert reg.configured == set()


def test_live_mode_refuses_when_only_auto_detected_addresses_exist(monkeypatch):
    monkeypatch.setattr(assets_mod, "local_addresses", lambda: {"192.168.7.20"})
    monkeypatch.setattr(assets_mod, "default_gateway", lambda: "192.168.7.1")
    reg = AssetRegistry.load(BACKEND / "protected_assets.yaml", auto_detect=True, env=False)
    with pytest.raises(ProtectedListEmpty) as ei:
        Executor(Database(), reg, dry_run=False)
    assert "protected_assets.yaml" in str(ei.value) and "SENTINEL_PROTECTED_ASSETS" in str(ei.value)
    # dry run is fine with the same registry
    Executor(Database(), reg, dry_run=True)


def test_live_mode_starts_once_configured(monkeypatch):
    monkeypatch.setenv("SENTINEL_PROTECTED_ASSETS", "192.168.1.1")
    reg = AssetRegistry.load(BACKEND / "protected_assets.yaml", auto_detect=False)
    Executor(Database(), reg, dry_run=False)


def test_registry_publishes_into_schemas_validators(monkeypatch):
    import schemas
    before = set(schemas.PROTECTED_ASSETS)
    try:
        AssetRegistry.load(None, auto_detect=False, env=False, extra=["198.18.0.77"])
        from schemas import validate_and_classify
        ok, _, why = validate_and_classify({"target": "198.18.0.77", "duration_sec": 60}, "block_ip")
        assert ok is False and "protected asset" in why
    finally:
        schemas.PROTECTED_ASSETS.clear()
        schemas.PROTECTED_ASSETS.update(before)


def test_local_addresses_returns_only_addresses():
    import ipaddress
    for a in local_addresses():
        ipaddress.ip_address(a)


# ---------------------------------------------------------------- ingest
def test_normalize_strips_pri_and_rewrites_rfc5424():
    raw = "<34>Sep 19 11:59:01 bastion sshd[2041]: Failed password for root from 185.220.101.34 port 51240 ssh2"
    assert normalize_syslog(raw) == raw[4:]
    r5424 = "<86>1 2026-09-19T11:59:01.123Z bastion sshd 2041 - - Failed password for root from 185.220.101.34 port 51240 ssh2"
    line = normalize_syslog(r5424)
    assert line == "Sep 19 11:59:01 bastion sshd[2041]: Failed password for root from 185.220.101.34 port 51240 ssh2"
    assert LineParser(year=2026).parse(line) is not None


def test_source_map_loads_and_rejects_unknown_kind(tmp_path):
    assert load_source_map(BACKEND / "source_map.yaml") == {}
    y = tmp_path / "m.yaml"
    y.write_text("sources:\n  192.168.1.1: firewall\n  192.168.1.30: auth\n  192.168.1.53: dns\n", encoding="utf-8")
    assert load_source_map(y) == {"192.168.1.1": "firewall", "192.168.1.30": "auth", "192.168.1.53": "dns"}
    y.write_text("sources:\n  192.168.1.1: proxy\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_source_map(y)


def test_lines_from_local_host_are_dropped_and_counted():
    lis = SyslogListener(exclude={"192.168.7.20", "127.0.0.1"}, source_map={"192.168.7.1": "firewall"})
    good = "Sep 19 11:59:01 bastion sshd[2041]: Failed password for root from 185.220.101.34 port 51240 ssh2"
    assert lis.feed("192.168.7.20", good) is False
    assert lis.feed("127.0.0.1", good) is False
    assert lis.feed("192.168.7.1", good) is True
    assert lis.feed("10.9.9.9", good) is True
    assert lis.stats.dropped_local == 2 and lis.stats.received == 4
    assert lis.queue.qsize() == 2
    t1, t2 = lis.queue.get(), lis.queue.get()
    assert (t1.sender, t1.kind) == ("192.168.7.1", "firewall")
    assert (t2.sender, t2.kind) == ("10.9.9.9", None)


def test_listener_excludes_local_addresses_by_default():
    lis = SyslogListener()
    assert lis.exclude == local_addresses()


def test_kind_hint_restricts_parser_family():
    p = LineParser(year=2026)
    ssh = "Sep 19 11:59:01 bastion sshd[2041]: Failed password for root from 185.220.101.34 port 51240 ssh2"
    assert p.parse(ssh, "auth") is not None
    assert p.parse(ssh, "firewall") is None
    assert p.parse(ssh, None) is not None


def test_udp_and_tcp_listener_end_to_end():
    async def run():
        lis = SyslogListener(host="127.0.0.1", port=0, exclude=set())
        await lis.start()
        port = lis.bound_port
        msg = b"<34>Sep 19 11:59:01 bastion sshd[2041]: Failed password for root from 185.220.101.34 port 51240 ssh2"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(msg, ("127.0.0.1", port))
        r, w = await asyncio.open_connection("127.0.0.1", port)
        w.write(msg + b"\n" + msg + b"\n")
        await w.drain()
        w.close()
        for _ in range(50):
            if lis.queue.qsize() >= 3:
                break
            await asyncio.sleep(0.05)
        await lis.stop()
        return lis

    lis = asyncio.run(run())
    assert lis.stats.received == 3 and lis.queue.qsize() == 4  # 3 lines + the stop sentinel
    lines = list(lis.lines())
    assert len(lines) == 3 and all(l.line.startswith("Sep 19") for l in lines)


def test_lines_yields_none_ticks_while_idle():
    lis = SyslogListener(exclude=set())
    gen = lis.lines(tick_sec=0.05)
    assert next(gen) is None
    lis.feed("10.0.0.5", "hello")
    assert next(gen).line == "hello"
