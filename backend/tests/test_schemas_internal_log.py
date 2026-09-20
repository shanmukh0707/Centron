import pytest
from pydantic import ValidationError

from schemas import EventData, OllamaOutput, Source, Window, validate_no_tokens, _T0


def event(internal_log: str) -> EventData:
    return EventData(
        signature="ssh.auth.brute_force", severity="high", title="t", internal_log=internal_log,
        tts_summary="Repeated failed logins were seen.", confidence=0.9,
        source=Source(kind="auth", collector="sshd", raw_count=1), window=Window(start=_T0, end=_T0),
    )


def ollama(internal_log: str) -> OllamaOutput:
    return OllamaOutput(
        internal_log=internal_log, tts_summary="Repeated failed logins were seen.", severity="high",
        confidence=0.9, escalate=False, action="notify_only",
    )


def test_event_internal_log_accepts_real_values():
    ev = event("185.220.101.34 hit bastion as root from fe80::1 via 00:11:22:33:44:55")
    assert "185.220.101.34" in ev.internal_log


@pytest.mark.parametrize("tok", ["HOST_A", "USER_1", "MAC_1", "HOST_AB"])
def test_event_internal_log_rejects_leftover_tokens(tok):
    with pytest.raises(ValidationError, match="token"):
        event(f"something about {tok} here")


def test_ollama_internal_log_still_rejects_literals_and_accepts_tokens():
    ollama("HOST_A hit HOST_B as USER_1")
    with pytest.raises(ValidationError):
        ollama("185.220.101.34 hit bastion")


def test_validate_no_tokens_reports_each_token():
    assert validate_no_tokens("clean text") == []
    out = validate_no_tokens("HOST_A and USER_2 and HOST_A")
    assert len(out) == 2
