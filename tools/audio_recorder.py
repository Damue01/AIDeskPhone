from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
import wave
from typing import Any


class AudioRecorderError(RuntimeError):
    pass


@dataclass
class RecordingResult:
    path: Path
    sample_rate: int
    channels: int
    bytes_written: int
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "bytes_written": self.bytes_written,
            "duration_seconds": self.duration_seconds,
        }


class AudioRecorder:
    def __init__(self) -> None:
        self._stream: Any | None = None
        self._frames: list[bytes] = []
        self._lock = threading.Lock()
        self._started_at: float | None = None
        self._last_voice_at: float | None = None
        self._last_level = 0.0
        self._peak_level = 0.0
        self.sample_rate = 16000
        self.channels = 1
        self.device: str | int | None = None

    def is_recording(self) -> bool:
        return self._stream is not None

    @staticmethod
    def dependencies_available() -> bool:
        try:
            import sounddevice  # noqa: F401
        except ImportError:
            return False
        return True

    def start(self, *, sample_rate: int = 16000, channels: int = 1, device: str | int | None = None) -> None:
        if self._stream is not None:
            raise AudioRecorderError("recording already active")

        try:
            import sounddevice as sd
        except ImportError as exc:
            raise AudioRecorderError("sounddevice is not installed. Run pip install -r requirements.txt.") from exc

        self.sample_rate = sample_rate
        self.channels = channels
        self.device = device or None
        self._frames = []
        self._started_at = time.monotonic()
        self._last_voice_at = self._started_at
        self._last_level = 0.0
        self._peak_level = 0.0

        def callback(indata: bytes, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info
            if status:
                # Keep recording; status is usually an overflow warning.
                pass
            level = pcm_average_abs(indata)
            now = time.monotonic()
            with self._lock:
                self._frames.append(bytes(indata))
                self._last_level = level
                self._peak_level = max(self._peak_level, level)
                if level >= 650:
                    self._last_voice_at = now

        self._stream = sd.RawInputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="int16",
            device=self.device,
            callback=callback,
        )
        self._stream.start()

    def stop_to_wav(self, output_path: Path) -> RecordingResult:
        if self._stream is None:
            raise AudioRecorderError("recording is not active")

        stream = self._stream
        self._stream = None
        try:
            stream.stop()
            stream.close()
        finally:
            pass

        with self._lock:
            audio = b"".join(self._frames)
            self._frames = []

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(output_path), "wb") as wav:
            wav.setnchannels(self.channels)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(audio)

        started_at = self._started_at or time.monotonic()
        duration = max(0.0, time.monotonic() - started_at)
        self._started_at = None
        self._last_voice_at = None
        return RecordingResult(
            path=output_path,
            sample_rate=self.sample_rate,
            channels=self.channels,
            bytes_written=len(audio),
            duration_seconds=duration,
        )

    def cancel(self) -> None:
        if self._stream is None:
            return

        stream = self._stream
        self._stream = None
        try:
            stream.stop()
            stream.close()
        finally:
            with self._lock:
                self._frames = []
                self._last_level = 0.0
                self._peak_level = 0.0
                self._last_voice_at = None
                self._started_at = None

    def current_duration_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return max(0.0, time.monotonic() - self._started_at)

    def voice_activity(self) -> dict[str, float]:
        now = time.monotonic()
        with self._lock:
            started_at = self._started_at
            last_voice_at = self._last_voice_at or started_at or now
            return {
                "duration_seconds": max(0.0, now - started_at) if started_at else 0.0,
                "silence_seconds": max(0.0, now - last_voice_at),
                "last_level": self._last_level,
                "peak_level": self._peak_level,
            }


def pcm_average_abs(data: bytes) -> float:
    if len(data) < 2:
        return 0.0
    total = 0
    count = 0
    for index in range(0, len(data) - 1, 2):
        total += abs(int.from_bytes(data[index:index + 2], "little", signed=True))
        count += 1
    return total / count if count else 0.0
