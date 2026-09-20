"""stub_server.py --source pipeline over a real socket."""

import asyncio
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

import schemas as S
import stub_server

ROOT = Path(__file__).resolve().parent.parent
websockets = pytest.importorskip("websockets")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, *extra: str) -> None:
        self.port = free_port()
        self.proc = subprocess.Popen(
            [sys.executable, "stub_server.py", "--host", "127.0.0.1", "--port", str(self.port),
             "--log-level", "warning", *extra],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
        )
        self.url = f"ws://127.0.0.1:{self.port}/ws"

    def wait(self, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited: {self.proc.stderr.read()}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/healthz", timeout=1):
                    return
            except OSError:
                time.sleep(0.2)
        raise RuntimeError("server did not come up")

    def state(self) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/debug/state", timeout=2) as r:
            return json.loads(r.read())

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


async def collect(url: str, seconds: float, approve_first_pending: bool = False) -> list:
    frames = []
    approved = False
    async with websockets.connect(url) as ws:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            fr = S.parse_frame(raw)  # every frame re-parses through the Frame union
            frames.append(fr)
            a = getattr(fr.data, "action", None)
            if approve_first_pending and not approved and fr.type == "event" and a and a.status == "pending_approval":
                approved = True
                appr = S.make_frame("approve_action", S.ApproveActionData(
                    approval_id=a.approval_id, event_id=fr.data.event_id, decision="approved", biometric=True), seq=1)
                await ws.send(S.dump_frame(appr))
    return frames


PIPELINE_ARGS = ("--source", "pipeline", "--fixture", "--fake-llm", "--year", "2026", "--replay-rate", "60",
                 "--no-auto-assets")


@pytest.fixture(scope="module")
def pipeline_frames():
    srv = Server(*PIPELINE_ARGS)
    try:
        srv.wait()
        frames = asyncio.run(collect(srv.url, 8.0, approve_first_pending=True))
        state = srv.state()
    finally:
        srv.stop()
    return frames, state


def test_source_pipeline_serves_frames_with_strictly_increasing_seq(pipeline_frames):
    frames, _ = pipeline_frames
    assert frames[0].type == "hello"
    seqs = [f.seq for f in frames]
    assert all(b > a for a, b in zip(seqs, seqs[1:])), seqs
    events = [f for f in frames if f.type == "event"]
    assert len(events) >= 8
    sigs = {e.data.signature for e in events}
    assert sigs == {"ssh.auth.brute_force", "ssh.auth.success", "net.portscan", "dns.blocked"}
    for e in events:
        S.EventData.model_validate(e.data.model_dump(mode="json"))


def test_late_verdict_reemits_same_event_id_with_fresh_seq(pipeline_frames):
    frames, _ = pipeline_frames
    events = [f for f in frames if f.type == "event"]
    by_id: dict = {}
    for f in events:
        by_id.setdefault(f.data.event_id, []).append(f)
    reemitted = {k: v for k, v in by_id.items() if len(v) > 1}
    assert reemitted, "no event was re-emitted after the escalation verdict"
    for eid, fs in reemitted.items():
        assert fs[0].data.escalation.state == "pending"
        assert fs[-1].data.escalation.state in ("unreachable", "answered")  # no API key in CI -> unreachable
        assert fs[-1].seq > fs[0].seq
        assert fs[0].data.signature == fs[-1].data.signature


def test_approval_over_socket_calls_executor_and_reports_true_outcome(pipeline_frames):
    frames, state = pipeline_frames
    updates = [f for f in frames if f.type == "action_update"]
    assert updates, "approve_action produced no action_update"
    au = updates[0].data
    assert au.status == "approved" and au.playbook == "block_ip"
    assert au.reason and "DRY RUN" in au.reason
    assert au.approval_id not in state["pending_approvals"]
    assert state["source"] == "pipeline" and state["executor"]["dry_run"] is True


def test_pipeline_events_carry_audio_except_info(pipeline_frames):
    """info is silent; everything else carries an audio ref.

    The status is deliberately not pinned to "ready". With a TTS renderer
    present the first emission is "pending" and an audio_ready frame follows,
    which is what contracts.md requires -- never block an event on TTS. With no
    renderer the server attaches its silent clip and the ref is "ready"
    immediately.

    Asserting "ready" here made the outcome depend on whether espeak-ng happened
    to be installed on the machine running the suite: green on a dev laptop, red
    on serverpi. The invariant that actually matters is that a spoken severity
    always gets a ref, and info never does.
    """
    frames, _ = pipeline_frames
    for f in frames:
        if f.type != "event":
            continue
        if f.data.severity == "info":
            assert f.data.audio is None
        else:
            assert f.data.audio is not None
            assert f.data.audio.status in ("ready", "pending")
            assert f.data.audio.cache_key  # needed to dedupe speech later


def test_heartbeat_reflects_pipeline_subsystems():
    srv = Server(*PIPELINE_ARGS)
    try:
        srv.wait()
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.port}/debug/degrade?subsystem=tts", timeout=2):
            pass
        state = srv.state()
        assert state["pipeline"]["ollama"] == "ok"
        assert state["pipeline"]["claude"] == "unreachable"  # no ANTHROPIC_API_KEY in tests
    finally:
        srv.stop()


# ------------------------------------------------------------- --quiet
def test_quiet_suppresses_info_and_low_events_entirely():
    srv = Server(*PIPELINE_ARGS, "--quiet")
    try:
        srv.wait()
        frames = asyncio.run(collect(srv.url, 6.0))
        state = srv.state()
    finally:
        srv.stop()
    events = [f for f in frames if f.type == "event"]
    assert events and all(e.data.severity in ("high", "critical") for e in events)
    assert state["quiet"] is True and state["quiet_dropped"] >= 1
    seqs = [f.seq for f in frames]
    assert all(b > a for a, b in zip(seqs, seqs[1:]))


def test_quiet_also_applies_to_scripted_source():
    srv = Server("--quiet", "--rate", "600")
    try:
        srv.wait()
        frames = asyncio.run(collect(srv.url, 6.0))
    finally:
        srv.stop()
    events = [f for f in frames if f.type == "event"]
    assert events and all(e.data.severity in ("high", "critical") for e in events)
    # follow-ups for suppressed events (audio_ready / action_update) are suppressed too
    ids = {e.data.event_id for e in events}
    for f in frames:
        if f.type in ("audio_ready", "action_update"):
            assert f.data.event_id in ids


# -------------------------------------------------------------- calm
def test_calm_scenario_is_one_event_per_minute_with_a_single_critical():
    assert stub_server.CALM_GAP_S == 60.0
    assert "burst_6_in_20s" not in stub_server.CALM_ORDER
    assert stub_server.CALM_ORDER.count("critical_isolate_pending") == 1
    assert "critical_isolate_pending" not in stub_server.CALM_LOOP
    assert set(stub_server.CALM_ORDER) <= set(stub_server.SCENARIOS)


def test_calm_first_event_arrives_and_no_second_within_the_gap():
    srv = Server("--scenario", "calm")
    try:
        srv.wait()
        frames = asyncio.run(collect(srv.url, 9.0))
    finally:
        srv.stop()
    events = [f for f in frames if f.type == "event"]
    assert len(events) == 1 and events[0].data.severity == "info"


def test_default_source_is_still_scripted():
    srv = Server("--rate", "600")
    try:
        srv.wait()
        state = srv.state()
        assert state["source"] == "scripted" and state["executor"] is None
    finally:
        srv.stop()


def test_source_pipeline_requires_its_flags():
    proc = subprocess.run([sys.executable, "stub_server.py", "--source", "pipeline"], cwd=ROOT,
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2 and "--fixture" in proc.stderr
