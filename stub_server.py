"""Sentinel stub WebSocket server.

Speaks the contract in schemas.py and nothing else: no firewall, no Ollama,
no ElevenLabs, no Claude, no DB. It exists so the Android client can be built
and demoed against a server that behaves like the real one on the wire.

Run:
    python stub_server.py --port 8765 --rate 6 --scenario scripted

Endpoints:
    WS   /ws                                   the contract channel
    GET  /audio/{audio_id}.mp3                 1s silent mp3 (real fetch + ExoPlayer path)
    GET  /contract.json                        JSON schema, same as schemas.py schema
    GET  /healthz
    GET  /debug/state                          seq, clients, pending approvals, pipeline
    GET  /debug/degrade?subsystem=ollama[&state=unreachable]   flip a pipeline block
    GET  /debug/restore                        all subsystems back to ok
    GET  /debug/emit?scenario=<name>           fire one scenario now (see --list-scenarios)

TLS (later, when the phone pins the cert):
    openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
        -keyout key.pem -out cert.pem -subj "/CN=sentinel.local" \
        -addext "subjectAltName=IP:192.168.1.50,DNS:sentinel.local"
    python stub_server.py --certfile cert.pem --keyfile key.pem
    Fingerprint for OkHttp CertificatePinner:
        openssl x509 -in cert.pem -noout -fingerprint -sha256
    The audio URLs advertised in events switch to https automatically.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import random
import socket
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

import schemas as S

log = logging.getLogger("sentinel.stub")

HEARTBEAT_S = 10
APPROVAL_TTL_S = 120
SILENT_MP3 = Path(__file__).with_name("static") / "silence-1s.mp3"
SAMPLES_DIR = Path(__file__).with_name("samples")

# --------------------------------------------------------------------------- #
# Config + state
# --------------------------------------------------------------------------- #


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8765
    rate: float = 6.0  # events per minute (gap between scenario steps)
    scenario: str = "scripted"
    advertise: str | None = None  # host used in audio URLs
    certfile: str | None = None
    keyfile: str | None = None
    server_id: str = "sentinel-stub-01"

    @property
    def scheme(self) -> str:
        return "https" if self.certfile else "http"

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.advertise}:{self.port}"

    @property
    def step_gap_s(self) -> float:
        return 60.0 / max(self.rate, 0.01)


@dataclass
class Pending:
    approval_id: str
    event_id: Any
    playbook: str
    expires_at: Any


@dataclass
class Hub:
    cfg: Config
    seq: int = 0
    last_event_seq: int = 0
    started: float = field(default_factory=time.monotonic)
    clients: set[WebSocket] = field(default_factory=set)
    buffer: deque = field(default_factory=lambda: deque(maxlen=S.RESYNC_BUFFER_MAX))
    pending: dict[str, Pending] = field(default_factory=dict)
    pipeline: S.PipelineStatus = field(default_factory=S.PipelineStatus)
    audio_files: dict[str, Path] = field(default_factory=dict)
    tasks: set[asyncio.Task] = field(default_factory=set)
    silent_sha: str = ""

    # -- envelope -------------------------------------------------------------
    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def frame(self, type_: str, data: Any) -> S._Frame:
        fr = S.make_frame(type_, data, seq=self.next_seq())
        if type_ in ("event", "audio_ready", "action_update"):
            self.buffer.append(fr)
        if type_ == "event":
            self.last_event_seq = fr.seq
        return fr

    async def send(self, ws: WebSocket, fr: S._Frame) -> None:
        await ws.send_text(S.dump_frame(fr))

    async def broadcast(self, fr: S._Frame) -> None:
        text = S.dump_frame(fr)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
        log.info("-> %-13s seq=%d clients=%d", fr.type, fr.seq, len(self.clients))

    async def emit(self, type_: str, data: Any) -> S._Frame:
        fr = self.frame(type_, data)
        await self.broadcast(fr)
        return fr

    def later(self, delay_s: float, make_coro: Callable[[], Awaitable[Any]]) -> None:
        """Schedule a follow-up frame. Takes a factory so a cancelled task never
        leaves an un-awaited coroutine behind at shutdown."""
        async def _run() -> None:
            await asyncio.sleep(delay_s)
            await make_coro()

        t = asyncio.create_task(_run())
        self.tasks.add(t)
        t.add_done_callback(self.tasks.discard)

    # -- audio ----------------------------------------------------------------
    def new_audio(self, tts_summary: str, status: str = "ready") -> S.AudioRef:
        audio_id = "aud_" + S.uuid7().hex[:16]
        self.audio_files[audio_id] = SILENT_MP3
        cache_key = "tts_" + hashlib.sha256(tts_summary.encode()).hexdigest()[:12]
        if status != "ready":
            return S.AudioRef(status=status, audio_id=audio_id, cache_key=cache_key)
        return S.AudioRef(
            status="ready",
            audio_id=audio_id,
            url=f"{self.cfg.base_url}/audio/{audio_id}.mp3",
            mime="audio/mpeg",
            duration_ms=1000,
            sha256=self.silent_sha,
            cache_key=cache_key,
        )

    def ready_version(self, pending: S.AudioRef) -> S.AudioRef:
        return S.AudioRef(
            status="ready",
            audio_id=pending.audio_id,
            url=f"{self.cfg.base_url}/audio/{pending.audio_id}.mp3",
            mime="audio/mpeg",
            duration_ms=1000,
            sha256=self.silent_sha,
            cache_key=pending.cache_key,
        )

    # -- approvals ------------------------------------------------------------
    def open_approval(self, event_id: Any, playbook: str, ttl_s: int = APPROVAL_TTL_S) -> Pending:
        p = Pending(
            approval_id="apr_" + S.uuid7().hex[:16],
            event_id=event_id,
            playbook=playbook,
            expires_at=S.now_utc() + timedelta(seconds=ttl_s),
        )
        self.pending[p.approval_id] = p
        return p

    def snapshot(self) -> dict[str, Any]:
        return {
            "server_id": self.cfg.server_id,
            "seq": self.seq,
            "last_event_seq": self.last_event_seq,
            "uptime_s": int(time.monotonic() - self.started),
            "clients": len(self.clients),
            "buffered": len(self.buffer),
            "pending_approvals": {
                k: {"event_id": str(v.event_id), "playbook": v.playbook, "expires_at": S.format_ts(v.expires_at)}
                for k, v in self.pending.items()
            },
            "pipeline": self.pipeline.model_dump(),
            "scenario": self.cfg.scenario,
            "rate_per_min": self.cfg.rate,
            "audio_base": self.cfg.base_url,
        }


# --------------------------------------------------------------------------- #
# Event factory
# --------------------------------------------------------------------------- #


def _window(seconds: int = 60) -> S.Window:
    end = S.now_utc()
    return S.Window(start=end - timedelta(seconds=seconds), end=end)


def build_event(
    *,
    signature: str,
    severity: str,
    title: str,
    internal_log: str,
    tts_summary: str,
    confidence: float,
    source: tuple[str, str, int],
    src_ips: list[str] = (),
    dst_hosts: list[str] = (),
    users: list[str] = (),
    ports: list[int] = (),
    audio: S.AudioRef | None = None,
    escalation: S.Escalation | None = None,
    action: S.ActionRef | None = None,
    reviewed: bool = False,
    window_s: int = 60,
    event_id: Any = None,
) -> S.EventData:
    kind, collector, raw_count = source
    return S.EventData(
        event_id=event_id or S.uuid7(),
        signature=signature,
        severity=severity,
        title=title,
        internal_log=internal_log,
        tts_summary=tts_summary,
        confidence=confidence,
        entities=S.Entities(src_ips=list(src_ips), dst_hosts=list(dst_hosts), users=list(users), ports=list(ports)),
        source=S.Source(kind=kind, collector=collector, raw_count=raw_count),
        window=_window(window_s),
        audio=audio,
        escalation=escalation or S.Escalation(escalated=False),
        action=action,
        reviewed=reviewed,
    )


# --------------------------------------------------------------------------- #
# Scenarios. Each is `async (hub) -> None`. Follow-ups schedule themselves.
# --------------------------------------------------------------------------- #

Scenario = Callable[[Hub], Awaitable[None]]
SCENARIOS: dict[str, Scenario] = {}


def scenario(name: str) -> Callable[[Scenario], Scenario]:
    def deco(fn: Scenario) -> Scenario:
        SCENARIOS[name] = fn
        return fn

    return deco


@scenario("info_no_audio")
async def sc_info_no_audio(hub: Hub) -> None:
    ev = build_event(
        signature="dhcp.new_lease",
        severity="info",
        title="New device joined the LAN",
        internal_log="DHCP handed a lease to 10.0.4.88 (vendor prefix matches a printer). No policy match; logged only.",
        tts_summary="A new device joined the network and was logged.",
        confidence=0.97,
        source=("dhcp", "dnsmasq", 1),
        dst_hosts=["gw01"],
        action=S.ActionRef(
            playbook="notify_only",
            params=S.NotifyOnlyParams(channel="digest"),
            status="auto_executed",
            requires_approval=False,
        ),
    )
    await hub.emit("event", ev)


@scenario("low_rate_limit_auto")
async def sc_low_rate_limit_auto(hub: Hub) -> None:
    duration = 30  # short so the demo sees it expire
    expires = S.now_utc() + timedelta(seconds=duration)
    ev = build_event(
        signature="http.scanner",
        severity="low",
        title="Web scanner probing public site",
        internal_log="91.240.118.29 requested 212 distinct paths on web01 in 45s, 96% 404s. Signature http.scanner. rate_limit auto-applied.",
        tts_summary="A web scanner was rate limited automatically for half a minute.",
        confidence=0.88,
        source=("proxy", "nginx", 212),
        src_ips=["91.240.118.29"],
        dst_hosts=["web01"],
        ports=[443],
        audio=hub.new_audio("A web scanner was rate limited automatically for half a minute."),
        action=S.ActionRef(
            playbook="rate_limit",
            params=S.RateLimitParams(target="91.240.118.29/32", limit_conn_per_min=20, duration_sec=duration),
            status="auto_executed",
            requires_approval=False,
            expires_at=expires,
        ),
    )
    fr = await hub.emit("event", ev)
    hub.later(
        duration,
        lambda: hub.emit(
            "action_update",
            S.ActionUpdateData(
                event_id=fr.data.event_id,
                playbook="rate_limit",
                status="expired",
                expires_at=expires,
                reason="auto-executed rate_limit reached its duration and was lifted",
            ),
        ),
    )


@scenario("high_block_ip_pending")
async def sc_high_block_ip_pending(hub: Hub) -> None:
    ev_id = S.uuid7()
    p = hub.open_approval(ev_id, "block_ip")
    ev = build_event(
        signature="ssh.bruteforce",
        severity="high",
        title="SSH brute force against bastion",
        internal_log="185.220.101.34 attempted 47 SSH logins as svc-backup and root against bastion in 60s; all failed. Duration requested exceeds auto cap, approval required.",
        tts_summary="Repeated failed SSH logins from one outside address are waiting for your approval to block.",
        confidence=0.91,
        source=("auth", "sshd", 47),
        src_ips=["185.220.101.34"],
        dst_hosts=["bastion"],
        users=["svc-backup", "root"],
        ports=[22],
        audio=hub.new_audio("Repeated failed SSH logins from one outside address are waiting for your approval to block."),
        action=S.ActionRef(
            playbook="block_ip",
            params=S.BlockIpParams(target="185.220.101.34/32", duration_sec=14400),
            status="pending_approval",
            requires_approval=True,
            expires_at=p.expires_at,
            approval_id=p.approval_id,
        ),
        event_id=ev_id,
    )
    await hub.emit("event", ev)


@scenario("critical_isolate_pending")
async def sc_critical_isolate_pending(hub: Hub) -> None:
    ev_id = S.uuid7()
    p = hub.open_approval(ev_id, "isolate_host")
    ev = build_event(
        signature="smb.lateral_movement",
        severity="critical",
        title="Workstation spraying SMB logins across the LAN",
        internal_log="10.0.4.27 opened SMB sessions to 14 internal hosts in 40s using m.alvarez credentials, 11 rejected. Consistent with lateral movement. Isolation proposed.",
        tts_summary="A finance workstation is spraying logins across the network and needs your approval to isolate.",
        confidence=0.83,
        source=("auth", "winlogbeat", 38),
        src_ips=["10.0.4.27"],
        dst_hosts=["fs01", "hr-share", "print01"],
        users=["m.alvarez"],
        ports=[445],
        audio=hub.new_audio("A finance workstation is spraying logins across the network and needs your approval to isolate."),
        escalation=S.Escalation(escalated=True, gate="severity_floor", reason="model returned critical", state="pending"),
        action=S.ActionRef(
            playbook="isolate_host",
            params=S.IsolateHostParams(host_id="ws-finance-07", duration_sec=3600),
            status="pending_approval",
            requires_approval=True,
            expires_at=p.expires_at,
            approval_id=p.approval_id,
        ),
        event_id=ev_id,
    )
    await hub.emit("event", ev)


@scenario("escalated_unreachable")
async def sc_escalated_unreachable(hub: Hub) -> None:
    ev = build_event(
        signature="dns.unknown_tunnel_pattern",
        severity="high",
        title="Unusual DNS query volume to one domain",
        internal_log="10.0.4.41 issued 1,900 TXT queries for subdomains of one external zone in 60s. Signature not in catalog; novelty gate fired. Claude API unreachable, event left unreviewed.",
        tts_summary="One computer is making an unusual flood of name lookups and the cloud reviewer could not be reached.",
        confidence=0.62,
        source=("dns", "unbound", 1900),
        src_ips=["10.0.4.41"],
        dst_hosts=["dns01"],
        ports=[53],
        audio=hub.new_audio("One computer is making an unusual flood of name lookups and the cloud reviewer could not be reached."),
        escalation=S.Escalation(
            escalated=True,
            gate="unknown_pattern",
            reason="signature absent from catalog and history; confidence below floor",
            state="unreachable",
        ),
        action=S.ActionRef(
            playbook="watch",
            params=S.WatchParams(target="10.0.4.41", duration_sec=3600),
            status="auto_executed",
            requires_approval=False,
        ),
        reviewed=False,
    )
    await hub.emit("event", ev)


@scenario("audio_pending_then_ready")
async def sc_audio_pending_then_ready(hub: Hub) -> None:
    tts = "An outside address was silently added to the watch list after odd port activity."
    pending_audio = hub.new_audio(tts, status="pending")
    ev = build_event(
        signature="fw.port_sweep",
        severity="low",
        title="Slow port sweep from a single source",
        internal_log="45.155.205.233 touched 60 distinct ports on gw01 over 10 minutes, one packet each. Watch added. TTS render still in flight.",
        tts_summary=tts,
        confidence=0.79,
        source=("firewall", "pfsense", 60),
        src_ips=["45.155.205.233"],
        dst_hosts=["gw01"],
        audio=pending_audio,
        action=S.ActionRef(
            playbook="watch",
            params=S.WatchParams(target="45.155.205.233", duration_sec=86400),
            status="auto_executed",
            requires_approval=False,
        ),
        window_s=600,
    )
    fr = await hub.emit("event", ev)
    hub.later(
        2.0,
        lambda: hub.emit("audio_ready", S.AudioReadyData(event_id=fr.data.event_id, audio=hub.ready_version(pending_audio))),
    )


@scenario("correlation_3_sources")
async def sc_correlation_3_sources(hub: Hub) -> None:
    ev = build_event(
        signature="auth.password_spray",
        severity="high",
        title="Password spray from three sources",
        internal_log="185.220.101.34, 45.155.205.233 and 91.240.118.29 each tried the same 6 usernames once against vpn01 inside one window. Correlation gate fired (3 distinct sources). Claude verdict: coordinated spray, block all three.",
        tts_summary="Three outside addresses tried the same passwords on the VPN and evidence was captured.",
        confidence=0.86,
        source=("auth", "openvpn", 18),
        src_ips=["185.220.101.34", "45.155.205.233", "91.240.118.29"],
        dst_hosts=["vpn01"],
        users=["admin", "helpdesk", "j.doe"],
        ports=[1194],
        audio=hub.new_audio("Three outside addresses tried the same passwords on the VPN and evidence was captured."),
        escalation=S.Escalation(
            escalated=True,
            gate="correlation",
            reason="3 distinct sources in one window",
            state="answered",
            verdict="Coordinated low-and-slow spray. Block all three sources for 24h and rotate the helpdesk password.",
        ),
        action=S.ActionRef(
            playbook="snapshot_evidence",
            params=S.SnapshotEvidenceParams(scope="auth", target="vpn01", window_sec=900),
            status="auto_executed",
            requires_approval=False,
        ),
        reviewed=True,
        window_s=900,
    )
    await hub.emit("event", ev)


@scenario("revoke_session_pending")
async def sc_revoke_session_pending(hub: Hub) -> None:
    ev_id = S.uuid7()
    p = hub.open_approval(ev_id, "revoke_session")
    ev = build_event(
        signature="auth.impossible_travel",
        severity="high",
        title="Same account signed in from two countries",
        internal_log="j.doe authenticated from 185.220.101.34 and 91.240.118.29, geolocated to two continents, 4 minutes apart. Session revocation proposed.",
        tts_summary="One account signed in from two countries minutes apart and can be signed out with your approval.",
        confidence=0.9,
        source=("auth", "keycloak", 2),
        src_ips=["185.220.101.34", "91.240.118.29"],
        dst_hosts=["sso01"],
        users=["j.doe"],
        audio=hub.new_audio("One account signed in from two countries minutes apart and can be signed out with your approval."),
        action=S.ActionRef(
            playbook="revoke_session",
            params=S.RevokeSessionParams(user="j.doe", session_id="sess_7f3a2c"),
            status="pending_approval",
            requires_approval=True,
            expires_at=p.expires_at,
            approval_id=p.approval_id,
        ),
        event_id=ev_id,
    )
    await hub.emit("event", ev)


_BURST = [
    ("info", "fw.deny_outbound", "Outbound connection denied", "The firewall quietly dropped one outbound connection."),
    ("low", "http.scanner", "Scanner returned", "The earlier web scanner came back and was noted."),
    ("info", "dhcp.new_lease", "Another device joined", "Another device joined the network and was logged."),
    ("high", "ssh.bruteforce", "SSH brute force resumed", "Failed SSH logins resumed from an outside address."),
    ("low", "dns.nxdomain_spike", "Burst of failed lookups", "One computer produced a short burst of failed name lookups."),
    ("critical", "av.ransom_note", "Ransom note file written", "A ransom note style file was written on a shared drive."),
]


@scenario("burst_6_in_20s")
async def sc_burst(hub: Hub) -> None:
    gap = 20.0 / len(_BURST)
    for i, (sev, sig, title, tts) in enumerate(_BURST):
        ev = build_event(
            signature=sig,
            severity=sev,
            title=title,
            internal_log=f"Burst item {i + 1} of {len(_BURST)}: {title.lower()} involving 45.155.205.{200 + i}. Exercising the phone's playback queue.",
            tts_summary=tts,
            confidence=0.8,
            source=("mixed", "stub", 1 + i),
            src_ips=[f"45.155.205.{200 + i}"],
            dst_hosts=["web01"],
            audio=hub.new_audio(tts),
            action=S.ActionRef(
                playbook="notify_only",
                params=S.NotifyOnlyParams(),
                status="auto_executed",
                requires_approval=False,
            ),
        )
        await hub.emit("event", ev)
        if i < len(_BURST) - 1:
            await asyncio.sleep(gap)


SCRIPT_ORDER = [
    "info_no_audio",
    "low_rate_limit_auto",
    "high_block_ip_pending",
    "audio_pending_then_ready",
    "critical_isolate_pending",
    "escalated_unreachable",
    "correlation_3_sources",
    "revoke_session_pending",
    "burst_6_in_20s",
]
assert set(SCRIPT_ORDER) == set(SCENARIOS)

# --------------------------------------------------------------------------- #
# Background loops
# --------------------------------------------------------------------------- #


async def heartbeat_loop(hub: Hub) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        await hub.emit("heartbeat", heartbeat_data(hub))


def heartbeat_data(hub: Hub) -> S.HeartbeatData:
    return S.HeartbeatData(
        interval_s=HEARTBEAT_S,
        uptime_s=int(time.monotonic() - hub.started),
        last_event_seq=hub.last_event_seq,
        queue_depth=len(hub.pending),
        pipeline=hub.pipeline,
    )


async def scenario_loop(hub: Hub) -> None:
    await asyncio.sleep(3)  # let the first client connect
    while True:
        names = SCRIPT_ORDER if hub.cfg.scenario == "scripted" else [random.choice(SCRIPT_ORDER)]
        for name in names:
            log.info("scenario %s", name)
            try:
                await SCENARIOS[name](hub)
            except Exception:
                log.exception("scenario %s failed", name)
            await asyncio.sleep(hub.cfg.step_gap_s)
        if hub.cfg.scenario == "scripted":
            log.info("script finished; looping")


async def expiry_loop(hub: Hub) -> None:
    while True:
        await asyncio.sleep(5)
        now = S.now_utc()
        for aid, p in list(hub.pending.items()):
            if now > p.expires_at:
                hub.pending.pop(aid, None)
                await hub.emit(
                    "action_update",
                    S.ActionUpdateData(
                        event_id=p.event_id,
                        playbook=p.playbook,
                        status="expired",
                        approval_id=aid,
                        expires_at=p.expires_at,
                        reason="no decision before expires_at",
                    ),
                )


# --------------------------------------------------------------------------- #
# Inbound handling
# --------------------------------------------------------------------------- #


async def handle_client_frame(hub: Hub, ws: WebSocket, fr: S._Frame) -> None:
    if fr.type == "ack":
        log.info("<- ack seq=%d for seq=%d event_id=%s", fr.seq, fr.data.seq, fr.data.event_id)
        return

    if fr.type == "resync":
        since = fr.data.since_seq
        replay = [b for b in hub.buffer if b.seq > since][: S.RESYNC_BUFFER_MAX]
        log.info("<- resync since_seq=%d -> replaying %d frames", since, len(replay))
        for b in replay:  # deque is append-ordered, so oldest first
            await hub.send(ws, b)
        return

    if fr.type == "approve_action":
        d = fr.data
        now = S.now_utc()
        p = hub.pending.get(d.approval_id)
        if p is None:
            status, reason, expires_at = "failed", "unknown or already settled approval_id", None
            playbook = "notify_only"
        elif now > p.expires_at:
            hub.pending.pop(d.approval_id, None)
            status, reason, expires_at, playbook = "expired", "decision arrived after expires_at", p.expires_at, p.playbook
        elif p.event_id != d.event_id:
            status, reason, expires_at, playbook = "failed", "event_id does not match approval", p.expires_at, p.playbook
        elif d.decision == "approved" and not d.biometric:
            status, reason, expires_at, playbook = "denied", "approval without biometric is refused", p.expires_at, p.playbook
        else:
            hub.pending.pop(d.approval_id, None)
            status, reason, expires_at, playbook = d.decision, f"{d.decision} on device", None, p.playbook
        log.info("<- approve_action %s decision=%s -> %s", d.approval_id, d.decision, status)
        await hub.emit(
            "action_update",
            S.ActionUpdateData(
                event_id=d.event_id,
                playbook=playbook,
                status=status,
                approval_id=d.approval_id,
                expires_at=expires_at,
                reason=reason,
            ),
        )
        return


# --------------------------------------------------------------------------- #
# Startup helpers
# --------------------------------------------------------------------------- #


def ensure_silent_mp3(path: Path) -> str:
    """Write a valid 1s MPEG-1 Layer III mono 32kbps 44.1kHz file of silence.

    38 frames x 1152 samples = 0.99s. All-zero side info + main data decodes
    as silence in every decoder ExoPlayer ships. Returns the sha256 hex.
    """
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # FF FB: sync + MPEG-1 + Layer III + no CRC
        # 10:    bitrate idx 1 (32 kbps), 44.1 kHz, no padding, private=0
        # C0:    mono, no mode ext, not copyrighted, copy, no emphasis
        frame = bytes.fromhex("fffb10c0") + b"\x00" * 100  # 144*32000/44100 = 104 bytes
        path.write_bytes(frame * 38)
        log.info("generated %s (%d bytes)", path, path.stat().st_size)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def detect_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def validate_samples_or_die() -> None:
    try:
        parsed = S.validate_samples(SAMPLES_DIR)
    except Exception as e:  # noqa: BLE001 - we want to die loudly on anything
        log.critical("GOLDEN SAMPLES FAILED VALIDATION\n%s", e)
        raise SystemExit(2) from None
    log.info("golden samples ok: %s", ", ".join(sorted(parsed)))


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #

CONFIG = Config()
HUB: Hub


@asynccontextmanager
async def lifespan(app: FastAPI):
    global HUB
    validate_samples_or_die()
    HUB = Hub(cfg=CONFIG)
    HUB.silent_sha = ensure_silent_mp3(SILENT_MP3)
    HUB.audio_files["silence"] = SILENT_MP3
    loops = [asyncio.create_task(c(HUB)) for c in (heartbeat_loop, scenario_loop, expiry_loop)]
    log.info("stub up: ws://%s:%d/ws  audio base %s  scenario=%s rate=%.1f/min",
             CONFIG.host, CONFIG.port, CONFIG.base_url, CONFIG.scenario, CONFIG.rate)
    try:
        yield
    finally:
        for t in loops + list(HUB.tasks):
            t.cancel()


app = FastAPI(title="Sentinel stub", lifespan=lifespan)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    HUB.clients.add(ws)
    peer = f"{ws.client.host}:{ws.client.port}" if ws.client else "?"
    log.info("client connected %s (%d total)", peer, len(HUB.clients))
    try:
        await HUB.send(
            ws,
            HUB.frame(
                "hello",
                S.HelloData(
                    server_id=CONFIG.server_id,
                    heartbeat_interval_s=HEARTBEAT_S,
                    last_event_seq=HUB.last_event_seq,
                ),
            ),
        )
        while True:
            raw = await ws.receive_text()
            try:
                fr = S.parse_client_frame(raw)
            except ValidationError as e:
                log.warning("<- invalid client frame from %s: %s | %s", peer, e.errors()[0].get("msg"), raw[:200])
                continue
            await handle_client_frame(HUB, ws, fr)
    except WebSocketDisconnect:
        pass
    finally:
        HUB.clients.discard(ws)
        log.info("client disconnected %s (%d total)", peer, len(HUB.clients))


@app.get("/audio/{audio_id}.mp3")
async def audio(audio_id: str) -> FileResponse:
    path = HUB.audio_files.get(audio_id)
    if path is None or not path.exists():
        raise HTTPException(404, "unknown audio_id")
    return FileResponse(path, media_type="audio/mpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/contract.json")
async def contract() -> JSONResponse:
    return JSONResponse(S.build_json_schema())


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    return {"ok": True, "seq": HUB.seq, "clients": len(HUB.clients)}


@app.get("/debug/state")
async def debug_state() -> dict[str, Any]:
    return HUB.snapshot()


@app.get("/debug/degrade")
async def debug_degrade(
    subsystem: str = Query(pattern="^(log_source|ollama|tts|claude)$"),
    state: str = Query("unreachable", pattern="^(ok|degraded|unreachable)$"),
) -> dict[str, Any]:
    HUB.pipeline = HUB.pipeline.model_copy(update={subsystem: state})
    await HUB.emit("heartbeat", heartbeat_data(HUB))  # push immediately, no 10s wait
    return HUB.pipeline.model_dump()


@app.get("/debug/restore")
async def debug_restore() -> dict[str, Any]:
    HUB.pipeline = S.PipelineStatus()
    await HUB.emit("heartbeat", heartbeat_data(HUB))
    return HUB.pipeline.model_dump()


@app.get("/debug/emit")
async def debug_emit(scenario: str) -> dict[str, Any]:
    fn = SCENARIOS.get(scenario)
    if fn is None:
        raise HTTPException(404, f"unknown scenario; one of {SCRIPT_ORDER}")
    t = asyncio.create_task(fn(HUB))
    HUB.tasks.add(t)
    t.add_done_callback(HUB.tasks.discard)
    return {"started": scenario, "seq_before": HUB.seq}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=CONFIG.port)
    ap.add_argument("--host", default=CONFIG.host, help="bind address")
    ap.add_argument("--rate", type=float, default=CONFIG.rate, help="events per minute between scenario steps")
    ap.add_argument("--scenario", choices=["scripted", "random"], default=CONFIG.scenario)
    ap.add_argument("--advertise", default=None, help="host to put in audio URLs (default: detected LAN IP)")
    ap.add_argument("--certfile", default=None, help="PEM cert; enables wss:// and https audio URLs")
    ap.add_argument("--keyfile", default=None, help="PEM key for --certfile")
    ap.add_argument("--list-scenarios", action="store_true")
    ap.add_argument("--log-level", default="info")
    a = ap.parse_args(argv)

    if a.list_scenarios:
        print("\n".join(SCRIPT_ORDER))
        return
    if bool(a.certfile) != bool(a.keyfile):
        ap.error("--certfile and --keyfile go together")

    CONFIG.host, CONFIG.port, CONFIG.rate, CONFIG.scenario = a.host, a.port, a.rate, a.scenario
    CONFIG.certfile, CONFIG.keyfile = a.certfile, a.keyfile
    CONFIG.advertise = a.advertise or detect_lan_ip()

    logging.basicConfig(level=a.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    uvicorn.run(
        app,
        host=CONFIG.host,
        port=CONFIG.port,
        log_level=a.log_level,
        ssl_certfile=a.certfile,
        ssl_keyfile=a.keyfile,
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
