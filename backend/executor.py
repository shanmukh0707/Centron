"""Playbook executor: the only place a playbook turns into an effect.

execute(playbook, resolved_params, dry_run) -> ExecResult(status, approval_id, expires_at)

Check order, every call, no shortcuts:
  1. assets.is_protected(target)      -> "denied", never reaches the caps
  2. schemas.validate_and_classify()  -> invalid params "denied"; caps decide
                                         auto vs pending_approval
  3. circuit breaker                  -> auto degrades to pending_approval
  4. auto-action budget               -> auto degrades to pending_approval
Only then does anything run.

Status is the REAL outcome. Dry run (the default) logs
"[DRY RUN] would execute ..." and reports auto_executed because that is what
the run mode means: the decision was made and recorded. Live mode reports
"failed" when the enforcement stub raises, never an optimistic guess.

HOMELAB, for whoever lands live enforcement: expiry must live in the
enforcement layer itself, not in an application timer. Use an nft set with
`timeout` (`nft add element inet sentinel blocked4 { 1.2.3.4 timeout 3600s }`)
or a pfSense schedule, so a rule self-removes even if Sentinel dies between
adding it and lifting it. list_expiring() exists for bookkeeping and for the
action_update frames, not as the thing that keeps the network unblocked.

Read-only playbooks (watch, notify_only, snapshot_evidence) change nothing on
the network, so they run live and do not count toward the budget or the
breaker: a log flood must not turn "notify" into fifty approval prompts.

The caps are schemas.CAPS and are never relaxed here. An RFC1918 target on
block_ip / rate_limit stays pending_approval no matter how tempting it is
to auto-block a noisy neighbour.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator
from uuid import UUID

from pydantic import ValidationError

from assets import AssetRegistry
from db import Database
from schemas import CAPS, PlaybookParamsAdapter, validate_and_classify

log = logging.getLogger("sentinel.executor")

APPROVAL_TTL_SEC = 600
AUTO_BUDGET_PER_HOUR = 10
BREAKER_THRESHOLD = 5          # more than this many auto executions ...
BREAKER_WINDOW_SEC = 600       # ... inside this window trips the breaker
READ_ONLY = frozenset(p for p, cap in CAPS.items() if cap.get("read_only"))


@dataclass(frozen=True)
class ExecResult:
    status: str                      # schemas.ActionStatus
    approval_id: str | None = None
    expires_at: datetime | None = None
    reason: str = ""
    requires_approval: bool = True

    def __iter__(self) -> Iterator[Any]:  # allows: status, approval_id, expires_at = execute(...)
        yield self.status
        yield self.approval_id
        yield self.expires_at


def target_of(playbook: str, params: dict[str, Any]) -> str | None:
    """The network entity a playbook would act on, or None for revoke/notify."""
    if playbook in ("block_ip", "rate_limit", "watch", "snapshot_evidence"):
        v = params.get("target")
    elif playbook == "isolate_host":
        v = params.get("host_id")
    else:
        return None
    return str(v) if v is not None else None


class Executor:
    def __init__(
        self,
        db: Database,
        assets: AssetRegistry,
        dry_run: bool = True,
        clock: Callable[[], float] = time.time,
        approval_ttl_sec: int = APPROVAL_TTL_SEC,
        auto_budget_per_hour: int = AUTO_BUDGET_PER_HOUR,
        breaker_threshold: int = BREAKER_THRESHOLD,
        breaker_window_sec: int = BREAKER_WINDOW_SEC,
    ) -> None:
        self.db = db
        self.assets = assets
        self.dry_run = dry_run
        self.clock = clock
        self.approval_ttl = timedelta(seconds=approval_ttl_sec)
        self.auto_budget = auto_budget_per_hour
        self.breaker_threshold = breaker_threshold
        self.breaker_window = timedelta(seconds=breaker_window_sec)
        self._breaker_tripped_at: datetime | None = None
        self._auto_times: list[datetime] = []  # enforcement auto-executions, newest last
        self.watchlist: dict[str, datetime] = {}
        self.snapshots: list[dict[str, Any]] = []
        self.denied = 0
        self.degraded = 0
        if not dry_run:
            assets.require_configured_for_live()

    # ---------------------------------------------------------------- time
    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc)

    # ------------------------------------------------------------- breaker
    @property
    def breaker_tripped(self) -> bool:
        return self._breaker_tripped_at is not None

    def breaker_state(self) -> dict[str, Any]:
        now = self.now()
        return {
            "tripped": self.breaker_tripped,
            "tripped_at": self._breaker_tripped_at.isoformat() if self._breaker_tripped_at else None,
            "auto_last_10m": self._auto_count(now - self.breaker_window),
            "auto_last_1h": self._auto_count(now - timedelta(hours=1)),
            "threshold": self.breaker_threshold,
            "budget_per_hour": self.auto_budget,
            "dry_run": self.dry_run,
        }

    def reset(self) -> None:
        """Human reset. Clears the trip; the sliding counters keep their history."""
        self._breaker_tripped_at = None
        log.warning("circuit breaker reset by operator")

    def _auto_count(self, since: datetime) -> int:
        self._auto_times = [t for t in self._auto_times if t >= since - timedelta(hours=1)]
        return sum(1 for t in self._auto_times if t >= since)

    # ------------------------------------------------------------- execute
    def execute(
        self,
        playbook: str,
        resolved_params: dict[str, Any],
        dry_run: bool | None = None,
        event_id: UUID | str | None = None,
    ) -> ExecResult:
        dry = self.dry_run if dry_run is None else dry_run
        params = {k: v for k, v in resolved_params.items() if k != "playbook"}
        now = self.now()
        event_id = str(event_id) if event_id is not None else "unknown"

        # 1. protected asset: refused before anything else is consulted
        target = target_of(playbook, params)
        if target is not None and self.assets.is_protected(target):
            reason = f"{playbook}: target {target} is a protected asset"
            self.denied += 1
            log.warning("DENIED %s %s: %s", playbook, params, reason)
            self._record(event_id, playbook, params, "denied", None, now)
            return ExecResult("denied", None, None, reason, True)

        # 2. caps
        allowed, needs_approval, reason = validate_and_classify(params, playbook)
        if not allowed:
            self.denied += 1
            log.warning("DENIED %s %s: %s", playbook, params, reason)
            self._record(event_id, playbook, params, "denied", None, now)
            return ExecResult("denied", None, None, reason, True)
        if needs_approval:
            return self._pend(event_id, playbook, params, reason, now)

        # 3 + 4. breaker and budget, enforcement playbooks only
        if playbook not in READ_ONLY:
            if self.breaker_tripped:
                self.degraded += 1
                return self._pend(event_id, playbook, params,
                                  "circuit breaker open: auto execution suspended until reset", now)
            if self._auto_count(now - self.breaker_window) >= self.breaker_threshold:
                self._breaker_tripped_at = now
                self.degraded += 1
                log.error("CIRCUIT BREAKER TRIPPED: >%d auto executions in %ds; degrading to pending_approval",
                          self.breaker_threshold, int(self.breaker_window.total_seconds()))
                return self._pend(event_id, playbook, params,
                                  f"circuit breaker tripped: more than {self.breaker_threshold} auto "
                                  f"executions in {int(self.breaker_window.total_seconds() // 60)} minutes", now)
            if self._auto_count(now - timedelta(hours=1)) >= self.auto_budget:
                self.degraded += 1
                return self._pend(event_id, playbook, params,
                                  f"auto-action budget {self.auto_budget}/h exhausted", now)

        # execute
        ok, why = self._run(playbook, params, dry)
        if not ok:
            self._record(event_id, playbook, params, "failed", None, now)
            return ExecResult("failed", None, None, why, False)
        if playbook not in READ_ONLY:
            self._auto_times.append(now)
        expires = self._expiry(params, now)
        self._record(event_id, playbook, params, "auto_executed", expires, now, executed_at=now)
        return ExecResult("auto_executed", None, expires, why, False)

    def _pend(self, event_id: str, playbook: str, params: dict[str, Any], reason: str, now: datetime) -> ExecResult:
        approval_id = "apr_" + secrets.token_hex(8)
        expires = now + self.approval_ttl
        self._record(event_id, playbook, params, "pending_approval", expires, now, approval_id=approval_id)
        log.info("PENDING %s %s -> %s (%s)", playbook, params, approval_id, reason)
        return ExecResult("pending_approval", approval_id, expires, reason, True)

    # ----------------------------------------------------------- approvals
    def approve(self, approval_id: str, dry_run: bool | None = None) -> ExecResult:
        """A biometric-approved decision arrived. Runs the recorded action for real."""
        dry = self.dry_run if dry_run is None else dry_run
        row = self.db.get_action(approval_id)
        now = self.now()
        if row is None:
            return ExecResult("failed", approval_id, None, "unknown approval_id", True)
        if row["status"] != "pending_approval":
            return ExecResult("failed", approval_id, row["expires_at"], f"approval already {row['status']}", True)
        if row["expires_at"] is not None and now > row["expires_at"]:
            self.db.update_action(approval_id, "expired")
            return ExecResult("expired", approval_id, row["expires_at"], "decision arrived after expires_at", True)
        params = {k: v for k, v in row["params"].items() if k != "playbook"}
        target = target_of(row["playbook"], params)
        if target is not None and self.assets.is_protected(target):
            self.db.update_action(approval_id, "denied")
            return ExecResult("denied", approval_id, None, f"target {target} is a protected asset", True)
        ok, why = self._run(row["playbook"], params, dry)
        if not ok:
            self.db.update_action(approval_id, "failed", now)
            return ExecResult("failed", approval_id, None, why, True)
        expires = self._expiry(params, now)
        self.db.update_action(approval_id, "approved", now)
        return ExecResult("approved", approval_id, expires, why, True)

    def deny(self, approval_id: str) -> ExecResult:
        row = self.db.get_action(approval_id)
        if row is None:
            return ExecResult("failed", approval_id, None, "unknown approval_id", True)
        self.db.update_action(approval_id, "denied")
        return ExecResult("denied", approval_id, None, "denied on device", True)

    def expire(self, approval_id: str) -> None:
        self.db.update_action(approval_id, "expired")

    def list_expiring(self, within: timedelta | None = None) -> list[dict[str, Any]]:
        return self.db.list_expiring(within=within, now=self.now())

    # ----------------------------------------------------------------- run
    def _run(self, playbook: str, params: dict[str, Any], dry: bool) -> tuple[bool, str]:
        if dry:
            log.info("[DRY RUN] would execute %s %s", playbook, params)
            return True, f"[DRY RUN] would execute {playbook} {params}"
        try:
            return True, self._live(playbook, params)
        except NotImplementedError as e:
            log.error("live execution of %s not implemented: %s", playbook, e)
            return False, f"live {playbook} not implemented: {e}"
        except Exception as e:  # noqa: BLE001 - a broken enforcement layer must report failed, not raise
            log.exception("live execution of %s failed", playbook)
            return False, f"live {playbook} failed: {e}"

    def _live(self, playbook: str, params: dict[str, Any]) -> str:
        """Live effects. Read-only playbooks are real; enforcement ones are stubs.

        HOMELAB: when these stubs become real, put the expiry in the rule
        (nft `timeout`), not in a Python timer. See the module docstring.
        """
        if playbook == "watch":
            self.watchlist[str(params["target"])] = self.now() + timedelta(seconds=int(params["duration_sec"]))
            return f"watching {params['target']} for {params['duration_sec']}s"
        if playbook == "notify_only":
            return f"notify via {params.get('channel', 'app')}"
        if playbook == "snapshot_evidence":
            self.snapshots.append({"at": self.now().isoformat(), **params})
            return f"evidence window recorded for {params['target']} ({params['scope']}, {params['window_sec']}s)"
        if playbook == "block_ip":
            raise NotImplementedError(
                f"nft add element inet sentinel blocked4 {{ {params['target']} timeout {params['duration_sec']}s }}"
            )
        if playbook == "rate_limit":
            raise NotImplementedError(
                f"nft add element inet sentinel limited4 {{ {params['target']} timeout {params['duration_sec']}s }} "
                f"(limit rate {params['limit_conn_per_min']}/minute)"
            )
        if playbook == "isolate_host":
            raise NotImplementedError(
                f"nft add element inet sentinel isolated {{ {params['host_id']} timeout {params['duration_sec']}s }}"
            )
        if playbook == "revoke_session":
            raise NotImplementedError(f"identity provider: revoke sessions for user {params['user']}")
        raise NotImplementedError(f"unknown playbook {playbook}")

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _expiry(params: dict[str, Any], now: datetime) -> datetime | None:
        dur = params.get("duration_sec")
        return now + timedelta(seconds=int(dur)) if dur else None

    def _record(
        self, event_id: str, playbook: str, params: dict[str, Any], status: str,
        expires_at: datetime | None, now: datetime, approval_id: str | None = None,
        executed_at: datetime | None = None,
    ) -> None:
        approval_id = approval_id or ("act_" + secrets.token_hex(8))
        # Store the canonical typed form when the params validate; a denied
        # (protected / out-of-cap) action keeps the raw dict for the audit trail.
        try:
            canon = PlaybookParamsAdapter.validate_python({**params, "playbook": playbook}).model_dump(mode="json")
        except ValidationError:
            canon = {**params, "playbook": playbook}
        self.db.record_action(approval_id, event_id, playbook, canon, status, expires_at, executed_at)
