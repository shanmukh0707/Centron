"""Sentinel stub WebSocket server.

Speaks the contract in schemas.py and nothing else: no firewall, no Ollama,
no ElevenLabs, no Claude, no DB. It exists so the Android client can be built
and demoed against a server that behaves like the real one on the wire.

Run:
    python stub_server.py --port 8765 --rate 6 --scenario scripted
    python stub_server.py --scenario calm --quiet          # one event/min, one critical, no info/low
    python stub_server.py --source pipeline --fixture --fake-llm
    python stub_server.py --source pipeline --syslog 5514 --live-ollama --db sentinel.db

--source pipeline swaps the event source for pipeline.py (same Hub, seq,
heartbeat, approvals, audio). Approvals then call executor.execute() for real
and the action_update carries the true outcome; a late Claude verdict re-emits
the full event with the same event_id and a fresh seq. pipeline.py's flags
(--fixture/--tail/--syslog, --fake-llm/--live-ollama, --dry-run/--live, --db)
are accepted and forwarded.

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
import os
import random
import secrets
import socket
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError

import schemas as S
from pipeline import Pipeline, SubsystemHealth, add_pipeline_args, build_from_args
from tts import Rendered, Renderer

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
    source: str = "scripted"      # scripted | pipeline
    quiet: bool = False           # drop info/low events entirely (UI work)
    pipeline_args: Any = None     # argparse.Namespace forwarded to pipeline.build_from_args

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
    pipe: Pipeline | None = None
    suppressed: set = field(default_factory=set)   # event_ids dropped by --quiet
    quiet_dropped: int = 0
    # Speech renderer. None means no renderer is wired, which the heartbeat
    # reports as unreachable rather than pretending otherwise.
    tts: Renderer | None = None
    # Rendered audio, keyed on the contract's cache_key so a repeated phrase is
    # synthesised once. audio_files maps ids; this maps content.
    tts_cache: dict[str, Rendered] = field(default_factory=dict)
    # Subsystem state for the heartbeat when no pipeline owns one (--source
    # scripted). With a pipeline, `health` is the pipeline's own object.
    standby_health: SubsystemHealth = field(default_factory=SubsystemHealth)

    @property
    def health(self) -> SubsystemHealth:
        return self.pipe.health if self.pipe is not None else self.standby_health

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

    def _quiet_drop(self, type_: str, data: Any) -> bool:
        if not self.cfg.quiet:
            return False
        if type_ == "event" and data.severity in ("info", "low"):
            self.suppressed.add(data.event_id)
            return True
        if type_ in ("audio_ready", "action_update") and data.event_id in self.suppressed:
            return True
        return False

    async def emit(self, type_: str, data: Any) -> S._Frame:
        if self._quiet_drop(type_, data):
            # Build (unsent, seq not consumed) so follow-ups can still reference it.
            self.quiet_dropped += 1
            log.info("-- %-13s suppressed by --quiet (%s)", type_, getattr(data, "severity", ""))
            return S.make_frame(type_, data, seq=self.seq)
        fr = self.frame(type_, data)
        await self.broadcast(fr)
        # An event shipping pending audio owes the phone an audio_ready, or it
        # is simply mute: the client holds a pending ref and waits forever.
        # _emit_pipeline_event schedules its own render, but the scripted
        # scenarios did not, so once a renderer was installed every scripted
        # demo went silent. Enforcing it here means a new scenario cannot
        # forget. render_audio is idempotent per cache_key.
        if type_ == "event":
            audio = getattr(data, "audio", None)
            if audio is not None and audio.status == "pending":
                self.render_audio(data.event_id, audio, data.tts_summary)
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
    @staticmethod
    def cache_key_for(tts_summary: str) -> str:
        return "tts_" + hashlib.sha256(tts_summary.encode()).hexdigest()[:12]

    def _ref_from(self, audio_id: str, cache_key: str, r: Rendered) -> S.AudioRef:
        self.audio_files[audio_id] = r.path
        return S.AudioRef(
            status="ready",
            audio_id=audio_id,
            url=f"{self.cfg.base_url}/audio/{audio_id}.mp3",
            mime="audio/mpeg",
            duration_ms=r.duration_ms,
            sha256=r.sha256,
            cache_key=cache_key,
        )

    def new_audio(self, tts_summary: str, status: str = "ready") -> S.AudioRef:
        """An AudioRef for this phrase.

        If the phrase is already rendered it comes back ready, with the real
        duration and digest. Otherwise it comes back pending and the caller is
        expected to schedule a render; the event must never wait on audio.
        """
        audio_id = "aud_" + S.uuid7().hex[:16]
        cache_key = self.cache_key_for(tts_summary)

        cached = self.tts_cache.get(cache_key)
        if cached is not None and cached.path.exists():
            return self._ref_from(audio_id, cache_key, cached)

        if status != "ready" or self.tts is None or not self.tts.available:
            # No renderer, or the caller wants pending. Falling back to the
            # silent clip keeps the phone's fetch-and-play path exercised.
            self.audio_files[audio_id] = SILENT_MP3
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

        # Renderable but not yet rendered.
        self.audio_files[audio_id] = SILENT_MP3
        return S.AudioRef(status="pending", audio_id=audio_id, cache_key=cache_key)

    def ready_version(self, pending: S.AudioRef, text: str | None = None) -> S.AudioRef:
        """The ready form of a pending ref, using real audio when we have it."""
        key = pending.cache_key or (self.cache_key_for(text) if text else None)
        cached = self.tts_cache.get(key) if key else None
        if cached is not None and cached.path.exists():
            return self._ref_from(pending.audio_id, key, cached)

        self.audio_files.setdefault(pending.audio_id, SILENT_MP3)
        return S.AudioRef(
            status="ready",
            audio_id=pending.audio_id,
            url=f"{self.cfg.base_url}/audio/{pending.audio_id}.mp3",
            mime="audio/mpeg",
            duration_ms=1000,
            sha256=self.silent_sha,
            cache_key=pending.cache_key,
        )

    async def render_now(self, text: str, cache_key: str | None = None) -> Rendered | None:
        """One real render on a worker thread, outcome recorded in health.

        Every render goes through here so the heartbeat's tts word is backed
        by what render() last actually did, not by which engine was picked at
        startup.
        """
        if self.tts is None or not self.tts.available:
            return None
        key = cache_key or self.cache_key_for(text)
        loop = asyncio.get_running_loop()
        rendered = await loop.run_in_executor(None, self.tts.render, text, key)
        self.health.note_tts_render(rendered is not None)
        if rendered is not None:
            self.tts_cache[key] = rendered
        return rendered

    def render_audio(self, event_id: Any, ref: S.AudioRef, text: str) -> None:
        """Render off the event path, then announce with audio_ready.

        Runs in a worker thread because espeak and an HTTP call to ElevenLabs
        are both blocking, and the event loop is serving a live socket. The
        event has already gone out by the time this starts.
        """
        if self.tts is None or not self.tts.available or ref.status == "ready":
            return

        async def run() -> None:
            key = ref.cache_key or self.cache_key_for(text)
            rendered = await self.render_now(text, key)
            if rendered is None:
                # Contract: never block an event on TTS. It already shipped;
                # say the audio failed and move on.
                await self.emit("audio_ready", S.AudioReadyData(
                    event_id=event_id,
                    audio=S.AudioRef(status="failed", audio_id=ref.audio_id,
                                     cache_key=ref.cache_key),
                ))
                return

            await self.emit("audio_ready", S.AudioReadyData(
                event_id=event_id,
                audio=self._ref_from(ref.audio_id, key, rendered),
            ))

        # later() takes a factory, so a cancelled task never leaves an
        # un-awaited coroutine behind at shutdown.
        self.later(0.0, run)

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

    def refresh_pipeline(self) -> S.PipelineStatus:
        """Cached SubsystemHealth only. This path never probes anything: a
        heartbeat that blocks on a dead Ollama box would stop the heartbeat,
        which is the one thing that has to keep going when everything else is
        down. Called on every heartbeat and every /debug/state, so there is no
        window at startup where the defaults (all ok) are on show."""
        if self.pipe is not None:
            self.pipeline = self.pipe.status()
        else:
            # Scripted source: the other three subsystems are stage props (and
            # /debug/degrade may be driving them); tts is real either way.
            self.pipeline = self.pipeline.model_copy(update={"tts": self.health.tts_state()})
        return self.pipeline

    def snapshot(self) -> dict[str, Any]:
        self.refresh_pipeline()
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
            "source": self.cfg.source,
            "quiet": self.cfg.quiet,
            "quiet_dropped": self.quiet_dropped,
            "rate_per_min": self.cfg.rate,
            "audio_base": self.cfg.base_url,
            "executor": self.pipe.executor.breaker_state() if self.pipe else None,
            "pipeline_stats": self.pipe.stats.as_text() if self.pipe else None,
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

# `calm`: roughly one event per 60s, at most one critical per run, no burst,
# so the alert panel is not constantly auto-opening during UI work.
CALM_ORDER = [
    "info_no_audio",
    "low_rate_limit_auto",
    "high_block_ip_pending",
    "audio_pending_then_ready",
    "critical_isolate_pending",   # the single critical
    "escalated_unreachable",
    "correlation_3_sources",
    "revoke_session_pending",
]
CALM_LOOP = [n for n in CALM_ORDER if n != "critical_isolate_pending"]
CALM_GAP_S = 60.0

# --------------------------------------------------------------------------- #
# Background loops
# --------------------------------------------------------------------------- #


async def heartbeat_loop(hub: Hub) -> None:
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        await hub.emit("heartbeat", heartbeat_data(hub))


def heartbeat_data(hub: Hub) -> S.HeartbeatData:
    hub.refresh_pipeline()

    return S.HeartbeatData(
        interval_s=HEARTBEAT_S,
        uptime_s=int(time.monotonic() - hub.started),
        last_event_seq=hub.last_event_seq,
        queue_depth=len(hub.pending),
        pipeline=hub.pipeline,
    )


async def scenario_loop(hub: Hub) -> None:
    await asyncio.sleep(3)  # let the first client connect
    first_pass = True
    while True:
        if hub.cfg.scenario == "scripted":
            names, gap = SCRIPT_ORDER, hub.cfg.step_gap_s
        elif hub.cfg.scenario == "calm":
            names, gap = (CALM_ORDER if first_pass else CALM_LOOP), CALM_GAP_S
        else:
            names, gap = [random.choice(SCRIPT_ORDER)], hub.cfg.step_gap_s
        for name in names:
            log.info("scenario %s", name)
            try:
                await SCENARIOS[name](hub)
            except Exception:
                log.exception("scenario %s failed", name)
            await asyncio.sleep(gap)
        first_pass = False
        if hub.cfg.scenario in ("scripted", "calm"):
            log.info("script finished; looping")


# --------------------------------------------------------------------------- #
# --source pipeline: events come from pipeline.py
# --------------------------------------------------------------------------- #


def _with_audio(hub: Hub, ev: S.EventData) -> S.EventData:
    """Attach audio. info is never spoken, so it gets none at all.

    A phrase already in the cache comes back ready immediately. Anything new
    ships pending and the render is scheduled by the caller, because the event
    must not wait for speech.
    """
    if ev.audio is not None or ev.severity == "info":
        return ev
    return ev.model_copy(update={"audio": hub.new_audio(ev.tts_summary)})


async def _emit_pipeline_event(hub: Hub, ev: S.EventData, reemit: bool = False) -> None:
    if not reemit and ev.action is not None and ev.action.status == "pending_approval" and ev.action.approval_id:
        hub.pending[ev.action.approval_id] = Pending(
            approval_id=ev.action.approval_id, event_id=ev.event_id,
            playbook=ev.action.playbook, expires_at=ev.action.expires_at,
        )
    with_audio = _with_audio(hub, ev)
    fr = await hub.emit("event", with_audio)
    if hub.pipe is not None and not reemit:
        hub.pipe.db.update_event(ev.event_id, seq=fr.seq)

    # The render used to be scheduled here. hub.emit now does it for every
    # event carrying pending audio, so doing it again would render twice and
    # send two audio_ready frames for one event.


async def pipeline_loop(hub: Hub) -> None:
    """Run the (synchronous) pipeline on a worker thread; emit on the loop."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[tuple[S.EventData, bool] | None] = asyncio.Queue()

    def push(ev: S.EventData, reemit: bool) -> None:
        loop.call_soon_threadsafe(q.put_nowait, (ev, reemit))

    def on_update(ev: S.EventData) -> None:   # late Claude verdict, worker thread
        push(ev, True)

    pipe, lines, listener = build_from_args(hub.cfg.pipeline_args, on_update=on_update)
    hub.pipe = pipe
    if hub.tts is not None:
        pipe.health.note_tts_engine(hub.tts.engine)
    if listener is not None:
        await listener.start()

    def consume() -> None:
        try:
            for ev in pipe.run(lines):
                push(ev, False)
        except Exception:
            log.exception("pipeline stopped")
            pipe.log_source_state = "unreachable"
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    await asyncio.sleep(3)  # let the first client connect, same as scenario_loop
    worker = loop.run_in_executor(None, consume)
    log.info("pipeline source up: %s", pipe.stats.as_text())
    while True:
        item = await q.get()
        if item is None:
            break
        ev, reemit = item
        try:
            await _emit_pipeline_event(hub, ev, reemit)
        except Exception:
            log.exception("failed to emit event %s", ev.event_id)
    await worker
    log.info("pipeline source finished: %s", pipe.stats.as_text())
    while True:  # the fixture ran out; stay up so the phone keeps its heartbeat
        await asyncio.sleep(3600)


async def expiry_loop(hub: Hub) -> None:
    while True:
        await asyncio.sleep(5)
        now = S.now_utc()
        for aid, p in list(hub.pending.items()):
            if now > p.expires_at:
                hub.pending.pop(aid, None)
                if hub.pipe is not None:
                    hub.pipe.executor.expire(aid)
                    hub.pipe.db.update_event(p.event_id, action_status="expired")
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
        elif hub.pipe is not None:
            # Real executor: the status is whatever actually happened.
            hub.pending.pop(d.approval_id, None)
            ex = hub.pipe.executor
            res = await asyncio.to_thread(ex.approve if d.decision == "approved" else ex.deny, d.approval_id)
            status, reason, expires_at, playbook = res.status, res.reason[:200], res.expires_at, p.playbook
            hub.pipe.db.update_event(d.event_id, action_status=status)
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
    HUB.tts = Renderer()
    HUB.standby_health.note_tts_engine(HUB.tts.engine)
    source_loop = pipeline_loop if CONFIG.source == "pipeline" else scenario_loop
    loops = [asyncio.create_task(c(HUB)) for c in (heartbeat_loop, source_loop, expiry_loop)]
    log.info("stub up: ws://%s:%d/ws  audio base %s  source=%s scenario=%s rate=%.1f/min quiet=%s",
             CONFIG.host, CONFIG.port, CONFIG.base_url, CONFIG.source, CONFIG.scenario, CONFIG.rate, CONFIG.quiet)
    try:
        yield
    finally:
        for t in loops + list(HUB.tasks):
            t.cancel()
        if HUB.pipe is not None and HUB.pipe.escalator is not None:
            HUB.pipe.escalator.drain()


app = FastAPI(title="Sentinel stub", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Client authentication
#
# contracts.md: "the pinned cert plus the pre-shared token is what actually
# authenticates the client." `biometric: true` on an approve_action is a claim
# the app makes about its operator, not proof of anything to us.
#
# Disabled when SENTINEL_TOKEN is unset, so fixture runs, the test suite and
# local UI work are unaffected. Set it on serverpi and the door is shut.
#
# This is not a replacement for TLS. Over plaintext the token is readable by
# anyone on the path; it is only meaningful behind --certfile/--keyfile.
# --------------------------------------------------------------------------- #

SENTINEL_TOKEN = os.environ.get("SENTINEL_TOKEN", "").strip()


def _token_from_header(value: str | None) -> str:
    if not value:
        return ""
    prefix = "bearer "
    return value[len(prefix):].strip() if value.lower().startswith(prefix) else value.strip()


def _authorized(header_value: str | None) -> bool:
    if not SENTINEL_TOKEN:
        return True  # auth disabled
    # compare_digest: token check must not leak length or prefix via timing.
    return secrets.compare_digest(_token_from_header(header_value), SENTINEL_TOKEN)




@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if not _authorized(ws.headers.get("authorization")):
        # Refused during the handshake, so the client sees a failed upgrade
        # rather than a connection that opens and then goes quiet.
        peer_ = f"{ws.client.host}" if ws.client else "?"
        log.warning("rejected unauthenticated ws connection from %s", peer_)
        await ws.close(code=1008, reason="unauthorized")
        return

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
async def audio(audio_id: str, authorization: str | None = Header(default=None)) -> FileResponse:
    # Same token as the socket: the contract has the phone fetching audio over
    # the same session with the same bearer.
    if not _authorized(authorization):
        raise HTTPException(401, "unauthorized")
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


@app.get("/debug/breaker/reset")
async def debug_breaker_reset() -> dict[str, Any]:
    if HUB.pipe is None:
        raise HTTPException(400, "no executor with --source scripted")
    HUB.pipe.executor.reset()
    return HUB.pipe.executor.breaker_state()


@app.get("/debug/tts")
async def debug_tts(
    text: str = Query("Sentinel preflight check. The speech path is working.", min_length=1, max_length=200),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """One real render, same path an event takes, returned as a ready AudioRef.

    tools/preflight.sh fetches the URL, checks the digest and listens for
    silence. A success here is also what flips the heartbeat's tts to ok on an
    ElevenLabs box: the renderer is proven by rendering, not by having a key.
    """
    if not _authorized(authorization):
        raise HTTPException(401, "unauthorized")
    if HUB.tts is None or not HUB.tts.available:
        raise HTTPException(503, "no tts renderer (no ELEVENLABS_API_KEY and no espeak-ng+lame)")
    key = HUB.cache_key_for(text)
    rendered = await HUB.render_now(text, key)
    if rendered is None:
        raise HTTPException(502, f"render failed on {HUB.tts.engine}; see the journal")
    ref = HUB._ref_from("aud_" + S.uuid7().hex[:16], key, rendered)
    return {"engine": rendered.engine, "tts": HUB.health.tts_state(), "audio": ref.model_dump(mode="json")}


CHAT_SYSTEM = (
    "You are Centron, an assistant embedded in a home lab security console. "
    "The person you are talking to owns this lab and is looking at their phone. "
    "Answer in at most four sentences, plainly, no markdown and no bullet lists, "
    "because your reply is rendered in a small chat bubble. "
    "If you are asked to do something that would change the state of a machine, "
    "explain that actions go through the approval flow with a fingerprint and are "
    "never taken from chat. If you do not know something about their specific "
    "hardware, say so rather than guessing."
)


@app.post("/chat")
async def chat(
    body: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Ask Claude a question from one of the app's chat boxes.

    Deliberately an HTTP endpoint rather than a new frame type: the WebSocket
    contract in schemas.py is not mine to extend, and chat is request/response
    anyway, so it does not want to share the event stream's ordering.

    Only the operator's own words and a scope label are sent. No log lines, no
    addresses, no event payloads -- escalation already has a redaction path for
    that and this endpoint has no business reimplementing it badly.
    """
    if not _authorized(authorization):
        raise HTTPException(401, "unauthorized")

    message = str(body.get("message", "")).strip()
    if not message:
        raise HTTPException(400, "empty message")
    scope = str(body.get("scope", "this home lab")).strip() or "this home lab"

    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise HTTPException(503, "ANTHROPIC_API_KEY is not set on the engine")

    # Recent turns, so a follow-up question is not answered blind. Trimmed
    # because the phone should not be able to push an unbounded prompt.
    history = body.get("history") or []
    messages: list[dict[str, str]] = []
    for turn in history[-6:]:
        role = "assistant" if str(turn.get("role")) == "assistant" else "user"
        text = str(turn.get("text", "")).strip()[:2000]
        if text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": message[:2000]})

    def ask() -> str:
        import anthropic

        client = anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=0)
        resp = client.messages.create(
            model=os.environ.get("SENTINEL_CLAUDE_MODEL", "claude-opus-5"),
            max_tokens=400,
            system=f"{CHAT_SYSTEM}\n\nThe question is about: {scope}.",
            messages=messages,
        )
        return " ".join(
            b.text.strip()
            for b in getattr(resp, "content", [])
            if getattr(b, "type", None) == "text"
        ).strip()

    try:
        # Blocking SDK call, so off the loop: it is serving a live socket.
        reply = await asyncio.get_running_loop().run_in_executor(None, ask)
    except Exception as e:
        log.warning("chat failed: %s: %s", type(e).__name__, e)
        raise HTTPException(502, f"chat failed: {type(e).__name__}")

    if not reply:
        raise HTTPException(502, "empty reply")
    return {"reply": reply}


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
    ap.add_argument("--scenario", choices=["scripted", "random", "calm"], default=CONFIG.scenario,
                    help="calm: ~1 event/60s, at most one critical per run")
    ap.add_argument("--source", choices=["scripted", "pipeline"], default=CONFIG.source,
                    help="pipeline: events from pipeline.py (flags below); scripted: the scenario list")
    ap.add_argument("--quiet", action="store_true", help="suppress info and low severity events entirely")
    add_pipeline_args(ap)
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
    CONFIG.source, CONFIG.quiet, CONFIG.pipeline_args = a.source, a.quiet, a
    if a.source == "pipeline":
        if not (a.fixture or a.tail or a.syslog):
            ap.error("--source pipeline needs one of --fixture / --tail / --syslog")
        if not (a.fake_llm or a.live_ollama):
            ap.error("--source pipeline needs --fake-llm or --live-ollama")

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
