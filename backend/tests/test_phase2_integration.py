"""Phase 2 end to end: fixture -> gates -> executor -> mocked Claude -> EventData."""

import io
import json
import re
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from assets import AssetRegistry
from claude_escalation import ClaudeEscalator
from db import Database
from executor import Executor
from gates import QUEUED_DIGEST, RateController
from llm import CannedLLM, ModelInput, OllamaClient, OllamaUnreachable, SchemaFailure, load_prompt_spec
from pipeline import Pipeline, fallback_tts
from redaction import TOKEN_RE, RedactionMap
from schemas import EventData, OllamaOutput, make_frame, parse_frame, validate_tts

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "sample_logs.txt"
YEAR = 2026

_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:])|::[0-9A-Fa-f]{1,4}")
_MAC = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
_PORT = re.compile(r"(?::\d{1,5}\b)|(?i:\bports?\s*#?\s*\d{1,5}\b)|(?:\b\d{4,5}\b)")
FIXTURE_USERS = {"admin", "root", "oracle", "test", "svc-backup", "alice"}


def fixture_lines() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


class FakeClaude:
    def __init__(self, text="HOST_A hammered HOST_B; the chosen action is appropriate.", exc=None):
        self.text, self.exc, self.requests = text, exc, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        if self.exc:
            raise self.exc
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.text)])


def build(llm=None, db=None, claude=None, protected=(), rate=None, on_update=None):
    db = db or Database()
    assets = AssetRegistry.of(protected)
    esc = ClaudeEscalator(db, client=claude) if claude is not None else None
    return Pipeline(llm=llm or OllamaClient(fake=True), window_sec=30, year=YEAR, db=db, assets=assets,
                    executor=Executor(db, assets), escalator=esc, rate=rate, on_update=on_update)


def assert_tts_boundary(text: str) -> None:
    assert not _IPV4.search(text), text
    assert not _IPV6.search(text), text
    assert not _MAC.search(text), text
    assert not _PORT.search(text), text
    assert "@" not in text
    for u in FIXTURE_USERS:
        assert not re.search(rf"\b{re.escape(u)}\b", text), (u, text)
    assert not TOKEN_RE.search(text), text


# ---------------------------------------------------------- tts boundary
def test_tts_summary_never_leaks_on_event_data_for_the_fixture():
    for ev in build().run(fixture_lines()):
        assert_tts_boundary(ev.tts_summary)
        assert validate_tts(ev.tts_summary, {"HOST_A", "USER_1"}) == []


@pytest.mark.parametrize("bad", [
    "Blocked 185.220.101.34 after failed logins.",
    "Blocked fe80::1 after failed logins.",
    "Device 00:11:22:33:44:55 was blocked.",
    "Root tried to log in on port 22 and failed.",
    "Someone hit :8080 repeatedly today.",
    "HOST_A was blocked for an hour.",
    "USER_1 signed in from outside.",
    "Mail to admin@example.com bounced.",
    "Session 51234 was terminated by the firewall.",
])
def test_tts_summary_rejected_on_ollama_output_and_event_data(bad):
    base = dict(internal_log="HOST_A did a thing.", severity="high", confidence=0.9, escalate=False,
                escalate_reason=None, action="notify_only", action_params={"channel": "app"})
    with pytest.raises(ValidationError):
        OllamaOutput.model_validate({**base, "tts_summary": bad}, context={"tokens": {"HOST_A", "USER_1"}})
    from schemas import Entities, Source, Window, now_utc
    with pytest.raises(ValidationError):
        EventData.model_validate(
            dict(signature="x", severity="high", title="t", internal_log="fine", tts_summary=bad, confidence=0.9,
                 entities=Entities().model_dump(), source=Source(kind="auth", collector="sshd", raw_count=1).model_dump(),
                 window=Window(start=now_utc(), end=now_utc()).model_dump(mode="json")),
            context={"tokens": {"HOST_A", "USER_1"}},
        )


def test_tts_fallback_templates_respect_the_boundary():
    for sig in ("ssh.auth.brute_force", "ssh.auth.success", "net.portscan", "dns.blocked", "zz.unknown"):
        for sev in ("info", "low", "high", "critical"):
            assert_tts_boundary(fallback_tts(sig, sev))


# ----------------------------------------------------- few-shot examples
def _mock_ollama(monkeypatch, replies: list[str]):
    """Patch urllib so OllamaClient's real path receives canned /api/chat replies."""
    bodies = []

    def fake_urlopen(req, timeout=None):
        bodies.append(json.loads(req.data.decode()))
        content = replies.pop(0)
        payload = json.dumps({"message": {"role": "assistant", "content": content}, "done": True}).encode()
        resp = io.BytesIO(payload)
        resp.__enter__ = lambda s: s
        resp.__exit__ = lambda s, *a: None
        return resp

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return bodies


def test_six_few_shot_examples_parse_through_the_real_ollama_path(monkeypatch):
    spec = load_prompt_spec()
    examples = spec.few_shot_examples
    assert len(examples) == 6
    client = OllamaClient(base_url="http://ollama.test:11434")
    for user, assistant in examples:
        bodies = _mock_ollama(monkeypatch, [json.dumps(assistant)])
        mi = ModelInput(signature=user["signature"], raw_count=user["raw_count"], window_start="", window_end="",
                        src_tokens=user["sources"], dst_tokens=user["targets"], user_tokens=user["users"],
                        ports=user["ports"], sample_lines=user["samples"], correlated=user["correlated"],
                        novel_signature=user["novel_signature"], window_sec=user["window_sec"])
        tokens = set(user["sources"] + user["targets"] + user["users"])
        out = client.generate(mi, tokens)
        assert isinstance(out, OllamaOutput)
        assert out.action == assistant["action"] and out.severity == assistant["severity"]
        assert_tts_boundary(out.tts_summary)
        body = bodies[0]
        assert body["model"] == "qwen2.5:14b" and body["format"] == "json" and body["keep_alive"] == "30m"
        assert body["options"] == spec.options
        assert body["messages"][0]["content"] == spec.system
        assert json.loads(body["messages"][-1]["content"]) == mi.to_dict()
    assert client.retries == 0 and client.status == "ok"


def test_real_path_retries_once_with_actual_violations_then_schema_failure(monkeypatch):
    client = OllamaClient(base_url="http://ollama.test:11434")
    good = json.dumps(load_prompt_spec().few_shot_examples[0][1])
    bad = json.dumps({"internal_log": "x", "tts_summary": "Blocked 1.2.3.4.", "severity": "loud", "confidence": 2,
                      "escalate": True, "escalate_reason": None, "action": "block_ip", "action_params": {}})
    bodies = _mock_ollama(monkeypatch, [bad, good])
    mi = ModelInput("ssh.auth.brute_force", 1, "", "", ["HOST_A"], ["HOST_B"], [], [22], [], window_sec=10)
    out = client.generate(mi, {"HOST_A", "HOST_B"})
    assert out.action == "block_ip" and client.retries == 1
    retry_turn = bodies[1]["messages"][-1]["content"]
    assert retry_turn.startswith("Your previous response was rejected")
    assert "severity" in retry_turn and "tts_summary" in retry_turn and "confidence" in retry_turn
    assert bodies[1]["messages"][-2]["content"] == bad

    _mock_ollama(monkeypatch, [bad, bad])
    with pytest.raises(SchemaFailure):
        client.generate(mi, {"HOST_A", "HOST_B"})
    assert client.status == "degraded"


def test_unreachable_ollama_raises_within_timeout(monkeypatch):
    def refuse(req, timeout=None):
        raise ConnectionRefusedError("no route")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    client = OllamaClient(base_url="http://10.255.255.1:11434")
    with pytest.raises(OllamaUnreachable):
        client.generate(ModelInput("net.portscan", 1, "", "", window_sec=1), set())
    assert client.status == "unreachable" and client.timeout_sec == 15.0


# ------------------------------------------------------ full integration
def test_full_integration_every_event_validates_and_claude_reemits():
    updates, done = [], threading.Event()

    def on_update(ev):
        updates.append(ev)
        if len(updates) >= 4:
            done.set()

    claude = FakeClaude("HOST_A tried many SSH passwords on HOST_B; block_ip on HOST_A is appropriate.")
    pipe = build(claude=claude, on_update=on_update)
    events = list(pipe.run(fixture_lines()))
    assert len(events) == 8
    for i, ev in enumerate(events):
        EventData.model_validate(ev.model_dump(mode="json"))
        parse_frame(make_frame("event", ev, seq=i + 1).model_dump_json())
        assert ev.action is not None
        assert pipe.db.get_event(ev.event_id) is not None
    # 4 signatures are novel on a fresh DB -> 4 escalations sent; the rest are cooled down / not escalated
    assert pipe.stats.escalations_sent == 4
    assert done.wait(5), "verdict callbacks never arrived"
    pipe.escalator.drain()
    assert len(updates) == 4
    for up in updates:
        assert up.escalation.state == "answered" and up.reviewed is True
        assert "HOST_" not in up.escalation.verdict
        EventData.model_validate(up.model_dump(mode="json"))
        row = pipe.db.get_event(up.event_id)
        assert row["escalation_state"] == "answered" and row["reviewed"] == 1 and row["escalation_verdict"] == up.escalation.verdict
    brute = next(u for u in updates if u.signature == "ssh.auth.brute_force")
    assert "185.220.101.34" in brute.escalation.verdict and "bastion" in brute.escalation.verdict
    # nothing real reached Claude
    for req in claude.requests:
        blob = json.dumps(req, default=str)
        assert not _IPV4.search(blob) and "bastion" not in blob and "root" not in blob


def test_claude_is_called_after_the_event_was_yielded():
    order = []
    claude = FakeClaude()
    pipe = build(claude=claude)
    original = pipe.escalator.submit

    def spy(*a, **k):
        order.append("submit")
        return original(*a, **k)

    pipe.escalator.submit = spy
    gen = pipe.run(fixture_lines())
    first = next(gen)
    assert order == [], "Claude was called before the event was handed over"
    assert first.escalation.state == "pending"
    next(gen)
    assert order == ["submit"]
    pipe.escalator.drain()


def test_cooldown_and_ceiling_are_recorded_as_queued_digest_on_wire_pending():
    db = Database()
    pipe = build(db=db, claude=FakeClaude())
    events = list(pipe.run(fixture_lines()))
    pipe.escalator.drain()
    brute = [e for e in events if e.signature == "ssh.auth.brute_force"]
    assert len(brute) == 4  # one in the first window, three correlated later
    states = [db.get_event(e.event_id)["escalation_state"] for e in brute]
    assert states[0] == "answered" and states[1:] == [QUEUED_DIGEST] * 3
    for e in brute[1:]:
        assert e.escalation.escalated and e.escalation.state == "pending" and e.reviewed is False
    assert pipe.stats.escalations_queued == 3


def test_unreachable_claude_never_raises_and_event_completes():
    done = threading.Event()
    pipe = build(claude=FakeClaude(exc=TimeoutError("8s")), on_update=lambda ev: done.set())
    events = list(pipe.run(fixture_lines()))
    assert len(events) == 8
    assert done.wait(5)
    pipe.escalator.drain()
    rows = [pipe.db.get_event(e.event_id) for e in events if e.escalation.escalated]
    assert rows and all(r["escalation_state"] in ("unreachable", QUEUED_DIGEST) for r in rows)
    assert all(r["reviewed"] == 0 for r in rows)


# ------------------------------------------------------------- fallback
@pytest.mark.parametrize("exc", [SchemaFailure("twice"), OllamaUnreachable("timeout")])
def test_llm_failure_builds_fallback_event_and_never_drops(exc):
    llm = CannedLLM([exc] * 20)
    pipe = build(llm=llm)
    events = list(pipe.run(fixture_lines()))
    assert len(events) == 8
    for ev in events:
        EventData.model_validate(ev.model_dump(mode="json"))
        assert ev.action.playbook == "notify_only" and ev.action.status == "auto_executed"
        assert ev.escalation.escalated is True
        assert ev.escalation.gate in ("schema_failure", "novelty", "correlation")
        assert ev.tts_summary == fallback_tts(ev.signature, ev.severity)
        assert_tts_boundary(ev.tts_summary)
        assert "Local model unavailable" in ev.internal_log
        assert not TOKEN_RE.search(ev.internal_log)
        assert pipe.db.get_event(ev.event_id)["action_playbook"] == "notify_only"
    assert pipe.stats.llm_failures == 8
    sf = [e for e in events if e.escalation.gate == "schema_failure"]
    assert sf, "schema_failure gate never surfaced"


# ---------------------------------------------------- persistence
def test_redaction_map_persists_across_restart(tmp_path):
    path = tmp_path / "s.db"
    db1 = Database(path)
    p1 = build(db=db1)
    list(p1.run(fixture_lines()))
    pairs = db1.load_redaction_map()
    assert pairs and "185.220.101.34" in pairs.values()
    db1.close()

    db2 = Database(path)
    p2 = build(db=db2)
    assert p2.rmap.token_for("185.220.101.34") == {v: k for k, v in pairs.items()}["185.220.101.34"]
    assert p2.rmap.register_host("198.51.100.250") not in pairs
    assert db2.count_events() == 8
    assert db2.signature_seen_before("net.portscan")


def test_every_event_is_inserted_with_actions_recorded():
    pipe = build()
    events = list(pipe.run(fixture_lines()))
    assert pipe.db.count_events() == len(events)
    actions = list(pipe.db.iter_actions())
    assert len(actions) == len(events)
    for a in actions:
        assert a["status"] in ("auto_executed", "pending_approval", "denied", "failed")


def test_protected_target_denied_becomes_blast_radius_with_no_action():
    pipe = build(protected={"185.220.101.34"})
    ev = next(e for e in pipe.run(fixture_lines()) if e.signature == "ssh.auth.brute_force")
    assert ev.action is None
    assert ev.escalation.gate == "novelty"  # novelty wins ordering on a fresh db; blast radius recorded in stats
    assert pipe.stats.actions_rejected >= 1


def test_blast_radius_gate_when_signature_is_known():
    db = Database()
    list(build(db=db).run(fixture_lines()))  # seed
    pipe = build(db=db, protected={"185.220.101.34"})
    ev = next(e for e in pipe.run(fixture_lines()) if e.signature == "ssh.auth.brute_force")
    assert ev.action is None and ev.escalation.gate == "blast_radius"
    assert "protected asset" in ev.escalation.reason


def test_status_reports_subsystems():
    pipe = build(claude=FakeClaude())
    st = pipe.status()
    # Credentials alone prove nothing: claude is degraded until a call succeeds.
    assert st.ollama == "ok" and st.claude == "degraded" and st.log_source == "ok"
    list(pipe.run(fixture_lines()))
    pipe.escalator.drain()
    assert pipe.status().claude == "ok"
    assert build().status().claude == "unreachable"
