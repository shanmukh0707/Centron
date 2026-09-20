"""Time-window aggregation: many ParsedLines -> one AggregatedEvent per key.

Windows are fixed buckets of `window_sec` aligned to the epoch. Inside a
bucket, lines are keyed on (signature, source_ip, dst_host); duplicates
collapse into raw_count and the distinct entity sets. A bucket is emitted when
a later line arrives past its end, or on flush().

Correlation gate (deterministic, pre-model): if 3+ distinct source_ips appear
anywhere in one window, every event from that window carries correlated=True.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from parsers import ParsedLine

WINDOW_MIN_SEC = 30
WINDOW_MAX_SEC = 60
CORRELATION_MIN_SOURCES = 3
RAW_SAMPLE_LIMIT = 3

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class AggregatedEvent:
    signature: str
    source_ip: str
    dst_host: str
    window_start: datetime
    window_end: datetime
    first_ts: datetime
    last_ts: datetime
    raw_count: int = 0
    source_ips: frozenset[str] = frozenset()
    dst_hosts: frozenset[str] = frozenset()
    users: frozenset[str] = frozenset()
    ports: frozenset[int] = frozenset()
    actions: frozenset[str] = frozenset()
    raw_samples: list[str] = field(default_factory=list)
    correlated: bool = False
    window_source_count: int = 0

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.signature, self.source_ip, self.dst_host)


@dataclass
class _Bucket:
    users: set[str] = field(default_factory=set)
    ports: set[int] = field(default_factory=set)
    actions: set[str] = field(default_factory=set)
    raw_samples: list[str] = field(default_factory=list)
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    raw_count: int = 0


class Aggregator:
    def __init__(self, window_sec: int = 30) -> None:
        if not WINDOW_MIN_SEC <= window_sec <= WINDOW_MAX_SEC:
            raise ValueError(f"window_sec must be {WINDOW_MIN_SEC}..{WINDOW_MAX_SEC}, got {window_sec}")
        self.window_sec = window_sec
        # window_index -> key -> bucket
        self._windows: dict[int, dict[tuple[str, str, str], _Bucket]] = {}

    def _window_index(self, ts: datetime) -> int:
        return int((ts - _EPOCH).total_seconds()) // self.window_sec

    def add(self, line: ParsedLine) -> list[AggregatedEvent]:
        """Absorb one line; return any windows that closed because of it."""
        idx = self._window_index(line.ts)
        key = (line.signature, line.source_ip, line.dst_host)
        bucket = self._windows.setdefault(idx, {}).setdefault(key, _Bucket())
        bucket.raw_count += 1
        if line.user is not None:
            bucket.users.add(line.user)
        if line.port is not None:
            bucket.ports.add(line.port)
        bucket.actions.add(line.action)
        if len(bucket.raw_samples) < RAW_SAMPLE_LIMIT:
            bucket.raw_samples.append(line.raw)
        if bucket.first_ts is None or line.ts < bucket.first_ts:
            bucket.first_ts = line.ts
        if bucket.last_ts is None or line.ts > bucket.last_ts:
            bucket.last_ts = line.ts

        closed = [i for i in self._windows if i < idx]
        out: list[AggregatedEvent] = []
        for i in sorted(closed):
            out.extend(self._emit(i))
        return out

    def flush(self) -> list[AggregatedEvent]:
        out: list[AggregatedEvent] = []
        for i in sorted(self._windows):
            out.extend(self._emit(i))
        return out

    def _emit(self, idx: int) -> list[AggregatedEvent]:
        buckets = self._windows.pop(idx)
        start = _EPOCH + timedelta(seconds=idx * self.window_sec)
        end = start + timedelta(seconds=self.window_sec)
        sources = {key[1] for key in buckets}
        correlated = len(sources) >= CORRELATION_MIN_SOURCES
        events: list[AggregatedEvent] = []
        for (signature, source_ip, dst_host), b in buckets.items():
            assert b.first_ts is not None and b.last_ts is not None
            events.append(AggregatedEvent(
                signature=signature, source_ip=source_ip, dst_host=dst_host,
                window_start=start, window_end=end, first_ts=b.first_ts, last_ts=b.last_ts,
                raw_count=b.raw_count,
                source_ips=frozenset({source_ip}), dst_hosts=frozenset({dst_host}),
                users=frozenset(b.users), ports=frozenset(b.ports), actions=frozenset(b.actions),
                raw_samples=list(b.raw_samples),
                correlated=correlated, window_source_count=len(sources),
            ))
        events.sort(key=lambda e: (e.first_ts, e.key))
        return events
