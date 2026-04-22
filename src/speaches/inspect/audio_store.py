from __future__ import annotations

import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Literal

import numpy as np

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

Channel = Literal["mic_in", "tts_out"]
_SAMPLE_RATES: dict[str, int] = {"mic_in": 16000, "tts_out": 24000}


class _Track:
    __slots__ = ("fh", "first_ns", "lock", "path", "sample_rate", "session_start_wall", "total_samples")

    def __init__(self, session_id: str, channel: Channel, session_dir: Path, session_start_wall: float) -> None:
        self.sample_rate = _SAMPLE_RATES[channel]
        self.path = session_dir / f"{session_id}.audio_{channel}.raw"
        self.session_start_wall = session_start_wall
        self.first_ns: int | None = None
        self.total_samples = 0
        self.lock = threading.Lock()
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            self.fh = self.path.open("ab")
        except OSError:
            logger.exception("Failed to open audio track at %s", self.path)
            self.fh = None

    def append_pcm16(self, pcm: bytes) -> None:
        if not pcm or self.fh is None:
            return
        with self.lock:
            if self.first_ns is None:
                self.first_ns = time.perf_counter_ns()
            try:
                self.fh.write(pcm)
                self.total_samples += len(pcm) // 2
            except OSError:
                logger.exception("Failed to write audio track")

    def append_float32(self, samples: np.ndarray) -> None:
        """Samples in [-1, 1] float32 mono at self.sample_rate."""
        if samples.size == 0:
            return
        clipped = np.clip(samples, -1.0, 1.0)
        pcm16 = (clipped * 32767.0).astype("<i2").tobytes()
        self.append_pcm16(pcm16)

    def slice(self, from_ms: int, to_ms: int) -> bytes:
        """Return PCM16 bytes for the given ms-range (from_ms inclusive, to_ms exclusive).
        to_ms <= 0 means to end of stream.
        """
        sr = self.sample_rate
        byte_offset = max(0, from_ms) * sr * 2 // 1000
        if to_ms <= 0:
            end_offset: int | None = None
        else:
            end_offset = to_ms * sr * 2 // 1000
        with self.lock:
            if self.fh is not None:
                self.fh.flush()
            try:
                with self.path.open("rb") as rh:
                    rh.seek(byte_offset)
                    if end_offset is None:
                        return rh.read()
                    return rh.read(end_offset - byte_offset)
            except OSError:
                logger.exception("Failed to read audio track %s", self.path)
                return b""

    def close(self) -> None:
        with self.lock:
            if self.fh is not None:
                try:
                    self.fh.flush()
                    self.fh.close()
                except OSError:
                    logger.exception("Failed to close audio track")
                self.fh = None


class AudioStore:
    def __init__(self, session_id: str, session_dir: Path) -> None:
        self.session_id = session_id
        self.session_dir = session_dir
        self.session_start_wall = time.time()
        self.session_start_ns = time.perf_counter_ns()
        self._tracks: dict[Channel, _Track] = {
            "mic_in": _Track(session_id, "mic_in", session_dir, self.session_start_wall),
            "tts_out": _Track(session_id, "tts_out", session_dir, self.session_start_wall),
        }

    def append_mic_in(self, float_samples: np.ndarray) -> None:
        self._tracks["mic_in"].append_float32(float_samples)

    def append_tts_out(self, pcm16_bytes: bytes) -> None:
        self._tracks["tts_out"].append_pcm16(pcm16_bytes)

    def track_offset_ms(self, channel: Channel) -> int:
        t = self._tracks.get(channel)
        if t is None or t.first_ns is None:
            return 0
        return max(0, (t.first_ns - self.session_start_ns) // 1_000_000)

    def slice(self, channel: Channel, from_ms: int, to_ms: int) -> bytes:
        t = self._tracks.get(channel)
        if t is None:
            return b""
        offset = self.track_offset_ms(channel)
        adjusted_from = max(0, from_ms - offset)
        adjusted_to = max(0, to_ms - offset) if to_ms > 0 else 0
        return t.slice(adjusted_from, adjusted_to)

    def close(self) -> None:
        sidecar = self.session_dir / f"{self.session_id}.audio.json"
        try:
            sidecar.write_text(
                json.dumps(
                    {
                        "session_id": self.session_id,
                        "started_at": self.session_start_wall,
                        "tracks": {
                            "mic_in": {
                                "sample_rate": 16000,
                                "samples": self._tracks["mic_in"].total_samples,
                                "offset_ms": self.track_offset_ms("mic_in"),
                            },
                            "tts_out": {
                                "sample_rate": 24000,
                                "samples": self._tracks["tts_out"].total_samples,
                                "offset_ms": self.track_offset_ms("tts_out"),
                            },
                        },
                    }
                )
            )
        except OSError:
            logger.exception("Failed to write audio sidecar")
        for t in self._tracks.values():
            t.close()
