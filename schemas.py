"""Sentinel wire contract (server <-> Android) and playbook/Ollama validation.

Single source of truth for:
  * the WebSocket envelope + discriminated frame union       (section A)
  * EventData / HeartbeatData / AudioReady / ActionUpdate ... (sections B, C)
  * playbook parameter models and their hard caps (CAPS)     (section D)
  * OllamaOutput + tts/internal_log leakage validators       (section E)
  * export_json_schema() -> contract.json for Kotlin codegen (section F)
  * golden sample writer/validator used by the stub server

Nothing here executes anything. No network, no DB, no model calls.

CLI:
    python schemas.py schema  [contract.json]   # dump JSON schema
    python schemas.py samples [samples/]        # (re)write golden samples
    python schemas.py check   [samples/]        # validate golden samples
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from typing import Annotated, Any, Iterable, Literal, Union
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyAddress,
    IPvAnyNetwork,
    PlainSerializer,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    WithJsonSchema,
    field_validator,
    model_validator,
)

PROTOCOL_VERSION = 1
CONTRACT_VERSION = "1.0.0"
RESYNC_BUFFER_MAX = 200

# --------------------------------------------------------------------------- #
# Shared primitives
# --------------------------------------------------------------------------- #


class Strict(BaseModel):
    """Every contract model forbids unknown keys so drift is caught at the edge."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_ts(dt: datetime) -> str:
    """RFC3339, UTC, millisecond precision, trailing Z. e.g. 2026-09-19T12:00:00.000Z"""
    dt = _to_utc(dt)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


_TS_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"

UtcMillis = Annotated[
    datetime,
    AfterValidator(_to_utc),
    PlainSerializer(format_ts, return_type=str, when_used="json"),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": _TS_PATTERN,
            "description": "RFC3339 UTC with millisecond precision, always ends in Z",
            "examples": ["2026-09-19T12:00:00.000Z"],
        }
    ),
]


def uuid7() -> UUID:
    """RFC 9562 UUIDv7: 48-bit unix ms | ver 7 | 12 rand | var 10 | 62 rand.

    Time-ordered so SQLite/Room indexes stay append-friendly. Python < 3.14
    has no uuid.uuid7, hence the helper.
    """
    ms = time.time_ns() // 1_000_000
    value = (
        (ms << 80)
        | (0x7 << 76)
        | (secrets.randbits(12) << 64)
        | (0b10 << 62)
        | secrets.randbits(62)
    )
    return UUID(int=value)


Severity = Literal["info", "low", "high", "critical"]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
Port = Annotated[int, Field(ge=0, le=65535)]
SubsystemState = Literal["ok", "degraded", "unreachable"]
Playbook = Literal[
    "block_ip",
    "rate_limit",
    "isolate_host",
    "watch",
    "notify_only",
    "revoke_session",
    "snapshot_evidence",
]
ActionStatus = Literal[
    "auto_executed",
    "pending_approval",
    "approved",
    "denied",
    "expired",
    "failed",
]
AudioStatus = Literal["ready", "pending", "failed", "suppressed"]
EscalationState = Literal["none", "pending", "answered", "unreachable"]

# Identifier shapes. No free text sneaks through these.
_ID_RE = r"^[A-Za-z0-9._:-]{1,128}$"
HostId = Annotated[str, Field(pattern=_ID_RE)]
UserId = Annotated[str, Field(pattern=r"^[A-Za-z0-9._@-]{1,128}$")]
ShortId = Annotated[str, Field(pattern=_ID_RE)]

# --------------------------------------------------------------------------- #
# D. Playbook caps -- the ONLY place a cap value lives
# --------------------------------------------------------------------------- #

CAPS: dict[str, dict[str, Any]] = {
    "block_ip": {
        "min_prefixlen": 24,  # never broader than /24 (IPv6: same host count, /120)
        "auto_max_addresses": 1,  # auto path: a single host
        "auto_public_only": True,  # auto path: public (global) addresses only
        "auto_max_duration_sec": 3600,
        "approved_max_duration_sec": 86400,
        "reversible": True,
    },
    "rate_limit": {
        "min_prefixlen": 24,
        "auto_max_addresses": 1,
        "auto_max_duration_sec": 1800,
        "approved_max_duration_sec": 7200,
        "min_limit_conn_per_min": 10,
        "reversible": True,
    },
    "isolate_host": {
        "max_hosts": 1,
        "max_duration_sec": 3600,
        "requires_approval": True,
        "reversible": True,
    },
    "watch": {
        "max_duration_sec": 86400,
        "requires_approval": False,
        "read_only": True,
    },
    "notify_only": {
        "requires_approval": False,
        "read_only": True,
        "free_text_allowed": False,
    },
    "revoke_session": {
        "requires_approval": True,
        "reversible": False,
    },
    "snapshot_evidence": {
        "max_window_sec": 3600,
        "requires_approval": False,
        "read_only": True,
    },
}

# Assets no playbook may ever target. IPs, CIDRs or host ids. Deployment can
# extend this set at import time; validators read it live.
PROTECTED_ASSETS: set[str] = {
    "10.0.0.1",  # gateway
    "10.0.0.10",  # domain controller
    "10.0.0.53",  # internal DNS
    "192.168.1.1",  # gateway (home/SMB default)
    "127.0.0.0/8",
    "::1",
    "gw01",
    "dc01",
    "dns01",
    "nas01",
    "sentinel-server",
}


def _protected_networks() -> list[IPv4Network | IPv6Network]:
    nets = []
    for item in PROTECTED_ASSETS:
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return nets


def _protected_host_ids() -> set[str]:
    ids = set()
    for item in PROTECTED_ASSETS:
        try:
            ipaddress.ip_network(item, strict=False)
        except ValueError:
            ids.add(item.lower())
    return ids


def is_protected(target: str | IPv4Network | IPv6Network) -> bool:
    """True if `target` (host id, IP or CIDR) touches any protected asset."""
    if isinstance(target, str):
        if target.lower() in _protected_host_ids():
            return True
        try:
            net = ipaddress.ip_network(target, strict=False)
        except ValueError:
            return False
    else:
        net = target
    return any(
        net.version == p.version and (net.overlaps(p) or p.overlaps(net))
        for p in _protected_networks()
    )


def _min_prefixlen_for(net: IPv4Network | IPv6Network, cap_v4: int) -> int:
    """Translate an IPv4 '/N' breadth cap into the same host count for IPv6."""
    return cap_v4 + (net.max_prefixlen - 32)


def _check_breadth(net: IPv4Network | IPv6Network, playbook: str) -> None:
    floor = _min_prefixlen_for(net, CAPS[playbook]["min_prefixlen"])
    if net.prefixlen < floor:
        raise ValueError(
            f"{playbook}: prefix /{net.prefixlen} broader than allowed floor /{floor}"
        )


def _check_not_protected(target: Any, playbook: str) -> None:
    if is_protected(target):
        raise ValueError(f"{playbook}: target {target} is a protected asset")


class BlockIpParams(Strict):
    playbook: Literal["block_ip"] = "block_ip"
    target: IPvAnyNetwork
    duration_sec: int = Field(gt=0)

    @model_validator(mode="after")
    def _caps(self) -> "BlockIpParams":
        cap = CAPS["block_ip"]
        _check_breadth(self.target, "block_ip")
        _check_not_protected(self.target, "block_ip")
        if self.duration_sec > cap["approved_max_duration_sec"]:
            raise ValueError(
                f"block_ip: duration_sec {self.duration_sec} exceeds approved cap "
                f"{cap['approved_max_duration_sec']}"
            )
        return self


class RateLimitParams(Strict):
    playbook: Literal["rate_limit"] = "rate_limit"
    target: IPvAnyNetwork
    limit_conn_per_min: int
    duration_sec: int = Field(gt=0)

    @model_validator(mode="after")
    def _caps(self) -> "RateLimitParams":
        cap = CAPS["rate_limit"]
        _check_breadth(self.target, "rate_limit")
        _check_not_protected(self.target, "rate_limit")
        if self.limit_conn_per_min < cap["min_limit_conn_per_min"]:
            raise ValueError(
                f"rate_limit: limit_conn_per_min {self.limit_conn_per_min} below floor "
                f"{cap['min_limit_conn_per_min']}"
            )
        if self.duration_sec > cap["approved_max_duration_sec"]:
            raise ValueError(
                f"rate_limit: duration_sec {self.duration_sec} exceeds approved cap "
                f"{cap['approved_max_duration_sec']}"
            )
        return self


class IsolateHostParams(Strict):
    playbook: Literal["isolate_host"] = "isolate_host"
    host_id: HostId
    duration_sec: int = Field(gt=0)

    @field_validator("host_id")
    @classmethod
    def _single_host_not_cidr(cls, v: str) -> str:
        if "/" in v or "," in v:
            raise ValueError("isolate_host: host_id must be exactly one host, never a CIDR or list")
        try:
            net = ipaddress.ip_network(v, strict=False)
        except ValueError:
            return v  # a hostname / asset id, fine
        if net.num_addresses != 1:
            raise ValueError("isolate_host: host_id must resolve to a single host")
        return v

    @model_validator(mode="after")
    def _caps(self) -> "IsolateHostParams":
        cap = CAPS["isolate_host"]
        _check_not_protected(self.host_id, "isolate_host")
        if self.duration_sec > cap["max_duration_sec"]:
            raise ValueError(
                f"isolate_host: duration_sec {self.duration_sec} exceeds cap {cap['max_duration_sec']}"
            )
        return self


class WatchParams(Strict):
    """Read-only. Adds a target to the hot list so future events correlate faster."""

    playbook: Literal["watch"] = "watch"
    target: HostId
    duration_sec: int = Field(gt=0)

    @model_validator(mode="after")
    def _caps(self) -> "WatchParams":
        cap = CAPS["watch"]
        if self.duration_sec > cap["max_duration_sec"]:
            raise ValueError(
                f"watch: duration_sec {self.duration_sec} exceeds cap {cap['max_duration_sec']}"
            )
        return self


class NotifyOnlyParams(Strict):
    """No free-text field by design: the model cannot smuggle prose into the app."""

    playbook: Literal["notify_only"] = "notify_only"
    channel: Literal["app", "digest"] = "app"


class RevokeSessionParams(Strict):
    playbook: Literal["revoke_session"] = "revoke_session"
    user: UserId
    session_id: ShortId | None = None


class SnapshotEvidenceParams(Strict):
    """Read-only: copies the relevant log window aside for later review."""

    playbook: Literal["snapshot_evidence"] = "snapshot_evidence"
    scope: Literal["host", "network", "auth"]
    target: HostId
    window_sec: int = Field(gt=0)

    @model_validator(mode="after")
    def _caps(self) -> "SnapshotEvidenceParams":
        cap = CAPS["snapshot_evidence"]
        if self.window_sec > cap["max_window_sec"]:
            raise ValueError(
                f"snapshot_evidence: window_sec {self.window_sec} exceeds cap {cap['max_window_sec']}"
            )
        return self


PlaybookParams = Annotated[
    Union[
        BlockIpParams,
        RateLimitParams,
        IsolateHostParams,
        WatchParams,
        NotifyOnlyParams,
        RevokeSessionParams,
        SnapshotEvidenceParams,
    ],
    Field(discriminator="playbook"),
]
PlaybookParamsAdapter: TypeAdapter[Any] = TypeAdapter(PlaybookParams)

PLAYBOOK_MODELS: dict[str, type[Strict]] = {
    "block_ip": BlockIpParams,
    "rate_limit": RateLimitParams,
    "isolate_host": IsolateHostParams,
    "watch": WatchParams,
    "notify_only": NotifyOnlyParams,
    "revoke_session": RevokeSessionParams,
    "snapshot_evidence": SnapshotEvidenceParams,
}


def _assert_no_free_text(model: type[BaseModel]) -> None:
    for name, f in model.model_fields.items():
        if f.annotation is str:
            raise AssertionError(f"{model.__name__}.{name} is free text; not allowed")


_assert_no_free_text(NotifyOnlyParams)
assert set(PLAYBOOK_MODELS) == set(CAPS) == set(Playbook.__args__)  # type: ignore[attr-defined]


def _auto_eligible_network(net: IPv4Network | IPv6Network, playbook: str) -> tuple[bool, str]:
    cap = CAPS[playbook]
    if net.num_addresses > cap["auto_max_addresses"]:
        return False, f"prefix /{net.prefixlen} is not a single host"
    if cap.get("auto_public_only") and not net.network_address.is_global:
        return False, f"{net.network_address} is not a public address"
    return True, ""


def _classify(p: Any) -> tuple[bool, str]:
    """Returns (requires_approval, reason) for a validated params model."""
    cap = CAPS[p.playbook]
    if p.playbook in ("block_ip", "rate_limit"):
        ok, why = _auto_eligible_network(p.target, p.playbook)
        if not ok:
            return True, f"{p.playbook}: {why}; approval required"
        if p.duration_sec > cap["auto_max_duration_sec"]:
            return True, (
                f"{p.playbook}: duration_sec {p.duration_sec} exceeds auto cap "
                f"{cap['auto_max_duration_sec']}; approval required"
            )
        return False, f"{p.playbook}: single public host for {p.duration_sec}s within auto cap"
    if cap.get("requires_approval"):
        return True, f"{p.playbook}: always requires approval"
    return False, f"{p.playbook}: {'read-only' if cap.get('read_only') else 'auto'} playbook"


def requires_approval(params: Any) -> bool:
    """Approval decision for a params model (or a dict, which is validated first)."""
    p = params if isinstance(params, BaseModel) else PlaybookParamsAdapter.validate_python(params)
    return _classify(p)[0]


def validate_and_classify(
    params: Any, playbook: str | None = None
) -> tuple[bool, bool, str]:
    """(allowed, requires_approval, reason).

    `params` may be a dict straight from the model's action_params (after token
    resolution) or an already-built params model. If `playbook` is given and the
    dict lacks a `playbook` key, it is injected so the discriminator resolves.
    Disallowed params always report requires_approval=True: nothing invalid can
    ever be mistaken for auto-safe.
    """
    if isinstance(params, BaseModel):
        p = params
    else:
        data = dict(params)
        if playbook is not None:
            if "playbook" in data and data["playbook"] != playbook:
                return False, True, f"playbook mismatch: {data['playbook']!r} != {playbook!r}"
            data.setdefault("playbook", playbook)
        try:
            p = PlaybookParamsAdapter.validate_python(data)
        except ValidationError as e:
            msgs = "; ".join(err["msg"].removeprefix("Value error, ") for err in e.errors())
            return False, True, msgs
    needs, reason = _classify(p)
    return True, needs, reason


# --------------------------------------------------------------------------- #
# E. Leakage validators (used by OllamaOutput and EventData)
# --------------------------------------------------------------------------- #

TTS_MAX_WORDS = 20
TTS_MAX_CHARS = 140

_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# Full form (2+ colon groups) or any form containing "::". Also catches hh:mm:ss
# times, which is acceptable: clock literals do not belong in a spoken summary.
_IPV6_RE = re.compile(
    r"(?<![\w:])(?:"
    r"(?:[0-9A-Fa-f]{1,4}:){2,7}[0-9A-Fa-f]{0,4}"
    r"|(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4})*)?"
    r")(?![\w:])"
)
_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
# ":22", "port 8080", or any bare 4-5 digit group.
_PORTISH_RE = re.compile(r"(?::\d{1,5}\b)|(?i:\bports?\s*#?\s*\d{1,5}\b)|(?:\b\d{4,5}\b)")
_SENTENCE_END_RE = re.compile(r"[.!?]")


def _literal_violations(text: str) -> list[str]:
    out = []
    if _IPV4_RE.search(text):
        out.append("contains IPv4 literal")
    if _IPV6_RE.search(text):
        out.append("contains IPv6 literal")
    if _MAC_RE.search(text):
        out.append("contains MAC address")
    if "@" in text:
        out.append("contains '@' (email/handle)")
    return out


def validate_internal_log(text: str) -> list[str]:
    """OllamaOutput side: the model only ever sees tokens, so no literal may appear."""
    return _literal_violations(text)


# Redaction token shapes (mirrors redaction.py). EventData is post-localization:
# the phone gets real values, so a leftover token means localize() was skipped.
_TOKEN_RE = re.compile(r"\b(?:HOST_[A-Z]+|USER_\d+|MAC_\d+)\b")


def validate_no_tokens(text: str) -> list[str]:
    """EventData side: every redaction token must have been localized away."""
    return [f"contains unlocalized token {tok!r}" for tok in sorted(set(_TOKEN_RE.findall(text)))]


def validate_tts(summary: str, tokens: Iterable[str] = ()) -> list[str]:
    """Return every rule the summary breaks, so the retry prompt can name them."""
    v: list[str] = []
    words = summary.split()
    if not words:
        v.append("empty summary")
    if len(words) > TTS_MAX_WORDS:
        v.append(f"{len(words)} words > {TTS_MAX_WORDS}")
    if len(summary) > TTS_MAX_CHARS:
        v.append(f"{len(summary)} chars > {TTS_MAX_CHARS}")
    ends = _SENTENCE_END_RE.findall(summary)
    if len(ends) != 1:
        v.append(f"{len(ends)} sentence-ending marks, need exactly 1")
    elif not summary.rstrip().endswith(ends[0]):
        v.append("sentence-ending mark is not at the end")
    v.extend(_literal_violations(summary))
    if _PORTISH_RE.search(summary):
        v.append("contains a port-like digit group")
    for tok in tokens:
        if tok and tok in summary:
            v.append(f"contains redaction token {tok!r}")
    return v


def _tts_field(v: str, info: ValidationInfo | None) -> str:
    tokens = ((info.context or {}) if info else {}).get("tokens", ())
    problems = validate_tts(v, tokens)
    if problems:
        raise ValueError("tts_summary: " + "; ".join(problems))
    return v


def _internal_log_field(v: str) -> str:
    problems = validate_internal_log(v)
    if problems:
        raise ValueError("internal_log: " + "; ".join(problems))
    return v


def _localized_log_field(v: str) -> str:
    problems = validate_no_tokens(v)
    if problems:
        raise ValueError("internal_log: " + "; ".join(problems))
    return v


TtsSummary = Annotated[str, Field(min_length=1, max_length=TTS_MAX_CHARS)]
InternalLog = Annotated[str, Field(min_length=1, max_length=600)]


class OllamaOutput(Strict):
    """Exact JSON shape the local model must return (format="json").

    Validate with  OllamaOutput.model_validate(obj, context={"tokens": token_set})
    so the tts_summary check can reject redaction tokens (HOST_A, USER_1, ...).
    action_params carries *tokens*, not real values; Python resolves them and
    then runs validate_and_classify() on the resolved dict.
    """

    internal_log: InternalLog
    tts_summary: TtsSummary
    severity: Severity
    confidence: Confidence
    escalate: bool
    escalate_reason: str | None = Field(default=None, max_length=300)
    action: Playbook
    action_params: dict[str, int | str | bool] = Field(default_factory=dict)

    @field_validator("internal_log")
    @classmethod
    def _log(cls, v: str) -> str:
        return _internal_log_field(v)

    @field_validator("tts_summary")
    @classmethod
    def _tts(cls, v: str, info: ValidationInfo) -> str:
        return _tts_field(v, info)

    @model_validator(mode="after")
    def _escalation_reason(self) -> "OllamaOutput":
        if self.escalate and not (self.escalate_reason or "").strip():
            raise ValueError("escalate_reason must be non-empty when escalate is true")
        if not self.escalate and self.escalate_reason is not None:
            raise ValueError("escalate_reason must be null when escalate is false")
        if "playbook" in self.action_params and self.action_params["playbook"] != self.action:
            raise ValueError("action_params.playbook disagrees with action")
        return self


# --------------------------------------------------------------------------- #
# B. EventData and its parts
# --------------------------------------------------------------------------- #


class Entities(Strict):
    src_ips: list[IPvAnyAddress] = Field(default_factory=list)
    dst_hosts: list[HostId] = Field(default_factory=list)
    users: list[UserId] = Field(default_factory=list)
    ports: list[Port] = Field(default_factory=list)


class Source(Strict):
    kind: str = Field(max_length=32, examples=["auth", "firewall", "dns", "proxy"])
    collector: str = Field(max_length=64, examples=["sshd", "pfsense", "unbound"])
    raw_count: int = Field(ge=1, description="raw log lines collapsed into this event")


class Window(Strict):
    start: UtcMillis
    end: UtcMillis

    @model_validator(mode="after")
    def _ordered(self) -> "Window":
        if self.end < self.start:
            raise ValueError("window.end precedes window.start")
        return self


class AudioRef(Strict):
    status: AudioStatus
    audio_id: ShortId
    url: str | None = None
    mime: Literal["audio/mpeg"] | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    cache_key: ShortId | None = None

    @model_validator(mode="after")
    def _ready_is_complete(self) -> "AudioRef":
        if self.status == "ready":
            missing = [k for k in ("url", "mime", "duration_ms", "sha256") if getattr(self, k) is None]
            if missing:
                raise ValueError(f"audio.status=ready but missing {missing}")
        return self


EscalationGate = Literal[
    "unknown_pattern",
    "novelty",
    "correlation",
    "schema_failure",
    "blast_radius",
    "severity_floor",
    "low_confidence",
    "model_self_report",
]


class Escalation(Strict):
    escalated: bool
    gate: EscalationGate | None = None
    reason: str | None = Field(default=None, max_length=300)
    state: EscalationState = "none"
    verdict: str | None = Field(default=None, max_length=600)

    @model_validator(mode="after")
    def _consistent(self) -> "Escalation":
        if self.escalated and self.state == "none":
            raise ValueError("escalated=true requires state != none")
        if not self.escalated and self.state != "none":
            raise ValueError("escalated=false requires state == none")
        if self.escalated and self.gate is None:
            raise ValueError("escalated=true requires a gate")
        if self.verdict is not None and self.state != "answered":
            raise ValueError("verdict only valid when state == answered")
        return self


class ActionRef(Strict):
    playbook: Playbook
    params: PlaybookParams
    status: ActionStatus
    requires_approval: bool
    expires_at: UtcMillis | None = None
    approval_id: ShortId | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "ActionRef":
        if self.params.playbook != self.playbook:
            raise ValueError("action.playbook disagrees with action.params.playbook")
        if requires_approval(self.params) and not self.requires_approval:
            raise ValueError(f"{self.playbook} with these params requires approval")
        if self.status == "auto_executed" and self.requires_approval:
            raise ValueError("auto_executed action cannot require approval")
        if self.status == "pending_approval" and self.approval_id is None:
            raise ValueError("pending_approval requires approval_id")
        if self.status == "pending_approval" and self.expires_at is None:
            raise ValueError("pending_approval requires expires_at")
        return self


class EventData(Strict):
    event_id: UUID = Field(default_factory=uuid7)
    signature: ShortId = Field(description="stable pattern key, e.g. ssh.bruteforce")
    severity: Severity
    title: str = Field(min_length=1, max_length=60)
    internal_log: InternalLog
    tts_summary: TtsSummary
    confidence: Confidence
    entities: Entities = Field(default_factory=Entities)
    source: Source
    window: Window
    audio: AudioRef | None = None
    escalation: Escalation = Field(default_factory=lambda: Escalation(escalated=False))
    action: ActionRef | None = None
    reviewed: bool = False

    @field_validator("tts_summary")
    @classmethod
    def _tts(cls, v: str, info: ValidationInfo) -> str:
        return _tts_field(v, info)

    @field_validator("internal_log")
    @classmethod
    def _log(cls, v: str) -> str:
        return _localized_log_field(v)


# --------------------------------------------------------------------------- #
# C. Other frame payloads
# --------------------------------------------------------------------------- #


class PipelineStatus(Strict):
    log_source: SubsystemState = "ok"
    ollama: SubsystemState = "ok"
    tts: SubsystemState = "ok"
    claude: SubsystemState = "ok"


class HeartbeatData(Strict):
    interval_s: int = Field(gt=0)
    uptime_s: int = Field(ge=0)
    last_event_seq: int = Field(ge=0)
    queue_depth: int = Field(ge=0)
    pipeline: PipelineStatus


class AudioReadyData(Strict):
    event_id: UUID
    audio: AudioRef


class ActionUpdateData(Strict):
    event_id: UUID
    playbook: Playbook
    status: ActionStatus
    approval_id: ShortId | None = None
    expires_at: UtcMillis | None = None
    reason: str | None = Field(default=None, max_length=200)


class HelloData(Strict):
    server_id: ShortId
    contract_version: str = CONTRACT_VERSION
    heartbeat_interval_s: int = Field(gt=0)
    last_event_seq: int = Field(ge=0)
    resync_buffer_max: int = RESYNC_BUFFER_MAX


class AckData(Strict):
    seq: int = Field(ge=0, description="seq of the frame being acknowledged")
    event_id: UUID | None = None


class ApproveActionData(Strict):
    approval_id: ShortId
    event_id: UUID
    decision: Literal["approved", "denied"]
    biometric: bool = Field(description="true only after AndroidX Biometric succeeded")


class ResyncData(Strict):
    since_seq: int = Field(ge=0, description="last seq the client saw; 0 = everything buffered")


# --------------------------------------------------------------------------- #
# A. Envelope + discriminated union
# --------------------------------------------------------------------------- #


class _Frame(Strict):
    v: int = PROTOCOL_VERSION
    type: str
    seq: int = Field(ge=0)
    ts: UtcMillis
    data: Any

    @field_validator("v")
    @classmethod
    def _version(cls, v: int) -> int:
        if v != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version {v}")
        return v


class EventFrame(_Frame):
    type: Literal["event"] = "event"
    data: EventData


class HeartbeatFrame(_Frame):
    type: Literal["heartbeat"] = "heartbeat"
    data: HeartbeatData


class AudioReadyFrame(_Frame):
    type: Literal["audio_ready"] = "audio_ready"
    data: AudioReadyData


class ActionUpdateFrame(_Frame):
    type: Literal["action_update"] = "action_update"
    data: ActionUpdateData


class HelloFrame(_Frame):
    type: Literal["hello"] = "hello"
    data: HelloData


class AckFrame(_Frame):
    type: Literal["ack"] = "ack"
    data: AckData


class ApproveActionFrame(_Frame):
    type: Literal["approve_action"] = "approve_action"
    data: ApproveActionData


class ResyncFrame(_Frame):
    type: Literal["resync"] = "resync"
    data: ResyncData


ServerFrame = Annotated[
    Union[EventFrame, HeartbeatFrame, AudioReadyFrame, ActionUpdateFrame, HelloFrame],
    Field(discriminator="type"),
]
ClientFrame = Annotated[
    Union[AckFrame, ApproveActionFrame, ResyncFrame],
    Field(discriminator="type"),
]
Frame = Annotated[
    Union[
        EventFrame,
        HeartbeatFrame,
        AudioReadyFrame,
        ActionUpdateFrame,
        HelloFrame,
        AckFrame,
        ApproveActionFrame,
        ResyncFrame,
    ],
    Field(discriminator="type"),
]

FrameAdapter: TypeAdapter[Any] = TypeAdapter(Frame)
ServerFrameAdapter: TypeAdapter[Any] = TypeAdapter(ServerFrame)
ClientFrameAdapter: TypeAdapter[Any] = TypeAdapter(ClientFrame)

FRAME_TYPES: dict[str, type[_Frame]] = {
    "event": EventFrame,
    "heartbeat": HeartbeatFrame,
    "audio_ready": AudioReadyFrame,
    "action_update": ActionUpdateFrame,
    "hello": HelloFrame,
    "ack": AckFrame,
    "approve_action": ApproveActionFrame,
    "resync": ResyncFrame,
}
SERVER_FRAME_TYPES = ("event", "heartbeat", "audio_ready", "action_update", "hello")
CLIENT_FRAME_TYPES = ("ack", "approve_action", "resync")


def make_frame(type_: str, data: Any, seq: int, ts: datetime | None = None) -> _Frame:
    """Wrap a payload in its envelope. `data` may be a model or a dict."""
    return FRAME_TYPES[type_](seq=seq, ts=ts or now_utc(), data=data)


def parse_frame(raw: str | bytes) -> _Frame:
    return FrameAdapter.validate_json(raw)


def parse_client_frame(raw: str | bytes) -> _Frame:
    return ClientFrameAdapter.validate_json(raw)


def dump_frame(frame: _Frame, indent: int | None = None) -> str:
    return frame.model_dump_json(indent=indent)


# --------------------------------------------------------------------------- #
# F. JSON schema export
# --------------------------------------------------------------------------- #


def build_json_schema() -> dict[str, Any]:
    ref = "#/$defs/{model}"
    schema = FrameAdapter.json_schema(mode="serialization", ref_template=ref)
    extra = TypeAdapter(OllamaOutput).json_schema(mode="validation", ref_template=ref)
    defs = schema.setdefault("$defs", {})
    for name, d in extra.pop("$defs", {}).items():
        if name in defs and defs[name] != d:
            raise RuntimeError(f"$defs collision while merging schema: {name}")
        defs.setdefault(name, d)
    defs["OllamaOutput"] = extra
    root: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://sentinel.local/contract.json",
        "title": "SentinelFrame",
        "description": (
            "Sentinel WebSocket envelope. Discriminate on `type`. "
            "Server->client: event, heartbeat, audio_ready, action_update, hello. "
            "Client->server: ack, approve_action, resync. "
            "$defs.OllamaOutput is the local-model JSON shape (not a frame)."
        ),
        "x-sentinel": {
            "contract_version": CONTRACT_VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "server_frame_types": list(SERVER_FRAME_TYPES),
            "client_frame_types": list(CLIENT_FRAME_TYPES),
            "resync_buffer_max": RESYNC_BUFFER_MAX,
            "tts_max_words": TTS_MAX_WORDS,
            "tts_max_chars": TTS_MAX_CHARS,
            "caps": CAPS,
            "protected_assets": sorted(PROTECTED_ASSETS),
        },
    }
    root.update(schema)
    return root


def export_json_schema(path: str | Path = "contract.json") -> Path:
    """Write the full envelope union (plus OllamaOutput) as JSON Schema 2020-12.

    Android: quicktype --lang kotlin --framework kotlinx -s schema contract.json
    """
    path = Path(path)
    path.write_text(json.dumps(build_json_schema(), indent=2) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Golden samples (one per frame type), built from the models themselves
# --------------------------------------------------------------------------- #

_T0 = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
_EVENT_ID = UUID("0199612e-b400-7000-8000-00000000c0de")  # v7-shaped, fixed for diffable goldens
_SHA = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def build_samples() -> dict[str, _Frame]:
    audio = AudioRef(
        status="ready",
        audio_id="aud_0199612eb400",
        url="http://192.168.1.50:8765/audio/aud_0199612eb400.mp3",
        mime="audio/mpeg",
        duration_ms=1000,
        sha256=_SHA,
        cache_key="tts_1f3a9c",
    )
    ev = EventData(
        event_id=_EVENT_ID,
        signature="ssh.bruteforce",
        severity="high",
        title="SSH brute force against bastion",
        internal_log=(
            "185.220.101.34 attempted 47 SSH logins as svc-backup and root against bastion in 60s; "
            "all failed. Pattern matches ssh.bruteforce. Recommending block_ip on 185.220.101.34."
        ),
        tts_summary="Repeated failed SSH logins from one outside address are waiting for your approval to block.",
        confidence=0.91,
        entities=Entities(
            src_ips=["185.220.101.34"], dst_hosts=["bastion"], users=["svc-backup", "root"], ports=[22]
        ),
        source=Source(kind="auth", collector="sshd", raw_count=47),
        window=Window(start=_T0.replace(hour=11, minute=59), end=_T0),
        audio=audio,
        escalation=Escalation(escalated=False),
        action=ActionRef(
            playbook="block_ip",
            params=BlockIpParams(target="185.220.101.34/32", duration_sec=14400),
            status="pending_approval",
            requires_approval=True,
            expires_at=_T0.replace(minute=2),
            approval_id="apr_0199612eb401",
        ),
        reviewed=False,
    )
    hb = HeartbeatData(
        interval_s=10,
        uptime_s=3600,
        last_event_seq=42,
        queue_depth=0,
        pipeline=PipelineStatus(log_source="ok", ollama="ok", tts="degraded", claude="ok"),
    )
    ar = AudioReadyData(event_id=_EVENT_ID, audio=audio)
    au = ActionUpdateData(
        event_id=_EVENT_ID,
        playbook="block_ip",
        status="approved",
        approval_id="apr_0199612eb401",
        expires_at=_T0.replace(hour=16),
        reason="approved on device with biometric",
    )
    hello = HelloData(server_id="sentinel-stub-01", heartbeat_interval_s=10, last_event_seq=42)
    ack = AckData(seq=43, event_id=_EVENT_ID)
    approve = ApproveActionData(
        approval_id="apr_0199612eb401", event_id=_EVENT_ID, decision="approved", biometric=True
    )
    resync = ResyncData(since_seq=40)

    payloads: list[tuple[str, Any]] = [
        ("hello", hello),
        ("heartbeat", hb),
        ("event", ev),
        ("audio_ready", ar),
        ("action_update", au),
        ("ack", ack),
        ("approve_action", approve),
        ("resync", resync),
    ]
    return {t: make_frame(t, d, seq=i + 1, ts=_T0) for i, (t, d) in enumerate(payloads)}


def write_samples(samples_dir: str | Path = "samples") -> list[Path]:
    d = Path(samples_dir)
    d.mkdir(parents=True, exist_ok=True)
    written = []
    for type_, frame in build_samples().items():
        p = d / f"{type_}.json"
        p.write_text(dump_frame(frame, indent=2) + "\n", encoding="utf-8")
        written.append(p)
    return written


def validate_samples(samples_dir: str | Path = "samples") -> dict[str, _Frame]:
    """Parse every samples/*.json through the union. Raises on the first problem.

    Also insists that one file exists per frame type and that each file's
    `type` matches its filename, so a renamed golden cannot silently pass.
    """
    d = Path(samples_dir)
    files = sorted(d.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"no golden samples in {d.resolve()}")
    parsed: dict[str, _Frame] = {}
    for p in files:
        try:
            frame = parse_frame(p.read_bytes())
        except ValidationError as e:
            raise ValueError(f"golden sample {p} does not validate:\n{e}") from None
        if frame.type != p.stem:
            raise ValueError(f"golden sample {p} has type={frame.type!r}, expected {p.stem!r}")
        if parse_frame(dump_frame(frame)) != frame:
            raise ValueError(f"golden sample {p} does not round-trip")
        parsed[frame.type] = frame
    missing = set(FRAME_TYPES) - set(parsed)
    if missing:
        raise ValueError(f"missing golden samples for frame types: {sorted(missing)}")
    return parsed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("schema", help="write contract.json")
    s.add_argument("path", nargs="?", default="contract.json")
    s = sub.add_parser("samples", help="write golden samples")
    s.add_argument("dir", nargs="?", default="samples")
    s = sub.add_parser("check", help="validate golden samples")
    s.add_argument("dir", nargs="?", default="samples")
    args = ap.parse_args(argv)

    if args.cmd == "schema":
        p = export_json_schema(args.path)
        print(f"wrote {p} ({p.stat().st_size} bytes)")
    elif args.cmd == "samples":
        for p in write_samples(args.dir):
            print(f"wrote {p}")
        validate_samples(args.dir)
        print("all samples validate")
    elif args.cmd == "check":
        parsed = validate_samples(args.dir)
        print(f"{len(parsed)} samples validate: {', '.join(sorted(parsed))}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
