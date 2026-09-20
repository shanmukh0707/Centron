"""Syslog ingestion: UDP + TCP listener that feeds raw lines to the pipeline.

    router / hosts / Pi-hole  --syslog-->  SyslogListener  --> queue --> Pipeline

Each datagram or TCP line is tagged with its sender address, normalised to
the "Mon DD HH:MM:SS host proc[pid]: msg" shape parsers.py understands (RFC
3164 PRI stripped, RFC 5424 headers rewritten), and paired with the parser
`kind` the source_map assigns to that sender. Unknown senders get kind=None,
which makes LineParser try every family.

HOMELAB, IMPORTANT: lines from this machine's own addresses are dropped and
counted. Sentinel logs to syslog; if syslog forwards back here, every emitted
event would generate log lines that generate events. The local address set
is detected at startup (assets.local_addresses) and the drop counter is on
SyslogListener.stats so a misconfiguration is visible, not silent.

Default port 5514 so it runs without root. log_source.tail_file() and
replay_fixture() are untouched; this module is a third source beside them.

source_map (YAML or JSON), see the shipped source_map.yaml:
    sources:
      192.168.1.1: firewall      # gateway
      192.168.1.30: auth         # a host forwarding auth.log
      192.168.1.53: dns          # Pi-hole
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from assets import local_addresses
from parsers import KINDS

log = logging.getLogger("sentinel.ingest")

HERE = Path(__file__).resolve().parent
DEFAULT_PORT = 5514
DEFAULT_SOURCE_MAP = HERE / "source_map.yaml"
MAX_LINE = 8192
QUEUE_MAX = 10_000

_PRI_RE = re.compile(r"^<\d{1,3}>")
# RFC 5424: VERSION SP TIMESTAMP SP HOSTNAME SP APP-NAME SP PROCID SP MSGID SP STRUCTURED-DATA SP MSG
_RFC5424_RE = re.compile(
    r"^1 (?P<ts>\S+) (?P<host>\S+) (?P<app>\S+) (?P<pid>\S+) (?P<msgid>\S+) (?P<sd>-|\[.*?\]) ?(?P<msg>.*)$"
)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class TaggedLine:
    sender: str
    line: str
    kind: str | None


@dataclass
class IngestStats:
    received: int = 0
    dropped_local: int = 0
    dropped_oversize: int = 0
    dropped_empty: int = 0
    by_sender: dict[str, int] = field(default_factory=dict)

    def as_text(self) -> str:
        return (f"received={self.received} dropped_local={self.dropped_local} "
                f"dropped_oversize={self.dropped_oversize} senders={len(self.by_sender)}")


# ------------------------------------------------------------- normalise
def normalize_syslog(raw: str) -> str:
    """Strip PRI, rewrite RFC 5424 into the RFC 3164 shape parsers.py expects."""
    line = _PRI_RE.sub("", raw.strip("\r\n\x00"))
    m = _RFC5424_RE.match(line)
    if m is None:
        return line
    try:
        ts = datetime.fromisoformat(m["ts"].replace("Z", "+00:00"))
        if ts.tzinfo is not None:
            ts = ts.astimezone(timezone.utc)
        stamp = f"{_MONTHS[ts.month - 1]} {ts.day:2d} {ts:%H:%M:%S}"
    except ValueError:
        return line
    app = m["app"] if m["app"] != "-" else "unknown"
    pid = f"[{m['pid']}]" if m["pid"] != "-" else ""
    return f"{stamp} {m['host']} {app}{pid}: {m['msg']}"


# ------------------------------------------------------------ source map
def load_source_map(path: str | Path | None) -> dict[str, str]:
    """sender address -> parser kind. Missing file -> empty map (all parsers tried)."""
    p = Path(path) if path else DEFAULT_SOURCE_MAP
    if not p.exists():
        return {}
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        data = json.loads(text) if text.strip() else {}
    else:
        try:
            import yaml  # type: ignore[import-untyped]
            data = yaml.safe_load(text) or {}
        except ImportError:
            data = _yaml_lite(text)
    sources = (data.get("sources", data) if isinstance(data, dict) else {}) or {}
    out: dict[str, str] = {}
    for sender, kind in dict(sources).items():
        kind = str(kind).strip().lower()
        if kind not in KINDS:
            raise ValueError(f"{p}: {sender}: kind {kind!r} not one of {sorted(KINDS)}")
        out[str(sender).strip()] = kind
    return out


def _yaml_lite(text: str) -> dict:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].rstrip()
        m = re.match(r"^\s+([^:\s]+):\s*(\S+)$", line)
        if m:
            out[m.group(1).strip("'\"")] = m.group(2).strip("'\"")
    return {"sources": out}


# -------------------------------------------------------------- listener
class SyslogListener:
    """Async UDP + TCP syslog receiver. Lines land on a thread-safe queue that
    lines() drains synchronously, so the (synchronous) Pipeline can consume it
    from a worker thread while the server's event loop keeps running."""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = DEFAULT_PORT,
        source_map: dict[str, str] | None = None,
        exclude: set[str] | None = None,
        udp: bool = True,
        tcp: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.source_map = dict(source_map or {})
        # HOMELAB: our own addresses, detected once at startup.
        self.exclude: set[str] = set(exclude) if exclude is not None else local_addresses()
        self.udp = udp
        self.tcp = tcp
        self.stats = IngestStats()
        self.queue: queue.Queue[TaggedLine | None] = queue.Queue(maxsize=QUEUE_MAX)
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._tcp_server: asyncio.AbstractServer | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.bound_port: int | None = None

    # --------------------------------------------------------- ingestion
    def feed(self, sender: str, raw: str) -> bool:
        """Tag, filter and enqueue one raw line. Returns True if enqueued."""
        sender = sender.split("%")[0]
        self.stats.received += 1
        if sender in self.exclude:
            self.stats.dropped_local += 1
            if self.stats.dropped_local in (1, 10, 100) or self.stats.dropped_local % 1000 == 0:
                log.warning("dropped %d line(s) from Sentinel's own address %s (feedback loop guard)",
                            self.stats.dropped_local, sender)
            return False
        if len(raw) > MAX_LINE:
            self.stats.dropped_oversize += 1
            return False
        line = normalize_syslog(raw)
        if not line.strip():
            self.stats.dropped_empty += 1
            return False
        self.stats.by_sender[sender] = self.stats.by_sender.get(sender, 0) + 1
        tagged = TaggedLine(sender=sender, line=line, kind=self.source_map.get(sender))
        try:
            self.queue.put_nowait(tagged)
        except queue.Full:
            log.error("ingest queue full; dropping line from %s", sender)
            return False
        return True

    # -------------------------------------------------------------- async
    class _UdpProto(asyncio.DatagramProtocol):
        def __init__(self, outer: "SyslogListener") -> None:
            self.outer = outer

        def datagram_received(self, data: bytes, addr: tuple) -> None:
            text = data.decode("utf-8", errors="replace")
            for raw in text.splitlines() or [text]:
                self.outer.feed(addr[0], raw)

    async def _tcp_conn(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        sender = peer[0] if peer else "?"
        try:
            while True:
                chunk = await reader.readline()
                if not chunk:
                    break
                self.feed(sender, chunk.decode("utf-8", errors="replace"))
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._loop = loop
        if self.udp:
            self._udp_transport, _ = await loop.create_datagram_endpoint(
                lambda: self._UdpProto(self), local_addr=(self.host, self.port))
            self.bound_port = self._udp_transport.get_extra_info("sockname")[1]
        if self.tcp:
            self._tcp_server = await asyncio.start_server(
                self._tcp_conn, self.host, self.port if self.bound_port is None else self.bound_port)
            self.bound_port = self._tcp_server.sockets[0].getsockname()[1]
        log.info("syslog listener on %s:%d (udp=%s tcp=%s) excluding %d local address(es); "
                 "%d mapped sender(s)", self.host, self.bound_port, self.udp, self.tcp,
                 len(self.exclude), len(self.source_map))

    async def stop(self) -> None:
        if self._udp_transport is not None:
            self._udp_transport.close()
        if self._tcp_server is not None:
            self._tcp_server.close()
            await self._tcp_server.wait_closed()
        self.queue.put_nowait(None)

    async def serve_forever(self) -> None:
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()

    # ------------------------------------------------------------- thread
    def run_in_thread(self) -> threading.Thread:
        """For the CLI: own event loop on a daemon thread, main thread consumes lines()."""
        ready = threading.Event()

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _main() -> None:
                await self.start()
                ready.set()
                await asyncio.Event().wait()

            try:
                loop.run_until_complete(_main())
            finally:
                loop.close()

        self._thread = threading.Thread(target=_run, name="syslog", daemon=True)
        self._thread.start()
        ready.wait(5)
        return self._thread

    # -------------------------------------------------------------- lines
    def lines(self, tick_sec: float = 1.0, stop: threading.Event | None = None) -> Iterator[TaggedLine | None]:
        """Blocking generator. Yields TaggedLine, or None every `tick_sec` of
        silence so the pipeline can flush idle aggregation windows."""
        while stop is None or not stop.is_set():
            try:
                item = self.queue.get(timeout=tick_sec)
            except queue.Empty:
                yield None
                continue
            if item is None:
                return
            yield item
