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
    client = OllamaClient(base_url="http://127.0.0.1:11434", model="qwen2.5:14b", keep_alive="30m")
    messages = client.build_messages(mi("net.portscan"))
    body = json.loads(client.request_body(messages))
    assert body["format"] == "json"
    assert body["keep_alive"] == "30m"
    assert body["stream"] is False
    assert body["model"] == "qwen2.5:14b"
    assert client.endpoint == "http://127.0.0.1:11434/api/chat"
    # section 1 of prompts/model-prompt.md, verbatim
    assert body["options"] == {"temperature": 0.2, "top_p": 0.9, "num_predict": 400, "num_ctx": 4096}
    # system, six few-shot pairs as prior turns, then the real event
    assert messages[0]["role"] == "system"
    assert [m["role"] for m in messages[1:-1]] == ["user", "assistant"] * 6
    assert messages[-1]["role"] == "user"
    assert json.loads(messages[-1]["content"])["signature"] == "net.portscan"


def test_defaults_come_from_prompt_file():
    client = OllamaClient()
    assert client.model == "qwen2.5:14b"
    assert client.keep_alive == "30m"
    assert client.timeout_sec == 15.0


def test_input_envelope_matches_section_2():
    env = mi("ssh.auth.brute_force", sample_lines=["a", "b", "c", "d"], novel_signature=True).to_dict()
    assert set(env) == {"signature", "raw_count", "window_sec", "sources", "targets", "users", "ports",
                        "correlated", "novel_signature", "samples"}
    assert env["window_sec"] == 30
    assert env["samples"] == ["a", "b", "c"]  # capped at 3
    assert env["novel_signature"] is True
