"""Log sources: follow a live file (tail -F semantics) or replay a fixture.

Both yield raw text lines with the newline stripped. Nothing here parses.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Iterator


def replay_fixture(path: str | Path, rate_lps: float = 10.0) -> Iterator[str]:
    """Yield a static file line by line. rate_lps <= 0 means as fast as possible."""
    delay = 1.0 / rate_lps if rate_lps and rate_lps > 0 else 0.0
    with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
        first = True
        for line in fh:
            if delay and not first:
                time.sleep(delay)
            first = False
            yield line.rstrip("\r\n")


def tail_file(
    path: str | Path,
    poll_interval: float = 0.5,
    stop: threading.Event | None = None,
    from_start: bool = False,
) -> Iterator[str]:
    """Follow `path` like `tail -F`: waits for the file, survives truncation and
    rotation (inode/size shrink -> reopen from the top). Runs until `stop` is set.
    """
    path = Path(path)
    stop = stop or threading.Event()
    fh = None
    pos = 0
    partial = ""

    def _open():
        nonlocal fh, pos, partial
        fh = path.open("r", encoding="utf-8", errors="replace")
        partial = ""
        if from_start:
            pos = 0
        else:
            fh.seek(0, os.SEEK_END)
            pos = fh.tell()

    try:
        while not stop.is_set():
            if fh is None:
                if not path.exists():
                    time.sleep(poll_interval)
                    continue
                _open()
                # from_start applies only to the first open; a rotated file is
                # always read from its beginning.
                from_start = True

            chunk = fh.read()
            if chunk:
                pos = fh.tell()
                buf = partial + chunk
                lines = buf.split("\n")
                partial = lines.pop()  # incomplete tail, if any
                for line in lines:
                    yield line.rstrip("\r")
                    if stop.is_set():
                        return
                continue

            # No new data: check for truncation / rotation.
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                fh.close()
                fh = None
                time.sleep(poll_interval)
                continue
            if size < pos:
                fh.close()
                fh = None
                continue
            time.sleep(poll_interval)
    finally:
        if fh is not None:
            fh.close()
