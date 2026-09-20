"""SQLite persistence: events, the redaction map, and recorded actions.

WAL mode, one connection, one lock. The server is async and calls in from a
thread pool, so every public method takes the writer lock; sqlite3 itself is
opened with check_same_thread=False. Reads go through the same lock, which is
fine at homelab volumes and keeps the reasoning simple.

HOMELAB: the redaction map is persistent on purpose. internal_log rows are
written localized (real values), but action_params and the Claude payload
carry tokens, and the model's few-shot memory of HOST_A must mean the same
host after a restart. Orphaned tokens would make localize() a no-op and a
token would leak onto the phone.

Timestamps are stored as RFC3339 UTC strings (schemas.format_ts) so rows are
readable with the sqlite3 CLI and sort lexically.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

from schemas import EventData, format_ts, now_utc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id            TEXT PRIMARY KEY,
    seq                 INTEGER,
    ts                  TEXT NOT NULL,
    signature           TEXT NOT NULL,
    severity            TEXT NOT NULL,
    internal_log        TEXT NOT NULL,
    tts_summary         TEXT NOT NULL,
    confidence          REAL NOT NULL,
    escalate            INTEGER NOT NULL,
    escalate_reason     TEXT,
    gate                TEXT,
    escalation_state    TEXT NOT NULL,
    escalation_verdict  TEXT,
    action_playbook     TEXT,
    action_params       TEXT,
    action_status       TEXT,
    entities            TEXT NOT NULL,
    raw_count           INTEGER NOT NULL,
    correlated          INTEGER NOT NULL,
    reviewed            INTEGER NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_signature_ts ON events (signature, ts);
CREATE INDEX IF NOT EXISTS events_escalation ON events (escalation_state, created_at);

CREATE TABLE IF NOT EXISTS redaction_map (
    token       TEXT PRIMARY KEY,
    real_value  TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS redaction_value ON redaction_map (real_value);

CREATE TABLE IF NOT EXISTS actions (
    approval_id TEXT PRIMARY KEY,
    event_id    TEXT NOT NULL,
    playbook    TEXT NOT NULL,
    params      TEXT NOT NULL,
    status      TEXT NOT NULL,
    expires_at  TEXT,
    executed_at TEXT
);
CREATE INDEX IF NOT EXISTS actions_expiry ON actions (status, expires_at);
"""

# escalation_state values that mean "Claude was (or is being) consulted" and
# therefore count against the cooldown and the global ceiling. queued_digest
# is deliberately excluded: it was rate-limited, no call was made.
CONSULTED_STATES = ("pending", "answered", "unreachable")


def _ts(dt: datetime | None) -> str | None:
    return None if dt is None else format_ts(dt)


def _parse_ts(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


class Database:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------ helpers
    def _rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    # ------------------------------------------------------------- events
    def insert_event(
        self,
        ev: EventData,
        *,
        escalation_state: str | None = None,
        escalate_reason: str | None = None,
        correlated: bool = False,
        seq: int | None = None,
    ) -> None:
        """Persist an emitted EventData. Always called, fallbacks included.

        `escalation_state` overrides the wire state so DB-only states such as
        queued_digest can be recorded while the frame still says pending.
        """
        now = format_ts(now_utc())
        esc = ev.escalation
        act = ev.action
        row = (
            str(ev.event_id),
            seq,
            format_ts(ev.window.end),
            ev.signature,
            ev.severity,
            ev.internal_log,
            ev.tts_summary,
            ev.confidence,
            int(esc.escalated),
            escalate_reason if escalate_reason is not None else esc.reason,
            esc.gate,
            escalation_state or esc.state,
            esc.verdict,
            act.playbook if act else None,
            json.dumps(act.params.model_dump(mode="json"), sort_keys=True) if act else None,
            act.status if act else None,
            json.dumps(ev.entities.model_dump(mode="json"), sort_keys=True),
            ev.source.raw_count,
            int(correlated),
            int(ev.reviewed),
            now,
            now,
        )
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO events (event_id, seq, ts, signature, severity, internal_log, "
                "tts_summary, confidence, escalate, escalate_reason, gate, escalation_state, "
                "escalation_verdict, action_playbook, action_params, action_status, entities, raw_count, "
                "correlated, reviewed, created_at, updated_at) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                row,
            )

    _EVENT_COLUMNS = {
        "seq", "escalation_state", "escalation_verdict", "action_status", "reviewed", "severity",
    }

    def update_event(self, event_id: UUID | str, **fields: Any) -> None:
        bad = set(fields) - self._EVENT_COLUMNS
        if bad:
            raise ValueError(f"update_event: not updatable: {sorted(bad)}")
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        vals = [int(v) if isinstance(v, bool) else v for v in fields.values()]
        with self._lock:
            self._conn.execute(
                f"UPDATE events SET {cols}, updated_at = ? WHERE event_id = ?",
                (*vals, format_ts(now_utc()), str(event_id)),
            )

    def get_event(self, event_id: UUID | str) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM events WHERE event_id = ?", (str(event_id),))
        return dict(r) if r else None

    def signature_seen_before(self, signature: str) -> bool:
        return self._one("SELECT 1 FROM events WHERE signature = ? LIMIT 1", (signature,)) is not None

    def escalations_since(self, signature: str, since: datetime) -> int:
        r = self._one(
            "SELECT COUNT(*) AS n FROM events WHERE signature = ? AND created_at >= ? "
            f"AND escalation_state IN ({','.join('?' * len(CONSULTED_STATES))})",
            (signature, format_ts(since), *CONSULTED_STATES),
        )
        return int(r["n"]) if r else 0

    def escalations_since_global(self, since: datetime) -> int:
        r = self._one(
            "SELECT COUNT(*) AS n FROM events WHERE created_at >= ? "
            f"AND escalation_state IN ({','.join('?' * len(CONSULTED_STATES))})",
            (format_ts(since), *CONSULTED_STATES),
        )
        return int(r["n"]) if r else 0

    def mark_reviewed(self, event_id: UUID | str, verdict: str | None, state: str = "answered") -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE events SET reviewed = ?, escalation_verdict = ?, escalation_state = ?, updated_at = ? "
                "WHERE event_id = ?",
                (int(state == "answered"), verdict, state, format_ts(now_utc()), str(event_id)),
            )

    def count_events(self) -> int:
        r = self._one("SELECT COUNT(*) AS n FROM events")
        return int(r["n"]) if r else 0

    # ------------------------------------------------------- redaction map
    def load_redaction_map(self) -> dict[str, str]:
        """token -> real_value for every token ever issued."""
        return {r["token"]: r["real_value"] for r in self._rows("SELECT token, real_value FROM redaction_map")}

    def save_tokens(self, pairs: dict[str, str], session_id: str) -> int:
        """Persist new token->value pairs. Existing tokens are left alone. Returns rows written."""
        now = format_ts(now_utc())
        n = 0
        with self._lock:
            for tok, val in pairs.items():
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO redaction_map (token, real_value, session_id, created_at) VALUES (?,?,?,?)",
                    (tok, val, session_id, now),
                )
                n += cur.rowcount
        return n

    # ------------------------------------------------------------ actions
    def record_action(
        self,
        approval_id: str,
        event_id: UUID | str,
        playbook: str,
        params: dict[str, Any],
        status: str,
        expires_at: datetime | None,
        executed_at: datetime | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO actions (approval_id, event_id, playbook, params, status, expires_at, "
                "executed_at) VALUES (?,?,?,?,?,?,?)",
                (approval_id, str(event_id), playbook, json.dumps(params, sort_keys=True, default=str), status,
                 _ts(expires_at), _ts(executed_at)),
            )

    def update_action(self, approval_id: str, status: str, executed_at: datetime | None = None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE actions SET status = ?, executed_at = COALESCE(?, executed_at) WHERE approval_id = ?",
                (status, _ts(executed_at), approval_id),
            )

    def get_action(self, approval_id: str) -> dict[str, Any] | None:
        r = self._one("SELECT * FROM actions WHERE approval_id = ?", (approval_id,))
        if r is None:
            return None
        d = dict(r)
        d["params"] = json.loads(d["params"])
        d["expires_at"] = _parse_ts(d["expires_at"])
        d["executed_at"] = _parse_ts(d["executed_at"])
        return d

    def list_expiring(self, within: timedelta | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
        """Actions with an expiry that is already past, or falls inside `within` of now.

        Covers both pending approvals (waiting on the phone) and auto-executed
        rules that must be lifted when their duration runs out.
        """
        now = now or now_utc()
        horizon = now + within if within else now
        rows = self._rows(
            "SELECT * FROM actions WHERE expires_at IS NOT NULL AND expires_at <= ? "
            "AND status IN ('pending_approval', 'auto_executed', 'approved') ORDER BY expires_at",
            (format_ts(horizon),),
        )
        out = []
        for r in rows:
            d = dict(r)
            d["params"] = json.loads(d["params"])
            d["expires_at"] = _parse_ts(d["expires_at"])
            d["executed_at"] = _parse_ts(d["executed_at"])
            out.append(d)
        return out

    def iter_actions(self) -> Iterator[dict[str, Any]]:
        for r in self._rows("SELECT * FROM actions ORDER BY rowid"):
            d = dict(r)
            d["params"] = json.loads(d["params"])
            yield d
