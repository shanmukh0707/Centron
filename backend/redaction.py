"""Deterministic redaction: real values <-> stable tokens, mapping stays local.

Runs BEFORE any model sees text. The model never redacts; it only ever sees
HOST_A / USER_1 / MAC_1 and hands the same tokens back in action_params, which
Python resolves with localize().

Token namespaces:
  HOST_A, HOST_B, ...   IPv4, IPv6 and hostnames (letters: A..Z, AA, AB, ...)
  USER_1, USER_2, ...   usernames
  MAC_1,  MAC_2,  ...   MAC addresses

redact(text) and localize(text) are exact inverses for any text that does not
already contain a token-shaped word.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"\b(?:HOST_[A-Z]+|USER_\d+|MAC_\d+)\b")
# Union of schemas.HostId / UserId shapes. Values that do not fit (spaces,
# quotes, injected sentences) are still tokenized, but the pipeline refuses to
# restore them into human-facing prose.
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9._@:-]{1,128}$")

_MAC_RE = re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")
# Full 8-group form, or a compressed form containing "::" with at least one hex
# group. Deliberately does NOT match hh:mm:ss clock literals.
_IPV6_RE = re.compile(
    r"(?<![\w:.])(?:"
    r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
    r"|[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6}::(?:[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6})?"
    r"|::[0-9A-Fa-f]{1,4}(?::[0-9A-Fa-f]{1,4}){0,6}"
    r")(?![\w:])"
)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# Dotted hostnames (FQDN-ish). Bare hostnames ("bastion") are not regex-
# detectable and must be registered from parser facts via register_host().
_FQDN_RE = re.compile(
    r"\b(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\b"
)
# sshd-style username context: "for root from", "for invalid user admin from",
# "user=alice". Registered usernames are replaced before this runs.
_USER_CTX_RE = re.compile(
    r"(?:\bfor\s+(?:invalid\s+user\s+)?|\buser=)(?P<user>[A-Za-z0-9._@-]+)(?=\s+from\b|\s|$)"
)


class LeakError(RuntimeError):
    """A real value was about to leave the box (to the model or to Claude). Refuse."""


def _letters(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA ..."""
    out = ""
    n += 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


def _letters_to_int(s: str) -> int:
    """Inverse of _letters: A -> 0, Z -> 25, AA -> 26 ..."""
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


@dataclass
class RedactionMap:
    """Session-local bidirectional mapping. Never serialized off-box."""

    _value_to_token: dict[str, str] = field(default_factory=dict)
    _token_to_value: dict[str, str] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=lambda: {"HOST": 0, "USER": 0, "MAC": 0})
    _known_users: set[str] = field(default_factory=set)
    _known_hosts: set[str] = field(default_factory=set)

    # ----------------------------------------------------------------- tokens
    def _token_for(self, value: str, kind: str) -> str:
        tok = self._value_to_token.get(value)
        if tok is not None:
            return tok
        n = self._counts[kind]
        self._counts[kind] = n + 1
        tok = f"HOST_{_letters(n)}" if kind == "HOST" else f"{kind}_{n + 1}"
        self._value_to_token[value] = tok
        self._token_to_value[tok] = value
        return tok

    def register_user(self, value: str) -> str:
        value = value.strip()
        if not value or TOKEN_RE.fullmatch(value):
            return value
        self._known_users.add(value)
        return self._token_for(value, "USER")

    def register_host(self, value: str) -> str:
        value = value.strip()
        if not value or TOKEN_RE.fullmatch(value):
            return value
        self._known_hosts.add(value)
        return self._token_for(value, "HOST")

    def tokens(self) -> set[str]:
        return set(self._token_to_value)

    def known_values(self) -> set[str]:
        return set(self._value_to_token)

    def pairs(self) -> dict[str, str]:
        """token -> value, for persistence."""
        return dict(self._token_to_value)

    def preload(self, pairs: dict[str, str]) -> None:
        """Restore token->value pairs from persistence (db.load_redaction_map).

        Counters are advanced past every restored token so a restarted
        Sentinel never re-issues HOST_A for a different host.
        """
        for tok, value in pairs.items():
            if not TOKEN_RE.fullmatch(tok) or tok in self._token_to_value:
                continue
            self._token_to_value[tok] = value
            self._value_to_token[value] = tok
            kind, _, tail = tok.partition("_")
            if kind == "HOST":
                n = _letters_to_int(tail) + 1
                self._known_hosts.add(value)
            else:
                n = int(tail)
                if kind == "USER":
                    self._known_users.add(value)
            self._counts[kind] = max(self._counts[kind], n)

    def token_for(self, value: str) -> str | None:
        return self._value_to_token.get(value)

    def value_for(self, token: str) -> str | None:
        return self._token_to_value.get(token)

    # ---------------------------------------------------------------- redact
    def _sub_known(self, text: str, values: set[str], kind: str) -> str:
        for value in sorted(values, key=len, reverse=True):
            pat = re.compile(r"(?<![\w.-])" + re.escape(value) + r"(?![\w.-])")
            text = pat.sub(lambda m, v=value: self._token_for(v, kind), text)
        return text

    def _sub_regex(self, text: str, pattern: re.Pattern[str], kind: str, group: int | str = 0) -> str:
        def repl(m: re.Match[str]) -> str:
            value = m.group(group)
            if TOKEN_RE.fullmatch(value):
                return m.group(0)
            tok = self._token_for(value, kind)
            if group == 0:
                return tok
            start, end = m.span(group)
            return m.group(0)[: start - m.start()] + tok + m.group(0)[end - m.start():]

        return pattern.sub(repl, text)

    def redact(self, text: str) -> str:
        text = self._sub_known(text, self._known_users, "USER")
        text = self._sub_known(text, self._known_hosts, "HOST")
        text = self._sub_regex(text, _MAC_RE, "MAC")
        text = self._sub_regex(text, _IPV6_RE, "HOST")
        text = self._sub_regex(text, _IPV4_RE, "HOST")
        text = self._sub_regex(text, _FQDN_RE, "HOST")
        text = self._sub_regex(text, _USER_CTX_RE, "USER", "user")
        return text

    # -------------------------------------------------------------- localize
    def localize(self, text: str) -> str:
        """Exact inverse of redact(). Unknown token-shaped words are left alone."""
        return TOKEN_RE.sub(lambda m: self._token_to_value.get(m.group(0), m.group(0)), text)
