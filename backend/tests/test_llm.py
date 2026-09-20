import json

import pytest

from llm import ModelInput, OllamaClient
from schemas import OllamaOutput, validate_tts


def mi(signature: str, **kw) -> ModelInput:
    base = dict(
        signature=signature, raw_count=12, window_start="2026-09-19T11:59:00.000Z",
        window_end="2026-09-19T11:59:30.000Z", src_tokens=["HOST_A"], dst_tokens=["HOST_B"],
        user_tokens=["USER_1", "USER_2"], ports=[22], sample_lines=["Failed password for USER_1 from HOST_A"],
        correlated=False, window_source_count=1,
    )
    base.update(kw)
    return ModelInput(**base)


def test_fake_mode_returns_schema_valid_output_without_network(monkeypatch):
    import urllib.request

    def boom(*a, **k):
        raise AssertionError("network call attempted in fake mode")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    client = OllamaClient(fake=True)
    out = client.generate(mi("ssh.auth.brute_force"))
    assert isinstance(out, OllamaOutput)
    OllamaOutput.model_validate(out.model_dump(), context={"tokens": {"HOST_A", "HOST_B", "USER_1", "USER_2"}})


@pytest.mark.parametrize("signature", ["ssh.auth.brute_force", "ssh.auth.success", "net.portscan", "dns.blocked", "zz.unknown"])
def test_fake_outputs_pass_tts_rules_for_every_signature(signature):
    out = OllamaClient(fake=True).generate(mi(signature))
    assert validate_tts(out.tts_summary, {"HOST_A", "HOST_B", "USER_1", "USER_2"}) == []


def test_fake_outputs_vary_by_signature():
    client = OllamaClient(fake=True)
    actions = {sig: client.generate(mi(sig)).action for sig in
               ("ssh.auth.brute_force", "ssh.auth.success", "net.portscan", "dns.blocked")}
    assert len(set(actions.values())) >= 3
    assert actions["ssh.auth.brute_force"] == "block_ip"


def test_fake_action_params_reference_tokens_not_values():
    out = OllamaClient(fake=True).generate(mi("ssh.auth.brute_force", src_tokens=["HOST_Q"]))
    assert out.action_params["target"] == "HOST_Q"


def test_unknown_signature_escalates_with_reason():
    out = OllamaClient(fake=True).generate(mi("zz.unknown"))
    assert out.escalate is True and out.escalate_reason


def test_prompt_contains_only_what_it_was_given():
    client = OllamaClient(fake=True)
    prompt = client.build_prompt(mi("net.portscan", sample_lines=["SRC=HOST_A DST=HOST_B DPT=22"]))
    assert "net.portscan" in prompt
    assert "SRC=HOST_A DST=HOST_B DPT=22" in prompt
    assert "format" not in prompt  # prompt is the user turn, not the request envelope


def test_real_request_body_shape():
    client = OllamaClient(base_url="http://127.0.0.1:11434", model="qwen2.5-coder:14b", keep_alive="30m")
    body = json.loads(client.request_body("hello"))
    assert body["format"] == "json"
    assert body["keep_alive"] == "30m"
    assert body["stream"] is False
    assert body["model"] == "qwen2.5-coder:14b"
    assert body["prompt"] == "hello"
    assert client.endpoint == "http://127.0.0.1:11434/api/generate"
