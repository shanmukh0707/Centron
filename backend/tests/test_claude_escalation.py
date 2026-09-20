import json
import threading
import time
from types import SimpleNamespace

import pytest

import claude_escalation as ce
from claude_escalation import ClaudeEscalator, assert_no_leak, build_payload
from db import Database
from redaction import LeakError, RedactionMap


def payload(**overrides):
    base = dict(
        signature="ssh.auth.brute_force", src_tokens=["HOST_A"], dst_tokens=["HOST_B"], user_tokens=["USER_1"],
        ports=[22], raw_count=47, window_sec=38, gate="novelty", reason="first occurrence",
        internal_log="HOST_A made 47 failed SSH attempts against HOST_B as USER_1.",
        tts_summary="An outside address is brute forcing SSH logins on one of your servers.",
        severity="high", confidence=0.91, action="block_ip", action_params={"target": "HOST_A", "duration_sec": 3600},
    )
    base.update(overrides)
    return build_payload(**base)


def rmap_with(values):
    r = RedactionMap()
    r.register_host(values.get("HOST_A", "185.220.101.34"))
    r.register_host(values.get("HOST_B", "bastion"))
    r.register_user(values.get("USER_1", "root"))
    return r


class FakeClient:
    """Stands in for anthropic.Anthropic(); records the request, returns a canned verdict."""

    def __init__(self, text="HOST_A brute forced HOST_B; block_ip on HOST_A is appropriate.", raise_exc=None,
                 delay=0.0, stop_reason="end_turn"):
        self.text, self.raise_exc, self.delay, self.stop_reason = text, raise_exc, delay, stop_reason
        self.requests = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.requests.append(kw)
        if self.delay:
            time.sleep(self.delay)
        if self.raise_exc:
            raise self.raise_exc
        return SimpleNamespace(stop_reason=self.stop_reason,
                               content=[SimpleNamespace(type="text", text=self.text)])


# ----------------------------------------------------------- leak check
def test_leak_assertion_rejects_ipv4_ipv6_mac_and_known_values():
    r = rmap_with({})
    with pytest.raises(LeakError):
        assert_no_leak(payload(internal_log="185.220.101.34 attacked HOST_B"), r)
    with pytest.raises(LeakError):
        assert_no_leak(payload(internal_log="fe80::1 attacked HOST_B"), r)
    with pytest.raises(LeakError):
        assert_no_leak(payload(internal_log="MAC 00:11:22:33:44:55 attacked HOST_B"), r)
    with pytest.raises(LeakError):
        assert_no_leak(payload(internal_log="the host called bastion was attacked"), r)  # a known real value
    with pytest.raises(LeakError):
        assert_no_leak(payload(action_params={"target": "185.220.101.34"}), r)  # params too
    assert_no_leak(payload(), r)  # token-only passes


def test_payload_never_sent_when_leaky(monkeypatch):
    client = FakeClient()
    esc = ClaudeEscalator(Database(), client=client)
    with pytest.raises(LeakError):
        esc.escalate("ev", payload(internal_log="see 185.220.101.34"), rmap_with({}))
    with pytest.raises(LeakError):
        esc.submit("ev", payload(internal_log="see 185.220.101.34"), rmap_with({}))
    assert client.requests == []


def test_payload_is_token_only_and_has_no_timestamps():
    text = assert_no_leak(payload(), rmap_with({}))
    obj = json.loads(text)
    assert set(obj) == {"signature", "entities", "raw_count", "window_sec", "gate", "reason", "local_model"}
    assert ":" not in obj["local_model"]["internal_log"]
    assert "window_sec" in obj and "window" not in obj


# ------------------------------------------------------------- verdict
def test_verdict_is_relocalized_and_persisted():
    db = Database()
    client = FakeClient("HOST_A brute forced HOST_B as USER_1; block_ip on HOST_A is appropriate.")
    esc = ClaudeEscalator(db, client=client)
    r = rmap_with({})
    state, verdict = esc.escalate("ev1", payload(), r)
    assert state == "answered"
    assert verdict == "185.220.101.34 brute forced bastion as root; block_ip on 185.220.101.34 is appropriate."
    req = client.requests[0]
    assert req["model"] == "claude-opus-5" and req["max_tokens"] == ce.MAX_TOKENS
    assert "185.220.101.34" not in json.dumps(req, default=str)
    assert "HOST_A" in req["messages"][0]["content"]


def test_verdict_is_capped_at_600_chars():
    client = FakeClient("x" * 2000)
    esc = ClaudeEscalator(Database(), client=client)
    _, verdict = esc.escalate("ev", payload(), rmap_with({}))
    assert len(verdict) == 600


# --------------------------------------------------------- unreachable
@pytest.mark.parametrize("exc", [TimeoutError("8s"), ConnectionError("refused"), RuntimeError("500")])
def test_unreachable_path_never_raises(exc, caplog):
    esc = ClaudeEscalator(Database(), client=FakeClient(raise_exc=exc))
    with caplog.at_level("WARNING", logger="sentinel.claude"):
        state, verdict = esc.escalate("ev", payload(), rmap_with({}))
    assert (state, verdict) == ("unreachable", None)
    assert "claude escalation for ev failed" in caplog.text
    assert esc.status == "unreachable"


def test_refusal_and_empty_are_unreachable():
    esc = ClaudeEscalator(Database(), client=FakeClient(stop_reason="refusal"))
    assert esc.escalate("ev", payload(), rmap_with({})) == ("unreachable", None)
    esc = ClaudeEscalator(Database(), client=FakeClient(text=""))
    assert esc.escalate("ev", payload(), rmap_with({})) == ("unreachable", None)


def test_missing_api_key_is_unreachable_with_clear_warning(monkeypatch, caplog):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    esc = ClaudeEscalator(Database())
    assert esc.api_key is None and esc.status == "unreachable"
    with caplog.at_level("WARNING", logger="sentinel.claude"):
        state, verdict = esc.escalate("ev", payload(), rmap_with({}))
    assert (state, verdict) == ("unreachable", None)
    assert "ANTHROPIC_API_KEY is not set" in caplog.text


def test_api_key_comes_from_env_only(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert ClaudeEscalator(Database()).api_key == "sk-test"


# -------------------------------------------------- fire and forget
def test_submit_runs_off_thread_and_fires_callback_with_db_update():
    db = Database()
    got = []
    done = threading.Event()

    def cb(event_id, verdict, state):
        got.append((event_id, verdict, state, threading.current_thread().name))
        done.set()

    client = FakeClient("Verdict about HOST_A.", delay=0.05)
    esc = ClaudeEscalator(db, client=client, on_verdict=cb)
    t0 = time.monotonic()
    esc.submit("ev9", payload(), rmap_with({}))
    assert time.monotonic() - t0 < 0.05, "submit blocked on the API call"
    assert done.wait(3)
    event_id, verdict, state, thread = got[0]
    assert (event_id, state) == ("ev9", "answered") and verdict == "Verdict about 185.220.101.34."
    assert thread != threading.main_thread().name


def test_submit_unreachable_still_completes_and_marks_db():
    db = Database()
    from schemas import Entities, Escalation, EventData, Source, Window, now_utc, uuid7
    ev = EventData(event_id=uuid7(), signature="s", severity="high", title="t", internal_log="l",
                   tts_summary="Something happened.", confidence=0.5, entities=Entities(),
                   source=Source(kind="auth", collector="sshd", raw_count=1), window=Window(start=now_utc(), end=now_utc()),
                   escalation=Escalation(escalated=True, gate="novelty", reason="r", state="pending"))
    db.insert_event(ev)
    done = threading.Event()
    esc = ClaudeEscalator(db, client=FakeClient(raise_exc=TimeoutError()), on_verdict=lambda *a: done.set())
    esc.submit(str(ev.event_id), payload(), rmap_with({}))
    assert done.wait(3)
    row = db.get_event(ev.event_id)
    assert row["escalation_state"] == "unreachable" and row["escalation_verdict"] is None and row["reviewed"] == 0
