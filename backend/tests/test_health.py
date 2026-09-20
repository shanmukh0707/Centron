"""Honest heartbeat: every subsystem reaches every state under a crafted
condition, and the heartbeat path never touches the network."""

import socket
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

import stub_server
from claude_escalation import ClaudeEscalator
from db import Database
from gates import RateController
from llm import CannedLLM, OllamaClient, OllamaUnreachable, SchemaFailure
from pipeline import (
    CLAUDE_SLOW_SEC,
    LOG_SOURCE_SILENT_SEC,
    LOG_SOURCE_WINDOW_SEC,
    Pipeline,
    SubsystemHealth,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "sample_logs.txt"
YEAR = 2026


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class FakeClaude:
    def __init__(self, text="HOST_A hammered HOST_B; block_ip is right.", exc=None):
        self.text, self.exc = text, exc
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        if self.exc:
            raise self.exc
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.text)])


def fixture_lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def build(llm=None, claude=None, rate=None, db=None, health=None) -> Pipeline:
    db = db or Database()
    esc = ClaudeEscalator(db, client=claude) if claude is not None else None
    return Pipeline(llm=llm or OllamaClient(fake=True), window_sec=30, year=YEAR, db=db,
                    escalator=esc, rate=rate, health=health)


# ------------------------------------------------------------------ claude
def test_claude_no_key_is_unreachable_and_event_still_ships(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    db = Database()
    pipe = Pipeline(llm=OllamaClient(fake=True), window_sec=30, year=YEAR, db=db, escalator=ClaudeEscalator(db))
    assert pipe.status().claude == "unreachable"
    events = list(pipe.run(fixture_lines()))
    pipe.escalator.drain()
    assert events, "events ship regardless of the escalation tier"
    assert any(e.escalation.escalated for e in events)
    assert pipe.status().claude == "unreachable"
    escalated = [db.get_event(e.event_id) for e in events if e.escalation.escalated]
    assert escalated and all(r["escalation_state"] in ("unreachable", "queued_digest") for r in escalated)


def test_claude_key_but_no_call_is_degraded():
    assert build(claude=FakeClaude()).status().claude == "degraded"


def test_claude_last_call_ok_is_ok():
    pipe = build(claude=FakeClaude())
    list(pipe.run(fixture_lines()))
    pipe.escalator.drain()
    assert pipe.health.claude_attempts >= 1
    assert pipe.status().claude == "ok"


def test_claude_last_call_failed_is_unreachable():
    pipe = build(claude=FakeClaude(exc=TimeoutError("8s")))
    list(pipe.run(fixture_lines()))
    pipe.escalator.drain()
    assert pipe.health.claude_attempts >= 1
    assert pipe.status().claude == "unreachable"


def test_claude_ceiling_reached_is_degraded():
    db = Database()
    clock = Clock()
    rate = RateController(db, clock=clock, global_per_hour=0)  # every escalation queues to digest
    h = SubsystemHealth(clock=clock)
    pipe = build(claude=FakeClaude(), rate=rate, db=db, health=h)
    h.note_claude_result(True, 0.5)  # a proven call, so only the ceiling can hold it below ok
    list(pipe.run(fixture_lines()))
    pipe.escalator.drain()
    assert pipe.stats.escalations_queued >= 1
    assert pipe.status().claude == "degraded"
    clock.t += 3601  # the hourly window has passed; nothing has been queued since
    assert pipe.status().claude == "ok"


def test_claude_slow_call_is_degraded():
    h = SubsystemHealth(clock=Clock())
    h.note_claude_key(True)
    h.note_claude_result(True, CLAUDE_SLOW_SEC + 0.1)
    assert h.claude_state() == "degraded"
    h.note_claude_result(True, 1.0)
    assert h.claude_state() == "ok"


# ------------------------------------------------------------------ ollama
def test_ollama_unreachable_then_recovers():
    llm = CannedLLM([OllamaUnreachable("refused")] * 50)
    pipe = build(llm=llm)
    events = list(pipe.run(fixture_lines()))
    assert events and pipe.status().ollama == "unreachable"
    assert pipe.status().tts in ("degraded", "unreachable")  # unrelated subsystem, untouched
    pipe2 = build(llm=OllamaClient(fake=True), health=pipe.health)
    list(pipe2.run(fixture_lines()))
    assert pipe2.status().ollama == "ok"


def test_ollama_schema_failure_is_degraded():
    pipe = build(llm=CannedLLM([SchemaFailure("twice")]))
    list(pipe.run(fixture_lines()))
    assert pipe.status().ollama == "degraded"


def test_ollama_retry_is_degraded():
    class RetryingLLM(CannedLLM):
        retries = 0

        def generate(self, mi, tokens=None):
            self.retries += 1  # the client's schema-retry counter moved
            return OllamaClient(fake=True).generate(mi, tokens)

    pipe = build(llm=RetryingLLM([]))
    list(pipe.run(fixture_lines()))
    assert pipe.status().ollama == "degraded"


def test_ollama_slow_p50_is_degraded():
    h = SubsystemHealth(clock=Clock())
    for _ in range(3):
        h.note_ollama("ok", 9.0)
    assert h.ollama_state() == "degraded"
    for _ in range(4):
        h.note_ollama("ok", 1.0)
    assert h.ollama_state() == "ok"


# --------------------------------------------------------------------- tts
@pytest.mark.parametrize("engine, render_ok, expected", [
    ("none", None, "unreachable"),
    ("espeak-ng", True, "degraded"),
    ("elevenlabs", None, "degraded"),   # a key is not proof
    ("elevenlabs", True, "ok"),
    ("elevenlabs", False, "unreachable"),
    ("espeak-ng", False, "unreachable"),
])
def test_tts_states(engine, render_ok, expected):
    h = SubsystemHealth(clock=Clock())
    h.note_tts_engine(engine)
    if render_ok is not None:
        h.note_tts_render(render_ok)
    assert h.tts_state() == expected


# -------------------------------------------------------------- log_source
def test_log_source_silent_source_is_unreachable():
    clock = Clock()
    h = SubsystemHealth(clock=clock)
    h.note_log_source_configured()
    assert h.log_source_state() == "ok"  # just started, no verdict yet
    clock.t += LOG_SOURCE_SILENT_SEC + 1
    assert h.log_source_state() == "unreachable"
    h.note_line(parsed=True, malformed=False)
    assert h.log_source_state() == "ok"
    clock.t += LOG_SOURCE_SILENT_SEC + 1
    assert h.log_source_state() == "unreachable"


def test_log_source_unconfigured_never_times_out():
    clock = Clock()
    h = SubsystemHealth(clock=clock)
    clock.t += 10 * LOG_SOURCE_SILENT_SEC
    assert h.log_source_state() == "ok"


def test_log_source_malformed_rate_is_degraded():
    clock = Clock()
    h = SubsystemHealth(clock=clock)
    for _ in range(3):
        h.note_line(parsed=False, malformed=True)
    h.note_line(parsed=True, malformed=False)
    assert h.log_source_drop_rate() == pytest.approx(0.75)
    assert h.log_source_state() == "degraded"
    # Well-formed noise (cron, sudo) is not a drop for this purpose.
    h2 = SubsystemHealth(clock=clock)
    for _ in range(9):
        h2.note_line(parsed=False, malformed=False)
    h2.note_line(parsed=True, malformed=False)
    assert h2.log_source_state() == "ok"


def test_log_source_window_rotates():
    clock = Clock()
    h = SubsystemHealth(clock=clock)
    for _ in range(4):
        h.note_line(parsed=False, malformed=True)
    assert h.log_source_state() == "degraded"
    clock.t += LOG_SOURCE_WINDOW_SEC + 1
    for _ in range(4):
        h.note_line(parsed=True, malformed=False)
    assert h.log_source_state() == "ok"


def test_log_source_rfc3339_lines_degrade_through_the_pipeline():
    pipe = build()
    pipe.health.note_log_source_configured()
    rfc3339 = "2026-09-20T10:00:00.000000+00:00 serverpi sshd[1]: Failed password for root from 1.2.3.4 port 1 ssh2"
    list(pipe.run([rfc3339] * 6))
    assert pipe.parser.malformed == 6
    assert pipe.status().log_source == "degraded"


def test_log_source_dead_thread_is_unreachable():
    pipe = build()
    pipe.log_source_state = "unreachable"  # what stub_server does when consume() dies
    assert pipe.status().log_source == "unreachable"


# -------------------------------------------------------------- heartbeat
def test_heartbeat_makes_no_network_calls(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("heartbeat touched the network")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    pipe = build(claude=FakeClaude())
    hub = stub_server.Hub(cfg=stub_server.Config())
    hub.pipe = pipe
    hb = stub_server.heartbeat_data(hub)
    assert hb.pipeline == pipe.status()
    assert hb.pipeline.claude == "degraded"

    hub.pipe = None  # scripted mode reads the standby object
    hub.standby_health.note_tts_engine("espeak-ng")
    assert stub_server.heartbeat_data(hub).pipeline.tts == "degraded"


def test_pipeline_health_is_shared_with_escalator():
    pipe = build(claude=FakeClaude())
    assert pipe.escalator.health is pipe.health
    assert pipe.health.claude_key_present is True
