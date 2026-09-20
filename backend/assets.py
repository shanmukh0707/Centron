"""Protected assets: the hosts Sentinel must never act against.

Loaded from config, never hardcoded here:
  * a YAML (or JSON) file, default backend/protected_assets.yaml, see the
    shipped example with commented placeholders
  * SENTINEL_PROTECTED_ASSETS, a comma-separated list, appended on top
  * SENTINEL_ASSETS_FILE overrides the file path

HOMELAB: on top of the configured list, every address bound to this machine
and the default gateway from the routing table are added at startup. Sentinel
must never block the host it runs on or the gateway it needs to reach
anything; a mis-typed config must not be able to remove that floor.

The registry also pushes its entries into schemas.PROTECTED_ASSETS so the
playbook validators (which read that set live) agree with the executor.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import platform
import re
import socket
import subprocess
from dataclasses import dataclass, field
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from typing import Iterable

import schemas

log = logging.getLogger("sentinel.assets")

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "protected_assets.yaml"
ENV_LIST = "SENTINEL_PROTECTED_ASSETS"
ENV_FILE = "SENTINEL_ASSETS_FILE"

# Always protected regardless of config: loopback.
_FLOOR = ("127.0.0.0/8", "::1")


class ProtectedListEmpty(RuntimeError):
    """--live requested but nothing beyond auto-detected local addresses is protected."""


def _as_network(item: str) -> IPv4Network | IPv6Network | None:
    try:
        return ipaddress.ip_network(item.strip(), strict=False)
    except ValueError:
        return None


# ------------------------------------------------------------- detection
def local_addresses() -> set[str]:
    """Every address this machine answers on, best effort and cross-platform.
    Loopback is always included: a syslog daemon on this box forwards from it."""
    found: set[str] = {"127.0.0.1", "::1"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            found.add(info[4][0].split("%")[0])
    except OSError:
        pass
    # The UDP-connect trick reveals the address used for the default route
    # without sending anything.
    for family, probe in ((socket.AF_INET, "10.255.255.255"), (socket.AF_INET6, "2001:db8::1")):
        try:
            with socket.socket(family, socket.SOCK_DGRAM) as s:
                s.connect((probe, 1))
                found.add(s.getsockname()[0].split("%")[0])
        except OSError:
            pass
    if platform.system() == "Linux":
        found |= _linux_addresses()
    return {a for a in found if _as_network(a) is not None}


def _linux_addresses() -> set[str]:
    out: set[str] = set()
    try:
        txt = subprocess.run(["ip", "-o", "addr"], capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return out
    for m in re.finditer(r"\binet6?\s+([0-9a-fA-F.:]+)/\d+", txt):
        out.add(m.group(1))
    return out


def default_gateway() -> str | None:
    """Default IPv4 gateway from the routing table, or None if undeterminable."""
    system = platform.system()
    try:
        if system == "Linux":
            route = Path("/proc/net/route")
            if route.exists():
                for line in route.read_text().splitlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 3 and parts[1] == "00000000":
                        raw = int(parts[2], 16)
                        return socket.inet_ntoa(raw.to_bytes(4, "little"))
            txt = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=3).stdout
            m = re.search(r"default via (\S+)", txt)
            return m.group(1) if m else None
        if system == "Windows":
            txt = subprocess.run(["route", "print", "0.0.0.0"], capture_output=True, text=True, timeout=5).stdout
            m = re.search(r"^\s*0\.0\.0\.0\s+0\.0\.0\.0\s+(\d+\.\d+\.\d+\.\d+)", txt, re.M)
            return m.group(1) if m else None
        txt = subprocess.run(["netstat", "-rn"], capture_output=True, text=True, timeout=5).stdout  # macOS/BSD
        m = re.search(r"^default\s+(\d+\.\d+\.\d+\.\d+)", txt, re.M)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


# ---------------------------------------------------------------- config
def _read_config_file(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    data: object
    if path.suffix.lower() == ".json":
        data = json.loads(text) if text.strip() else {}
    else:
        try:
            import yaml  # type: ignore[import-untyped]
        except ImportError:  # keep the box bootable without PyYAML
            data = _yaml_lite(text)
        else:
            data = yaml.safe_load(text) or {}
    if isinstance(data, dict):
        items = data.get("protected_assets") or []
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError(f"{path}: protected_assets must be a list")
    return [str(x).strip() for x in items if str(x).strip()]


def _yaml_lite(text: str) -> dict:
    """Enough YAML to read the shipped file: one `protected_assets:` list of scalars."""
    items = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].rstrip()
        m = re.match(r"^\s*-\s*(.+)$", line)
        if m:
            items.append(m.group(1).strip().strip("'\""))
    return {"protected_assets": items}


def _env_list() -> list[str]:
    raw = os.environ.get(ENV_LIST, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


# -------------------------------------------------------------- registry
@dataclass
class AssetRegistry:
    configured: set[str] = field(default_factory=set)   # from file + env
    detected: set[str] = field(default_factory=set)     # local addrs + gateway
    config_path: Path = DEFAULT_CONFIG
    _nets: list[IPv4Network | IPv6Network] = field(default_factory=list, repr=False)
    _ids: set[str] = field(default_factory=set, repr=False)

    @classmethod
    def load(
        cls,
        config_path: str | Path | None = None,
        *,
        auto_detect: bool = True,
        extra: Iterable[str] = (),
        env: bool = True,
    ) -> "AssetRegistry":
        path = Path(config_path or os.environ.get(ENV_FILE) or DEFAULT_CONFIG)
        configured = set(_read_config_file(path)) | set(extra)
        if env:
            configured |= set(_env_list())
        detected: set[str] = set(_FLOOR)
        if auto_detect:
            detected |= local_addresses()
            gw = default_gateway()
            if gw:
                detected.add(gw)
        reg = cls(configured=configured, detected=detected, config_path=path)
        reg._rebuild()
        reg.publish()
        log.info("protected assets: %d configured, %d auto-detected (%s)",
                 len(configured), len(detected), path)
        return reg

    @classmethod
    def of(cls, items: Iterable[str], detected: Iterable[str] = _FLOOR) -> "AssetRegistry":
        """An explicit registry that does NOT publish into schemas (tests, embedding)."""
        reg = cls(configured=set(items), detected=set(detected))
        reg._rebuild()
        return reg

    def _rebuild(self) -> None:
        self._nets, self._ids = [], set()
        for item in self.configured | self.detected:
            net = _as_network(item)
            if net is not None:
                self._nets.append(net)
            else:
                self._ids.add(item.lower())

    def publish(self) -> None:
        """Make schemas' validators see the same list the executor uses."""
        schemas.PROTECTED_ASSETS.update(self.configured | self.detected)

    def all(self) -> set[str]:
        return self.configured | self.detected

    def is_protected(self, addr: str | IPv4Network | IPv6Network) -> bool:
        """True if `addr` (host id, IP or CIDR) touches a protected asset."""
        if isinstance(addr, str):
            if addr.strip().lower() in self._ids:
                return True
            net = _as_network(addr)
            if net is None:
                return False
        else:
            net = addr
        return any(net.version == p.version and net.overlaps(p) for p in self._nets)

    def require_configured_for_live(self) -> None:
        """HOMELAB startup guard: refuse --live with an unconfigured list."""
        if not self.configured:
            raise ProtectedListEmpty(
                f"--live refused: no protected assets configured beyond auto-detected local addresses. "
                f"Fill in {self.config_path} (gateway, hypervisor, NAS, admin workstation, admin phone) "
                f"or set {ENV_LIST}."
            )


_REGISTRY: AssetRegistry | None = None


def registry() -> AssetRegistry:
    """Process-wide registry, loaded lazily with defaults."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = AssetRegistry.load()
    return _REGISTRY


def set_registry(reg: AssetRegistry) -> None:
    global _REGISTRY
    _REGISTRY = reg


def is_protected(addr: str | IPv4Network | IPv6Network) -> bool:
    return registry().is_protected(addr)
