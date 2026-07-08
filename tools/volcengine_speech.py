from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import gzip
import json
import os
from pathlib import Path
import time
import uuid
import wave
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / ".env"
DEFAULT_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
DEFAULT_TTS_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_TTS_SPEAKER = "zh_female_tianmeitaozi_uranus_bigtts"
DEFAULT_ASR_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
DEFAULT_ASR_RESOURCE_ID = "volc.seedasr.sauc.duration"
SPEECH_ENV_KEYS = {
    "VOLCENGINE_API_KEY",
    "DOUBAO_API_KEY",
    "VOLCENGINE_APP_KEY",
    "VOLCENGINE_APP_ID",
    "VOLCENGINE_ACCESS_KEY",
    "VOLCENGINE_ACCESS_TOKEN",
    "VOLCENGINE_SECRET_KEY",
    "DOUBAO_TTS_SPEAKER",
}


def load_dotenv(path: Path = DEFAULT_ENV_PATH) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def upsert_dotenv_values(values: dict[str, str], path: Path = DEFAULT_ENV_PATH) -> None:
    clean_values = {
        key: str(value).strip()
        for key, value in values.items()
        if key in SPEECH_ENV_KEYS and str(value).strip()
    }
    if not clean_values:
        return

    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated: set[str] = set()
    next_lines: list[str] = []

    for raw_line in existing_lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or "=" not in raw_line:
            next_lines.append(raw_line)
            continue
        key, _value = raw_line.split("=", 1)
        key = key.strip()
        if key in clean_values:
            next_lines.append(f"{key}={clean_values[key]}")
            updated.add(key)
        else:
            next_lines.append(raw_line)

    for key, value in clean_values.items():
        if key not in updated:
            next_lines.append(f"{key}={value}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")
    for key, value in clean_values.items():
        os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass
class SpeechConfig:
    api_key: str
    app_key: str
    access_key: str
    tts_enabled: bool
    tts_endpoint: str
    tts_resource_id: str
    tts_speaker: str
    tts_format: str
    tts_sample_rate: int
    asr_enabled: bool
    asr_endpoint: str
    asr_resource_id: str
    asr_model: str
    asr_chunk_ms: int

    @classmethod
    def from_env(cls) -> "SpeechConfig":
        load_dotenv()
        access_token = os.getenv("VOLCENGINE_ACCESS_TOKEN") or os.getenv("DOUBAO_ACCESS_TOKEN") or ""
        api_key = os.getenv("VOLCENGINE_API_KEY") or os.getenv("DOUBAO_API_KEY") or ""
        app_key = os.getenv("VOLCENGINE_APP_KEY") or os.getenv("VOLCENGINE_APP_ID") or os.getenv("DOUBAO_APP_KEY") or os.getenv("DOUBAO_APP_ID") or ""
        access_key = os.getenv("VOLCENGINE_ACCESS_KEY") or os.getenv("DOUBAO_ACCESS_KEY") or access_token
        return cls(
            api_key=api_key,
            app_key=app_key,
            access_key=access_key,
            tts_enabled=env_bool("DOUBAO_TTS_ENABLED", True),
            tts_endpoint=os.getenv("DOUBAO_TTS_ENDPOINT", DEFAULT_TTS_ENDPOINT),
            tts_resource_id=os.getenv("DOUBAO_TTS_RESOURCE_ID", DEFAULT_TTS_RESOURCE_ID),
            tts_speaker=os.getenv("DOUBAO_TTS_SPEAKER", DEFAULT_TTS_SPEAKER),
            tts_format=os.getenv("DOUBAO_TTS_FORMAT", "wav"),
            tts_sample_rate=env_int("DOUBAO_TTS_SAMPLE_RATE", 24000),
            asr_enabled=env_bool("DOUBAO_ASR_ENABLED", True),
            asr_endpoint=os.getenv("DOUBAO_ASR_ENDPOINT", DEFAULT_ASR_ENDPOINT),
            asr_resource_id=os.getenv("DOUBAO_ASR_RESOURCE_ID", DEFAULT_ASR_RESOURCE_ID),
            asr_model=os.getenv("DOUBAO_ASR_MODEL", "bigmodel"),
            asr_chunk_ms=max(20, env_int("DOUBAO_ASR_CHUNK_MS", 100)),
        )

    def has_credentials(self) -> bool:
        return bool(self.api_key or (self.app_key and self.access_key))

    def credential_mode(self) -> str:
        if self.api_key:
            return "api_key"
        if self.app_key and self.access_key:
            return "app_access_key"
        return "missing"


class VolcengineSpeechError(RuntimeError):
    pass


def auth_headers(config: SpeechConfig) -> dict[str, str]:
    if config.api_key:
        return {"X-Api-Key": config.api_key}
    return {
        "X-Api-App-Key": config.app_key,
        "X-Api-App-Id": config.app_key,
        "X-Api-Access-Key": config.access_key,
    }


class VolcengineSpeech:
    def __init__(self, config: SpeechConfig | None = None) -> None:
        self.config = config or SpeechConfig.from_env()

    def is_tts_ready(self) -> bool:
        return self.config.tts_enabled and self.config.has_credentials()

    def is_asr_ready(self) -> bool:
        return self.config.asr_enabled and self.config.has_credentials()

    def synthesize_to_file(
        self,
        text: str,
        output_path: Path,
        *,
        speed_ratio: float = 1.0,
        volume_ratio: float = 1.0,
        pitch_ratio: float = 1.0,
    ) -> Path:
        if not self.is_tts_ready():
            raise VolcengineSpeechError("Doubao TTS is not configured. Fill VOLCENGINE_APP_KEY and VOLCENGINE_ACCESS_KEY in .env.")

        try:
            import requests
        except ImportError as exc:
            raise VolcengineSpeechError("requests is not installed. Run pip install -r requirements.txt.") from exc

        output_path.parent.mkdir(parents=True, exist_ok=True)
        request_id = str(uuid.uuid4())
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": self.config.tts_resource_id,
            "X-Api-Request-Id": request_id,
        }
        headers.update(auth_headers(self.config))
        speech_rate = ratio_to_rate(speed_ratio)
        loudness_rate = ratio_to_rate(volume_ratio)
        pitch_rate = ratio_to_rate(pitch_ratio)
        payload = {
            "user": {"uid": "ai-desk-phone"},
            "req_params": {
                "text": text,
                "speaker": self.config.tts_speaker,
                "audio_params": {
                    "format": self.config.tts_format,
                    "sample_rate": self.config.tts_sample_rate,
                    "speech_rate": speech_rate,
                    "loudness_rate": loudness_rate,
                    "pitch_rate": pitch_rate,
                },
                "additions": json.dumps(
                    {
                        "disable_markdown_filter": True,
                        "disable_emoji_filter": True,
                    },
                    ensure_ascii=False,
                ),
            },
        }

        response = requests.post(
            self.config.tts_endpoint,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=60,
            stream=True,
        )
        if response.status_code >= 400:
            raise VolcengineSpeechError(f"Doubao TTS HTTP {response.status_code}: {response.text[:500]}")

        audio = self._collect_tts_audio(response)
        if not audio:
            raise VolcengineSpeechError("Doubao TTS returned no audio payload.")

        audio = normalize_wav_audio(audio)
        output_path.write_bytes(audio)
        return output_path

    def _collect_tts_audio(self, response: Any) -> bytes:
        chunks: list[bytes] = []
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "text/event-stream" not in content_type and "json" not in content_type:
            body = response.content
            return body if body else b""

        for raw_line in response.iter_lines(decode_unicode=True):
            if not raw_line:
                continue
            line = raw_line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line in {"[DONE]", "DONE"}:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            chunk = find_base64_audio(payload)
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)

    def transcribe_wav(self, wav_path: Path) -> dict[str, Any]:
        pcm, sample_rate = read_wav_mono_pcm(wav_path)
        return self.transcribe_pcm(pcm, sample_rate)

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> dict[str, Any]:
        if not self.is_asr_ready():
            return {"success": False, "error": "Doubao ASR is not configured. Fill VOLCENGINE_APP_KEY and VOLCENGINE_ACCESS_KEY in .env."}
        return asyncio.run(self._transcribe_pcm_async(pcm_bytes, sample_rate))

    async def _transcribe_pcm_async(self, pcm_bytes: bytes, sample_rate: int) -> dict[str, Any]:
        try:
            import websockets
        except ImportError:
            return {"success": False, "error": "websockets is not installed. Run pip install -r requirements.txt."}

        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-Resource-Id": self.config.asr_resource_id,
            "X-Api-Connect-Id": request_id,
            "X-Api-Request-Id": request_id,
        }
        headers.update(auth_headers(self.config))
        init_payload = {
            "audio": {"format": "pcm", "codec": "raw", "sample_rate": sample_rate, "channel": 1},
            "request": {"model_name": self.config.asr_model, "enable_punc": True, "enable_itn": True},
        }
        chunk_bytes = max(1, sample_rate * self.config.asr_chunk_ms // 1000) * 2
        started = time.time()
        latest_text = ""

        try:
            connect_ctx = websockets.connect(self.config.asr_endpoint, additional_headers=headers, open_timeout=15, close_timeout=10)
        except TypeError:
            connect_ctx = websockets.connect(self.config.asr_endpoint, extra_headers=headers, open_timeout=15, close_timeout=10)

        async with connect_ctx as ws:
            await ws.send(build_asr_init_packet(init_payload, sequence=1))
            init_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            init_result = parse_asr_packet(init_raw)
            if init_result.get("error"):
                return {"success": False, "error": init_result["error"]}

            offset = 0
            while offset < len(pcm_bytes):
                end = min(offset + chunk_bytes, len(pcm_bytes))
                await ws.send(build_asr_audio_packet(pcm_bytes[offset:end], is_last=end >= len(pcm_bytes)))
                offset = end

            async for message in ws:
                result = parse_asr_packet(message)
                if result.get("error"):
                    return {"success": False, "error": result["error"]}
                text = extract_asr_text(result.get("payload", {}))
                if text:
                    latest_text = text
                if result.get("is_last"):
                    break

        return {
            "success": True,
            "text": latest_text,
            "raw_text": latest_text,
            "inference_latency": time.time() - started,
        }


def ratio_to_rate(ratio: float) -> int:
    try:
        value = float(ratio)
    except (TypeError, ValueError):
        value = 1.0
    return max(-50, min(100, int(round((value - 1.0) * 100))))


def normalize_wav_audio(audio: bytes) -> bytes:
    if not (audio.startswith(b"RIFF") and audio[8:12] == b"WAVE"):
        return audio

    patched = bytearray(audio)
    riff_size = len(patched) - 8
    if len(patched) >= 8 and patched[4:8] == b"\xff\xff\xff\xff":
        patched[4:8] = riff_size.to_bytes(4, "little", signed=False)

    data_index = patched.find(b"data")
    if data_index >= 0 and len(patched) >= data_index + 8:
        data_size = len(patched) - data_index - 8
        if patched[data_index + 4:data_index + 8] == b"\xff\xff\xff\xff":
            patched[data_index + 4:data_index + 8] = data_size.to_bytes(4, "little", signed=False)

    return bytes(patched)


def find_base64_audio(payload: Any) -> bytes | None:
    if isinstance(payload, dict):
        for key in ("audio", "audio_data", "data", "payload", "binary_data"):
            value = payload.get(key)
            decoded = decode_base64_audio(value)
            if decoded:
                return decoded
        for value in payload.values():
            decoded = find_base64_audio(value)
            if decoded:
                return decoded
    if isinstance(payload, list):
        for item in payload:
            decoded = find_base64_audio(item)
            if decoded:
                return decoded
    return None


def decode_base64_audio(value: Any) -> bytes | None:
    if not isinstance(value, str) or len(value) < 16:
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except Exception:
        return None
    return decoded if decoded else None


ASR_JSON = 0x10
ASR_GZIP = 0x01


def build_asr_header(message_type: int, flags: int) -> bytearray:
    return bytearray([(1 << 4) | 1, (message_type << 4) | flags, ASR_JSON | ASR_GZIP, 0])


def build_asr_init_packet(payload: dict[str, Any], sequence: int = 1) -> bytes:
    body = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    packet = bytearray(build_asr_header(0x01, 0x01))
    packet.extend(sequence.to_bytes(4, "big", signed=True))
    packet.extend(len(body).to_bytes(4, "big"))
    packet.extend(body)
    return bytes(packet)


def build_asr_audio_packet(audio: bytes, *, is_last: bool) -> bytes:
    body = gzip.compress(audio)
    packet = bytearray(build_asr_header(0x02, 0x02 if is_last else 0x00))
    packet.extend(len(body).to_bytes(4, "big"))
    packet.extend(body)
    return bytes(packet)


def parse_asr_packet(data: bytes) -> dict[str, Any]:
    header_size = (data[0] & 0x0F) * 4
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    compression = data[2] & 0x0F
    payload = data[header_size:]
    result: dict[str, Any] = {"message_type": message_type, "is_last": bool(flags & 0x02)}

    if flags & 0x01:
        result["sequence"] = int.from_bytes(payload[:4], "big", signed=True)
        payload = payload[4:]

    if message_type == 0x09:
        size = int.from_bytes(payload[:4], "big", signed=True)
        body = payload[4:4 + size]
        if compression == 0x01:
            body = gzip.decompress(body)
        result["payload"] = json.loads(body.decode("utf-8"))
        return result

    if message_type == 0x0F:
        code = int.from_bytes(payload[:4], "big", signed=False)
        size = int.from_bytes(payload[4:8], "big", signed=False)
        body = payload[8:8 + size]
        if compression == 0x01:
            body = gzip.decompress(body)
        result["error"] = f"{code}: {body.decode('utf-8', errors='replace')}"
        return result

    return result


def extract_asr_text(payload: dict[str, Any]) -> str:
    result = payload.get("result") if isinstance(payload, dict) else None
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return text
    text = payload.get("text") if isinstance(payload, dict) else None
    return text if isinstance(text, str) else ""


def read_wav_mono_pcm(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise VolcengineSpeechError("ASR currently expects 16-bit PCM WAV.")
    if channels == 1:
        return frames, sample_rate
    if channels != 2:
        raise VolcengineSpeechError("ASR currently supports mono or stereo WAV only.")
    mono = bytearray()
    for index in range(0, len(frames), 4):
        left = int.from_bytes(frames[index:index + 2], "little", signed=True)
        right = int.from_bytes(frames[index + 2:index + 4], "little", signed=True)
        mixed = int((left + right) / 2)
        mono.extend(mixed.to_bytes(2, "little", signed=True))
    return bytes(mono), sample_rate
