"""Speech rendering for Sentinel.

`tts_summary` is the one string in the system that gets spoken aloud and shown
on a lock screen. schemas.validate_tts already guarantees it carries no
address, username, port or token. This module turns it into audio.

Two backends, tried in order:

  ElevenLabs   the intended voice. Needs ELEVENLABS_API_KEY.
  espeak-ng    local, offline, no key, no per-call cost. Robotic, but it
               speaks, and a demo where the phone talks in a flat voice beats
               a demo where it does not talk at all.

Design rules, all of them from contracts.md:

1. **Never block an event on TTS.** Rendering happens off the event path. The
   event ships immediately with `audio.status = "pending"`, and an
   `audio_ready` frame carrying the same `event_id` follows when the audio
   exists. If rendering fails the event has already been delivered.

2. **Cache on `cache_key`.** That key is a hash of the phrase, so the same
   sentence is rendered once no matter how many times it recurs. On
   ElevenLabs this is the difference between a demo that costs cents and one
   that costs real money.

3. **Failure is a status, not an exception.** Every path returns something the
   caller can put on the wire.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("sentinel.tts")

STATIC_DIR = Path(__file__).with_name("static")

# lame runs CBR at this rate, so duration is derivable from file size when a
# wav is not available to measure.
MP3_BITRATE_KBPS = 64

# espeak-ng defaults. A slightly slower rate than the default 175 is easier to
# follow across a room, which is the actual listening condition.
ESPEAK_VOICE = os.environ.get("SENTINEL_ESPEAK_VOICE", "en-us+f3")
ESPEAK_WPM = os.environ.get("SENTINEL_ESPEAK_WPM", "165")

ELEVEN_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
ELEVEN_MODEL = os.environ.get("ELEVENLABS_MODEL", "eleven_turbo_v2_5")


@dataclass(frozen=True)
class Rendered:
    """A finished audio file, with everything AudioRef needs for status=ready."""
    path: Path
    duration_ms: int
    sha256: str
    engine: str


class Renderer:
    """Renders speech, cached, with a local fallback.

    Construct once and share it. `available` tells the heartbeat whether to
    report tts as ok or degraded — reporting ok with no renderer would be the
    kind of lie the invariants exist to prevent.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or STATIC_DIR / "tts"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._eleven_key = os.environ.get("ELEVENLABS_API_KEY", "").strip() or None
        self._espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        self._lame = shutil.which("lame")

        if self._eleven_key:
            self.engine = "elevenlabs"
        elif self._espeak and self._lame:
            self.engine = "espeak-ng"
        else:
            self.engine = "none"

        log.info(
            "tts renderer: engine=%s cache=%s (elevenlabs_key=%s espeak=%s lame=%s)",
            self.engine, self.cache_dir, bool(self._eleven_key),
            bool(self._espeak), bool(self._lame),
        )

    @property
    def available(self) -> bool:
        return self.engine != "none"

    def status_word(self) -> str:
        """What the heartbeat should say about the tts subsystem."""
        if self.engine == "elevenlabs":
            return "ok"
        if self.engine == "espeak-ng":
            # Speaking, but not in the intended voice. "degraded" is the honest
            # word: the subsystem works and the output is worse than intended.
            return "degraded"
        return "unreachable"

    # ------------------------------------------------------------------ api

    def render(self, text: str, cache_key: str) -> Rendered | None:
        """Render `text`, or return None if it could not be rendered.

        Safe to call from a worker thread. Never raises.
        """
        if not self.available or not text.strip():
            return None

        target = self.cache_dir / f"{_safe(cache_key)}.mp3"
        if target.exists() and target.stat().st_size > 0:
            try:
                return _describe(target, self.engine)
            except Exception:
                # Corrupt cache entry: drop it and re-render rather than
                # serving a file the phone cannot decode.
                target.unlink(missing_ok=True)

        try:
            if self.engine == "elevenlabs":
                dur = self._render_elevenlabs(text, target)
            else:
                dur = self._render_espeak(text, target)
        except Exception as e:
            log.warning("tts render failed (%s): %s: %s", self.engine, type(e).__name__, e)
            target.unlink(missing_ok=True)
            return None

        if not target.exists() or target.stat().st_size == 0:
            log.warning("tts produced no audio for %r", text[:60])
            target.unlink(missing_ok=True)
            return None

        digest = _sha256(target)
        return Rendered(path=target, duration_ms=dur, sha256=digest, engine=self.engine)

    # -------------------------------------------------------------- backends

    def _render_espeak(self, text: str, target: Path) -> int:
        """espeak-ng to wav, lame to mp3. Duration measured from the wav."""
        wav = target.with_suffix(".wav")
        try:
            subprocess.run(
                [self._espeak, "-v", ESPEAK_VOICE, "-s", ESPEAK_WPM, "-w", str(wav), text],
                check=True, capture_output=True, timeout=30,
            )
            duration_ms = _wav_duration_ms(wav)
            subprocess.run(
                [self._lame, "--quiet", "-b", str(MP3_BITRATE_KBPS), "--resample", "22.05",
                 str(wav), str(target)],
                check=True, capture_output=True, timeout=30,
            )
            return duration_ms
        finally:
            wav.unlink(missing_ok=True)

    def _render_elevenlabs(self, text: str, target: Path) -> int:
        """Streaming mp3 straight to disk. No SDK dependency, just HTTP."""
        import urllib.request

        req = urllib.request.Request(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVEN_VOICE_ID}"
            f"?output_format=mp3_22050_32",
            data=_json_bytes({
                "text": text,
                "model_id": ELEVEN_MODEL,
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            }),
            headers={
                "xi-api-key": self._eleven_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            method="POST",
        )
        # 20s is generous for one sentence and still well short of anything a
        # caller would consider hung.
        with urllib.request.urlopen(req, timeout=20) as resp, target.open("wb") as fh:
            shutil.copyfileobj(resp, fh)

        return _mp3_duration_ms(target, bitrate_kbps=32)


# ---------------------------------------------------------------- helpers


def _safe(cache_key: str) -> str:
    """cache_key is contract-constrained, but this is a filename. Be sure."""
    return "".join(c for c in cache_key if c.isalnum() or c in "._-")[:128] or "unnamed"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _wav_duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as w:
        return int(round(w.getnframes() / float(w.getframerate()) * 1000))


def _mp3_duration_ms(path: Path, bitrate_kbps: int) -> int:
    """CBR estimate. Exact enough for a progress bar, and we control the rate.

    Only used on the ElevenLabs path, where we request a known CBR format.
    """
    bits = path.stat().st_size * 8
    return max(0, int(round(bits / (bitrate_kbps * 1000) * 1000)))


def _json_bytes(obj: dict) -> bytes:
    import json
    return json.dumps(obj).encode()
