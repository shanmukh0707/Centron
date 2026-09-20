"""Local-model client. Only ever sees redacted ModelInput; returns OllamaOutput.

--fake-llm: canned, schema-valid outputs keyed on signature, zero network.
Real path: POST {base_url}/api/chat with the system prompt, the few-shot turns
and the retry template all read from prompts/model-prompt.md. That file is the
source of truth; nothing prompt-shaped is hand-copied into Python.

Failure modes the pipeline must handle (both are terminal for one event):
  SchemaFailure     two attempts, neither parsed into OllamaOutput
  OllamaUnreachable connection error or the 15s hard timeout. HOMELAB: Ollama
                    runs on another box on the LAN that will be asleep at some
                    point; the pipeline must never hang on it.
Tests never call the real path.
"""

from __future__ import annotations

import json
import logging
import re
import socket
import statistics
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from schemas import OllamaOutput

log = logging.getLogger("sentinel.llm")

HERE = Path(__file__).resolve().parent
PROMPT_FILE = HERE / "prompts" / "model-prompt.md"

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen2.5:14b"
DEFAULT_KEEP_ALIVE = "30m"
HARD_TIMEOUT_SEC = 15.0
SAMPLES_CAP = 3
LATENCY_WINDOW = 20
LATENCY_WARN_P50_SEC = 8.0


class SchemaFailure(RuntimeError):
    """The model failed to produce a valid OllamaOutput twice in a row."""


class OllamaUnreachable(RuntimeError):
    """Connection refused, DNS failure, or the hard timeout fired."""


# ----------------------------------------------------------- prompt file
@dataclass(frozen=True)
class PromptSpec:
    """Everything llm.py takes from prompts/model-prompt.md."""

    model: str
    options: dict[str, Any]
    keep_alive: str
    system: str
    few_shots: list[dict[str, str]]  # alternating user/assistant, content is JSON text
    retry_template: str

    @property
    def few_shot_examples(self) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        pairs = []
        for i in range(0, len(self.few_shots) - 1, 2):
            pairs.append((json.loads(self.few_shots[i]["content"]), json.loads(self.few_shots[i + 1]["content"])))
        return pairs


_SECTION_RE = re.compile(r"^## (\d+)\. (.+?)\s*$", re.M)
_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.S)


def _sections(text: str) -> dict[int, str]:
    marks = list(_SECTION_RE.finditer(text))
    out: dict[int, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out[int(m.group(1))] = text[m.end():end]
    return out


def _fences(section: str, lang: str | None = None) -> list[str]:
    return [body for fence_lang, body in _FENCE_RE.findall(section) if lang is None or fence_lang == lang]


def load_prompt_spec(path: str | Path = PROMPT_FILE) -> PromptSpec:
    """Parse the markdown once. Raises if a section is missing or malformed,
    because a silently empty few-shot list would degrade every triage."""
    text = Path(path).read_text(encoding="utf-8")
    secs = _sections(text)
    for n in (1, 3, 4, 5):
        if n not in secs:
            raise ValueError(f"{path}: section {n} missing")

    call = _fences(secs[1])[0]
    call_json = json.loads(re.sub(r"\bFalse\b", "false", re.sub(r"\bTrue\b", "true", call)))
    system = _fences(secs[3])[0].strip()

    blocks = _fences(secs[4], "json")
    if len(blocks) < 2 or len(blocks) % 2:
        raise ValueError(f"{path}: section 4 must hold user/assistant JSON pairs, found {len(blocks)} blocks")
    few_shots = []
    for i, body in enumerate(blocks):
        obj = json.loads(body)  # validates it is JSON; re-serialised compact
        few_shots.append({"role": "user" if i % 2 == 0 else "assistant",
                          "content": json.dumps(obj, separators=(",", ":"))})

    retry = _fences(secs[5])[0].strip()
    if "{violations}" not in retry:
        raise ValueError(f"{path}: retry template must contain {{violations}}")

    return PromptSpec(
        model=call_json["model"],
        options=dict(call_json.get("options", {})),
        keep_alive=str(call_json.get("keep_alive", DEFAULT_KEEP_ALIVE)),
        system=system,
        few_shots=few_shots,
        retry_template=retry,
    )


# ------------------------------------------------------------ model input
@dataclass(frozen=True)
class ModelInput:
    """Everything the model is allowed to see. Every string here is redacted."""

    signature: str
    raw_count: int
    window_start: str
    window_end: str
    src_tokens: list[str] = field(default_factory=list)
    dst_tokens: list[str] = field(default_factory=list)
    user_tokens: list[str] = field(default_factory=list)
    ports: list[int] = field(default_factory=list)
    sample_lines: list[str] = field(default_factory=list)
    correlated: bool = False
    window_source_count: int = 1
    novel_signature: bool = False
    window_sec: int | None = None

    def _window_sec(self) -> int:
        if self.window_sec is not None:
            return self.window_sec
        try:
            fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
            a = datetime.strptime(self.window_start, fmt).replace(tzinfo=timezone.utc)
            b = datetime.strptime(self.window_end, fmt).replace(tzinfo=timezone.utc)
            return max(int((b - a).total_seconds()), 0)
        except ValueError:
            return 0

    def to_dict(self) -> dict[str, Any]:
        """The input envelope, exactly as prompts/model-prompt.md section 2 documents."""
        return {
            "signature": self.signature,
            "raw_count": self.raw_count,
            "window_sec": self._window_sec(),
            "sources": list(self.src_tokens),
            "targets": list(self.dst_tokens),
            "users": list(self.user_tokens),
            "ports": list(self.ports),
            "correlated": self.correlated,
            "novel_signature": self.novel_signature,
            "samples": list(self.sample_lines)[:SAMPLES_CAP],
        }


# ----------------------------------------------------------------- client
class OllamaClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str | None = None,
        keep_alive: str | None = None,
        fake: bool = False,
        timeout_sec: float = HARD_TIMEOUT_SEC,
        prompt_file: str | Path = PROMPT_FILE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fake = fake
        self.timeout_sec = timeout_sec
        self.spec = load_prompt_spec(prompt_file)
        self.model = model or self.spec.model
        self.keep_alive = keep_alive or self.spec.keep_alive
        self.latencies: deque[float] = deque(maxlen=LATENCY_WINDOW)
        self.calls = 0
        self.retries = 0
        self.failures = 0
        self.last_error: str | None = None

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/api/chat"

    @property
    def status(self) -> str:
        """PipelineStatus-shaped: ok | degraded | unreachable."""
        if self.fake:
            return "ok"
        if self.last_error and self.last_error.startswith("unreachable"):
            return "unreachable"
        if self.last_error or (self.latencies and statistics.median(self.latencies) > LATENCY_WARN_P50_SEC):
            return "degraded"
        return "ok"

    @property
    def p50_latency(self) -> float | None:
        return statistics.median(self.latencies) if self.latencies else None

    # ------------------------------------------------------------ request
    def build_prompt(self, mi: ModelInput) -> str:
        """The user turn: the envelope, nothing else."""
        return json.dumps(mi.to_dict(), separators=(",", ":"), sort_keys=True)

    def build_messages(self, mi: ModelInput, retry_violations: list[str] | None = None,
                       previous: str | None = None) -> list[dict[str, str]]:
        msgs: list[dict[str, str]] = [{"role": "system", "content": self.spec.system}]
        msgs.extend(self.spec.few_shots)
        msgs.append({"role": "user", "content": self.build_prompt(mi)})
        if retry_violations is not None:
            msgs.append({"role": "assistant", "content": previous or "{}"})
            msgs.append({"role": "user", "content": self.spec.retry_template.format(
                violations="\n".join(f"- {v}" for v in retry_violations))})
        return msgs

    def request_body(self, messages: list[dict[str, str]]) -> str:
        return json.dumps({
            "model": self.model,
            "messages": messages,
            "format": "json",
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self.spec.options,
        })

    def generate(self, mi: ModelInput, tokens: set[str] | None = None) -> OllamaOutput:
        if self.fake:
            return fake_output(mi)
        return self._generate_real(mi, tokens or set())

    # --------------------------------------------------------------- real
    def _post(self, body: str) -> str:
        """One HTTP round trip. Returns the assistant content string."""
        req = urllib.request.Request(
            self.endpoint, data=body.encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                envelope = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self.last_error = f"http {e.code}"
            raise OllamaUnreachable(f"ollama returned HTTP {e.code} from {self.endpoint}") from e
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
            self.last_error = f"unreachable: {e}"
            raise OllamaUnreachable(f"ollama unreachable at {self.endpoint}: {e}") from e
        finally:
            self._record_latency(time.monotonic() - t0)
        self.calls += 1
        try:
            return envelope["message"]["content"]
        except (KeyError, TypeError) as e:
            self.last_error = "malformed envelope"
            raise SchemaFailure(f"ollama envelope missing message.content: {envelope!r}"[:300]) from e

    def _record_latency(self, seconds: float) -> None:
        self.latencies.append(seconds)
        p50 = statistics.median(self.latencies)
        if p50 > LATENCY_WARN_P50_SEC:
            log.warning("ollama rolling p50 latency %.1fs exceeds %.0fs (last %.1fs, n=%d)",
                        p50, LATENCY_WARN_P50_SEC, seconds, len(self.latencies))

    @staticmethod
    def _violations(exc: Exception) -> list[str]:
        if isinstance(exc, ValidationError):
            out = []
            for err in exc.errors():
                loc = ".".join(str(p) for p in err.get("loc", ())) or "object"
                out.append(f"{loc}: {err['msg'].removeprefix('Value error, ')}")
            return out
        return [f"response is not a JSON object: {exc}"]

    def _parse(self, content: str, tokens: set[str]) -> OllamaOutput:
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError("top level must be an object")
        return OllamaOutput.model_validate(payload, context={"tokens": tokens})

    def _generate_real(self, mi: ModelInput, tokens: set[str]) -> OllamaOutput:
        first = self._post(self.request_body(self.build_messages(mi)))
        try:
            out = self._parse(first, tokens)
        except (ValidationError, ValueError) as e:
            violations = self._violations(e)
            self.retries += 1
            log.info("ollama output rejected, retrying once: %s", "; ".join(violations)[:300])
            second = self._post(self.request_body(self.build_messages(mi, violations, previous=first)))
            try:
                out = self._parse(second, tokens)
            except (ValidationError, ValueError) as e2:
                self.failures += 1
                self.last_error = "schema failure"
                raise SchemaFailure("; ".join(self._violations(e2))[:300]) from e2
        self.last_error = None
        return out


# ------------------------------------------------------------------ fake
def _first(tokens: list[str], fallback: str) -> str:
    return tokens[0] if tokens else fallback


def fake_output(mi: ModelInput) -> OllamaOutput:
    """Canned output per signature. Params carry tokens, exactly like the real model."""
    src = _first(mi.src_tokens, "HOST_A")
    dst = _first(mi.dst_tokens, "HOST_B")
    users = ", ".join(mi.user_tokens) or "no named user"
    sig = mi.signature

    if sig == "ssh.auth.brute_force":
        data = dict(
            internal_log=(f"{src} made {mi.raw_count} failed SSH password attempts against {dst} "
                          f"as {users} inside one window. Pattern matches ssh.auth.brute_force. "
                          f"Recommending a temporary block of {src}."),
            tts_summary="Repeated failed SSH logins from one outside address are being blocked.",
            severity="high", confidence=0.91, escalate=False, escalate_reason=None,
            action="block_ip", action_params={"target": src, "duration_sec": 3600},
        )
    elif sig == "ssh.auth.success":
        data = dict(
            internal_log=f"{users} logged in over SSH to {dst} from {src}. Single successful login, no failures preceding it.",
            tts_summary="A user signed in successfully over SSH.",
            severity="info", confidence=0.88, escalate=False, escalate_reason=None,
            action="notify_only", action_params={"channel": "digest"},
        )
    elif sig == "net.portscan":
        data = dict(
            internal_log=(f"{src} probed {len(mi.ports)} distinct TCP ports on {dst}; the firewall dropped "
                          f"{mi.raw_count} SYN packets. Looks like a sequential port scan. "
                          f"Recommending a rate limit on {src}."),
            tts_summary="An outside address is scanning the network and is being rate limited.",
            severity="high", confidence=0.86, escalate=False, escalate_reason=None,
            action="rate_limit", action_params={"target": src, "limit_conn_per_min": 20, "duration_sec": 1800},
        )
    elif sig == "dns.blocked":
        data = dict(
            internal_log=(f"{src} had {mi.raw_count} DNS {'query' if mi.raw_count == 1 else 'queries'} blocked by the resolver on {dst}. "
                          f"Consistent with ad or tracker traffic; watching {src} for follow-up."),
            tts_summary="A device on the network had several tracker lookups blocked.",
            severity="low", confidence=0.8, escalate=False, escalate_reason=None,
            action="watch", action_params={"target": src, "duration_sec": 3600},
        )
    else:
        data = dict(
            internal_log=f"Unrecognised signature {sig} from {src} with {mi.raw_count} lines. No playbook selected.",
            tts_summary="Unfamiliar activity was seen and has been queued for review.",
            severity="low", confidence=0.4, escalate=True,
            escalate_reason=f"signature {sig} is not in the known catalog",
            action="notify_only", action_params={"channel": "app"},
        )

    if mi.correlated and not data["escalate"]:
        data["escalate"] = True
        data["escalate_reason"] = f"{mi.window_source_count} distinct sources active in the same window"
    return OllamaOutput.model_validate(data)


class CannedLLM:
    """Test double that answers with pre-built OllamaOutput-shaped dicts (or raises)."""

    def __init__(self, responses: list[Any], tokens: set[str] | None = None) -> None:
        self.responses = list(responses)
        self.tokens = tokens or set()
        self.inputs: list[ModelInput] = []
        self.fake = True
        self.status = "ok"

    def generate(self, mi: ModelInput, tokens: set[str] | None = None) -> OllamaOutput:
        self.inputs.append(mi)
        if not self.responses:
            raise SchemaFailure("canned responses exhausted")
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        if isinstance(nxt, OllamaOutput):
            return nxt
        return OllamaOutput.model_validate(nxt, context={"tokens": tokens if tokens is not None else self.tokens})
