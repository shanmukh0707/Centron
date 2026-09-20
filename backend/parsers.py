"""Strict regex parsers: raw log line -> ParsedLine, or None (counted, dropped).

The `signature` is derived HERE and only here, from which regex matched.
No model ever assigns or rewrites a signature. Free text captured from a line
(e.g. a username) lands in a typed field and is never interpolated into
anything downstream; log text is attacker-controlled.

Supported line shapes (syslog "Mon DD HH:MM:SS host proc[pid]: msg"):
  sshd    Failed password for [invalid user ]<user> from <ip> port <n> ssh2
  sshd    Accepted <method> for <user> from <ip> port <n> ssh2
  kernel  [UFW BLOCK] ... SRC=<ip> DST=<ip> ... PROTO=TCP ... DPT=<n> ... SYN
  pihole  pihole-FTL: gravity blocked <domain> from <client ip>
Anything else is unparseable by design.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Iterator

_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}

_SYSLOG_RE = re.compile(
    r"^(?P<mon>[A-Z][a-z]{2}) {1,2}(?P<day>\d{1,2}) (?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2}) "
    r"(?P<host>[A-Za-z0-9._-]+) (?P<proc>[A-Za-z0-9._-]+)(?:\[\d+\])?: (?P<msg>.*)$"
)
_IPV4 = r"(?:\d{1,3}\.){3}\d{1,3}"

# Username is captured lazily up to " from <ip> port <n>", so sshd's verbatim
# echo of whatever the client sent (spaces included) stays inside this field.
_SSH_FAILED_RE = re.compile(
    rf"^Failed password for (?:invalid user )?(?P<user>.+?) from (?P<ip>{_IPV4}) port (?P<port>\d{{1,5}}) ssh2$"
)
_SSH_ACCEPTED_RE = re.compile(
    rf"^Accepted (?P<method>password|publickey|keyboard-interactive/pam) for (?P<user>.+?) "
    rf"from (?P<ip>{_IPV4}) port (?P<port>\d{{1,5}}) ssh2\b.*$"
)
_UFW_BLOCK_RE = re.compile(
    rf"^\[UFW BLOCK\] IN=\S* OUT=\S* (?:MAC=\S* )?SRC=(?P<src>{_IPV4}) DST=(?P<dst>{_IPV4}) "
    rf".*\bPROTO=TCP\b.*\bDPT=(?P<port>\d{{1,5}})\b.*\bSYN\b.*$"
)
_PIHOLE_BLOCK_RE = re.compile(
    rf"^gravity blocked (?P<domain>[A-Za-z0-9.-]+) from (?P<ip>{_IPV4})$"
)


@dataclass(frozen=True)
class ParsedLine:
    ts: datetime
    source_ip: str
    dst_host: str
    user: str | None
    port: int | None
    action: str
    signature: str
    raw: str


@dataclass(frozen=True)
class SignatureMeta:
    kind: str        # Source.kind
    collector: str   # Source.collector
    title: str       # EventData.title, <= 60 chars, deterministic


SIGNATURE_META: dict[str, SignatureMeta] = {
    "ssh.auth.brute_force": SignatureMeta("auth", "sshd", "Repeated failed SSH logins"),
    "ssh.auth.success": SignatureMeta("auth", "sshd", "Successful SSH login"),
    "net.portscan": SignatureMeta("firewall", "ufw", "Inbound port scan blocked at firewall"),
    "dns.blocked": SignatureMeta("dns", "pihole", "DNS queries blocked by Pi-hole"),
}


# Which parser family a syslog `proc` belongs to; used by ingest.py's source_map.
PROC_KIND: dict[str, str] = {"sshd": "auth", "kernel": "firewall", "pihole-FTL": "dns"}
KINDS = frozenset(PROC_KIND.values())


class LineParser:
    """Stateful only for counters. `year` fills the syslog timestamp's gap."""

    def __init__(self, year: int | None = None) -> None:
        self.year = year or datetime.now(timezone.utc).year
        self.parsed = 0
        self.dropped = 0

    def parse(self, line: str, kind: str | None = None) -> ParsedLine | None:
        """`kind` (auth | firewall | dns) restricts which family is tried; None tries all."""
        result = self._parse(line.rstrip("\r\n"), kind)
        if result is None:
            self.dropped += 1
        else:
            self.parsed += 1
        return result

    def parse_many(self, lines: Iterable[str]) -> Iterator[ParsedLine]:
        for line in lines:
            p = self.parse(line)
            if p is not None:
                yield p

    def _parse(self, line: str, kind: str | None = None) -> ParsedLine | None:
        head = _SYSLOG_RE.match(line)
        if head is None:
            return None
        try:
            ts = datetime(
                self.year, _MONTHS[head["mon"]], int(head["day"]),
                int(head["h"]), int(head["m"]), int(head["s"]), tzinfo=timezone.utc,
            )
        except (KeyError, ValueError):
            return None
        host, proc, msg = head["host"], head["proc"], head["msg"]
        if kind is not None and PROC_KIND.get(proc) != kind:
            return None

        if proc == "sshd":
            m = _SSH_FAILED_RE.match(msg)
            if m:
                return ParsedLine(ts, m["ip"], host, m["user"], 22, "failed_password",
                                  "ssh.auth.brute_force", line)
            m = _SSH_ACCEPTED_RE.match(msg)
            if m:
                return ParsedLine(ts, m["ip"], host, m["user"], 22, f"accepted_{m['method'].split('/')[0]}",
                                  "ssh.auth.success", line)
            return None

        if proc == "kernel":
            m = _UFW_BLOCK_RE.match(msg)
            if m:
                port = int(m["port"])
                if port > 65535:
                    return None
                return ParsedLine(ts, m["src"], m["dst"], None, port, "blocked_syn",
                                  "net.portscan", line)
            return None

        if proc == "pihole-FTL":
            m = _PIHOLE_BLOCK_RE.match(msg)
            if m:
                return ParsedLine(ts, m["ip"], host, None, 53, "gravity_blocked",
                                  "dns.blocked", line)
            return None

        return None
