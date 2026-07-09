from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
import gzip
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import threading
import time
import uuid
import wave
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_PATH = ROOT / ".env"
DEFAULT_TTS_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
DEFAULT_TTS_RESOURCE_ID = "seed-tts-2.0"
DEFAULT_TTS_SPEAKER = "zh_female_tianmeitaozi_uranus_bigtts"
DEFAULT_ASR_ENDPOINT = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"
DEFAULT_ASR_RESOURCE_ID = "volc.seedasr.sauc.duration"
DEFAULT_OPERATOR_ENDPOINT = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
DEFAULT_OPERATOR_MODEL = "doubao-seed-character-260628"
SPEECH_ENV_KEYS = {
    "ARK_API_KEY",
    "ARK_CHAT_COMPLETIONS_ENDPOINT",
    "VOLCENGINE_API_KEY",
    "DOUBAO_API_KEY",
    "VOLCENGINE_APP_KEY",
    "VOLCENGINE_APP_ID",
    "VOLCENGINE_ACCESS_KEY",
    "VOLCENGINE_ACCESS_TOKEN",
    "VOLCENGINE_SECRET_KEY",
    "DOUBAO_TTS_SPEAKER",
    "DOUBAO_TTS_RESOURCE_ID",
    "DOUBAO_TTS_MODEL",
    "DOUBAO_TTS_FORMAT",
    "DOUBAO_TTS_SAMPLE_RATE",
    "DOUBAO_TTS_STREAMING_PLAYBACK_ENABLED",
    "DOUBAO_TTS_EXPLICIT_LANGUAGE",
    "DOUBAO_TTS_EXPLICIT_DIALECT",
    "DOUBAO_TTS_DISABLE_MARKDOWN_FILTER",
    "DOUBAO_TTS_DISABLE_EMOJI_FILTER",
    "DOUBAO_ASR_ENDPOINT",
    "DOUBAO_ASR_RESOURCE_ID",
    "DOUBAO_ASR_MODEL",
    "DOUBAO_ASR_CHUNK_MS",
    "DOUBAO_ASR_STREAMING_ENABLED",
    "DOUBAO_ASR_BOOSTING_TABLE_ID",
    "DOUBAO_ASR_BOOSTING_TABLE_NAME",
    "DOUBAO_ASR_HOTWORDS",
    "DOUBAO_OPERATOR_POLISH_ENABLED",
    "DOUBAO_OPERATOR_MODEL",
    "DOUBAO_OPERATOR_SYSTEM_PROMPT",
    "DOUBAO_OPERATOR_MAX_TOKENS",
}
SPEECH_CLEARABLE_ENV_KEYS = {
    "ARK_CHAT_COMPLETIONS_ENDPOINT",
    "DOUBAO_TTS_EXPLICIT_LANGUAGE",
    "DOUBAO_TTS_EXPLICIT_DIALECT",
    "DOUBAO_ASR_BOOSTING_TABLE_ID",
    "DOUBAO_ASR_BOOSTING_TABLE_NAME",
    "DOUBAO_ASR_HOTWORDS",
    "DOUBAO_OPERATOR_MODEL",
    "DOUBAO_OPERATOR_SYSTEM_PROMPT",
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
    clean_values: dict[str, str] = {}
    for key, value in values.items():
        if key not in SPEECH_ENV_KEYS:
            continue
        text = str(value).strip()
        if text or key in SPEECH_CLEARABLE_ENV_KEYS:
            clean_values[key] = text
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
    tts_model: str
    tts_speaker: str
    tts_format: str
    tts_sample_rate: int
    tts_streaming_playback_enabled: bool
    tts_explicit_language: str
    tts_explicit_dialect: str
    tts_disable_markdown_filter: bool
    tts_disable_emoji_filter: bool
    asr_enabled: bool
    asr_endpoint: str
    asr_resource_id: str
    asr_model: str
    asr_chunk_ms: int
    asr_streaming_enabled: bool
    asr_boosting_table_id: str
    asr_boosting_table_name: str
    asr_hotwords: str
    operator_api_key: str
    operator_endpoint: str
    operator_model: str
    operator_polish_enabled: bool
    operator_system_prompt: str
    operator_max_tokens: int

    @classmethod
    def from_env(cls) -> "SpeechConfig":
        load_dotenv()
        ark_api_key = os.getenv("ARK_API_KEY") or ""
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
            tts_model=os.getenv("DOUBAO_TTS_MODEL", "seed-tts-2.0-standard"),
            tts_speaker=os.getenv("DOUBAO_TTS_SPEAKER", DEFAULT_TTS_SPEAKER),
            tts_format=os.getenv("DOUBAO_TTS_FORMAT", "wav"),
            tts_sample_rate=env_int("DOUBAO_TTS_SAMPLE_RATE", 24000),
            tts_streaming_playback_enabled=env_bool("DOUBAO_TTS_STREAMING_PLAYBACK_ENABLED", True),
            tts_explicit_language=os.getenv("DOUBAO_TTS_EXPLICIT_LANGUAGE", ""),
            tts_explicit_dialect=os.getenv("DOUBAO_TTS_EXPLICIT_DIALECT", ""),
            tts_disable_markdown_filter=env_bool("DOUBAO_TTS_DISABLE_MARKDOWN_FILTER", True),
            tts_disable_emoji_filter=env_bool("DOUBAO_TTS_DISABLE_EMOJI_FILTER", True),
            asr_enabled=env_bool("DOUBAO_ASR_ENABLED", True),
            asr_endpoint=os.getenv("DOUBAO_ASR_ENDPOINT", DEFAULT_ASR_ENDPOINT),
            asr_resource_id=os.getenv("DOUBAO_ASR_RESOURCE_ID", DEFAULT_ASR_RESOURCE_ID),
            asr_model=os.getenv("DOUBAO_ASR_MODEL", "bigmodel"),
            asr_chunk_ms=max(20, min(500, env_int("DOUBAO_ASR_CHUNK_MS", 200))),
            asr_streaming_enabled=env_bool("DOUBAO_ASR_STREAMING_ENABLED", True),
            asr_boosting_table_id=os.getenv("DOUBAO_ASR_BOOSTING_TABLE_ID", ""),
            asr_boosting_table_name=os.getenv("DOUBAO_ASR_BOOSTING_TABLE_NAME", ""),
            asr_hotwords=os.getenv("DOUBAO_ASR_HOTWORDS", ""),
            operator_api_key=ark_api_key or api_key,
            operator_endpoint=os.getenv("ARK_CHAT_COMPLETIONS_ENDPOINT") or DEFAULT_OPERATOR_ENDPOINT,
            operator_model=os.getenv("DOUBAO_OPERATOR_MODEL") or DEFAULT_OPERATOR_MODEL,
            operator_polish_enabled=env_bool("DOUBAO_OPERATOR_POLISH_ENABLED", True),
            operator_system_prompt=os.getenv("DOUBAO_OPERATOR_SYSTEM_PROMPT") or DEFAULT_OPERATOR_SYSTEM_PROMPT,
            operator_max_tokens=max(128, min(4096, env_int("DOUBAO_OPERATOR_MAX_TOKENS", 900))),
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


DEFAULT_OPERATOR_SYSTEM_PROMPT = (
    "你是一名电话通讯员，用户是电话另一端的联系人。你的任务是把任务完成结果整理成一段可以通过电话播报的回话。"
    "只以通讯员身份说话，不暴露任何后台身份、系统身份或实现细节。要求：保留事实和关键结论，不编造；"
    "称呼自然，不要固定套用“首长”开头；语气稳重、干练、像完成任务后的回报；"
    "收到命令时直接给出执行结果或无法办理的结果，不要解释内部能力；不要复述用户原话；"
    "不要使用 Markdown、项目符号、代码块或表格；除非原文要求，否则不要扩写操作步骤；控制在 80 到 220 个中文字符。"
)

DEFAULT_PHONE_AGENT_SYSTEM_PROMPT = (
    "你正在扮演电话另一端的通讯员“小叶”。用户通过一台实体电话和你通话。"
    "保持角色，不要说明自己是模型、系统、AI、助手或工具，也不要解释后台流程。"
    "说话要像真人接电话：自然、短、干脆，有一点亲近感；不要复述用户原话，不要固定套话。"
    "你知道自己可以使用技能；当上下文给出技能执行结果时，说明事情已经办过，直接按结果回报。"
    "当没有技能结果时，按通话内容自然接话，不要编造已经完成的动作。"
    "参考口吻只用于风格，不要机械照抄："
    "有明确命令时可以像“收到！小叶立刻执行，您稍等。”；"
    "整理文件一类请求可以像“收到，这就去整理。”；"
    "执行完成后可以像“好了，已经定位到北京。”；"
    "闲聊时可以像“小叶在，您说。”"
    "回复一到两句即可。"
)


def build_asr_init_payload(config: SpeechConfig, sample_rate: int) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model_name": config.asr_model,
        "enable_punc": True,
        "enable_itn": True,
    }
    if config.asr_boosting_table_id:
        request["boosting_table_id"] = config.asr_boosting_table_id
    if config.asr_boosting_table_name:
        request["boosting_table_name"] = config.asr_boosting_table_name

    hotwords = normalize_hotword_text(config.asr_hotwords)
    if hotwords:
        request["hotwords"] = hotwords

    return {
        "audio": {"format": "pcm", "codec": "raw", "sample_rate": sample_rate, "channel": 1},
        "request": request,
    }


def normalize_hotword_text(value: str) -> str:
    parts = [
        item.strip()
        for item in str(value or "").replace("\n", ",").replace("，", ",").split(",")
        if item.strip()
    ]
    return ",".join(parts)


def build_operator_report_payload(config: SpeechConfig, text: str, *, source: str = "codex") -> dict[str, Any]:
    user_content = (
        f"来源：{source or '任务来源'}\n"
        "请把下面这段任务完成结果整理成电话回报。必须忠实保留原意，不能添加原文没有的完成项；"
        "不要说明后台系统、程序或工具信息。\n\n"
        f"原始内容：\n{text}"
    )
    return {
        "model": config.operator_model,
        "messages": [
            {"role": "system", "content": config.operator_system_prompt or DEFAULT_OPERATOR_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.4,
        "max_tokens": config.operator_max_tokens,
    }


def build_phone_agent_reply_payload(
    config: SpeechConfig,
    text: str,
    *,
    source: str = "voice",
    skill_context: str = "",
    fallback_text: str = "",
) -> dict[str, Any]:
    user_content = (
        f"来源：{source or 'voice'}\n"
        "可用技能：command_center.earth 可控制指挥中心地球页，包括定位城市、跳转经纬度、切换状态、返回地球屏保。\n"
        f"已执行技能结果：{skill_context or '无'}\n"
        f"兜底参考：{fallback_text or '无'}\n"
        "请直接以“小叶”的电话通讯员身份回应下面这句话。不要复述原话，不要解释规则。\n\n"
        f"用户电话内容：\n{text}"
    )
    return {
        "model": config.operator_model,
        "messages": [
            {"role": "system", "content": DEFAULT_PHONE_AGENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.8,
        "max_tokens": min(config.operator_max_tokens, 220),
    }


def extract_chat_completion_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list):
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    return content
                if isinstance(content, list):
                    parts: list[str] = []
                    for item in content:
                        if isinstance(item, dict):
                            text = item.get("text") or item.get("content")
                            if isinstance(text, str):
                                parts.append(text)
                        elif isinstance(item, str):
                            parts.append(item)
                    joined = "".join(parts).strip()
                    if joined:
                        return joined
            text = choice.get("text")
            if isinstance(text, str) and text.strip():
                return text
    output = payload.get("output")
    if isinstance(output, str):
        return output
    if isinstance(output, dict):
        text = output.get("text") or output.get("content")
        if isinstance(text, str):
            return text
    return ""


def compact_json(value: Any, limit: int = 500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        text = str(value)
    return text[:limit]


def auth_headers(config: SpeechConfig) -> dict[str, str]:
    if config.api_key:
        return {"X-Api-Key": config.api_key}
    return {
        "X-Api-App-Key": config.app_key,
        "X-Api-App-Id": config.app_key,
        "X-Api-Access-Key": config.access_key,
    }


class StreamingAsrSession:
    def __init__(
        self,
        speech: "VolcengineSpeech",
        sample_rate: int,
        *,
        on_result: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.speech = speech
        self.sample_rate = sample_rate
        self.on_result = on_result
        self.chunk_bytes = max(1, sample_rate * speech.config.asr_chunk_ms // 1000) * 2
        self.audio_queue: queue.Queue[bytes | None] = queue.Queue(maxsize=120)
        self.buffer = bytearray()
        self.buffer_lock = threading.Lock()
        self.thread = threading.Thread(target=self._run_thread, daemon=True)
        self.ready_event = threading.Event()
        self.done_event = threading.Event()
        self.closed = False
        self.error: str | None = None
        self.latest_text = ""
        self.raw_text = ""
        self.started_at = time.time()
        self.bytes_submitted = 0
        self.chunks_submitted = 0
        self.chunks_dropped = 0

    def start(self) -> None:
        self.thread.start()

    def submit_audio(self, audio: bytes) -> None:
        if self.closed or not audio:
            return
        packets: list[bytes] = []
        with self.buffer_lock:
            self.buffer.extend(audio)
            while len(self.buffer) >= self.chunk_bytes:
                packets.append(bytes(self.buffer[: self.chunk_bytes]))
                del self.buffer[: self.chunk_bytes]
        for packet in packets:
            self._put_packet(packet)

    def _put_packet(self, packet: bytes | None) -> None:
        if packet is None:
            try:
                self.audio_queue.put(packet, timeout=1.0)
            except queue.Full:
                self.error = self.error or "streaming ASR queue is full"
            return
        try:
            self.audio_queue.put_nowait(packet)
            self.bytes_submitted += len(packet)
            self.chunks_submitted += 1
        except queue.Full:
            self.chunks_dropped += 1

    def finish(self, timeout: float = 25.0) -> dict[str, Any]:
        with self.buffer_lock:
            if self.buffer:
                self._put_packet(bytes(self.buffer))
                self.buffer.clear()
        self.closed = True
        self._put_packet(None)
        self.done_event.wait(timeout=timeout)
        if not self.done_event.is_set():
            self.error = self.error or "streaming ASR timed out"
        return self.result()

    def cancel(self) -> None:
        self.closed = True
        with self.buffer_lock:
            self.buffer.clear()
        self._put_packet(None)
        self.done_event.wait(timeout=1.0)

    def result(self) -> dict[str, Any]:
        success = self.error is None and self.done_event.is_set()
        return {
            "success": success,
            "text": self.latest_text,
            "raw_text": self.raw_text or self.latest_text,
            "partial": not self.done_event.is_set(),
            "streaming": True,
            "error": self.error,
            "inference_latency": time.time() - self.started_at,
            "bytes_submitted": self.bytes_submitted,
            "chunks_submitted": self.chunks_submitted,
            "chunks_dropped": self.chunks_dropped,
        }

    def _emit(self, payload: dict[str, Any]) -> None:
        if self.on_result is None:
            return
        try:
            self.on_result(payload)
        except Exception:
            pass

    def _run_thread(self) -> None:
        try:
            asyncio.run(self._run_async())
        except Exception as exc:
            self.error = str(exc)
            self.ready_event.set()
        finally:
            self.done_event.set()
            self._emit(self.result())

    async def _run_async(self) -> None:
        try:
            import websockets
        except ImportError:
            self.error = "websockets is not installed. Run pip install -r requirements.txt."
            self.ready_event.set()
            return

        request_id = str(uuid.uuid4())
        headers = {
            "X-Api-Resource-Id": self.speech.config.asr_resource_id,
            "X-Api-Connect-Id": request_id,
            "X-Api-Request-Id": request_id,
        }
        headers.update(auth_headers(self.speech.config))
        init_payload = build_asr_init_payload(self.speech.config, self.sample_rate)

        try:
            connect_ctx = websockets.connect(
                self.speech.config.asr_endpoint,
                additional_headers=headers,
                open_timeout=15,
                close_timeout=10,
            )
        except TypeError:
            connect_ctx = websockets.connect(
                self.speech.config.asr_endpoint,
                extra_headers=headers,
                open_timeout=15,
                close_timeout=10,
            )

        async with connect_ctx as ws:
            await ws.send(build_asr_init_packet(init_payload, sequence=1))
            init_raw = await asyncio.wait_for(ws.recv(), timeout=10)
            init_result = parse_asr_packet(init_raw)
            if init_result.get("error"):
                self.error = str(init_result["error"])
                self.ready_event.set()
                return

            self.ready_event.set()
            await asyncio.gather(self._send_audio(ws), self._receive_results(ws))

    async def _send_audio(self, ws: Any) -> None:
        while True:
            packet = await asyncio.to_thread(self.audio_queue.get)
            if packet is None:
                await ws.send(build_asr_audio_packet(b"", is_last=True))
                return
            await ws.send(build_asr_audio_packet(packet, is_last=False))

    async def _receive_results(self, ws: Any) -> None:
        async for message in ws:
            result = parse_asr_packet(message)
            if result.get("error"):
                self.error = str(result["error"])
                self.closed = True
                self._put_packet(None)
                return
            text = extract_asr_text(result.get("payload", {}))
            if text:
                self.latest_text = text
                self.raw_text = text
                self._emit(
                    {
                        "success": True,
                        "text": text,
                        "raw_text": text,
                        "partial": not result.get("is_last"),
                        "streaming": True,
                    }
                )
            if result.get("is_last"):
                return


class VolcengineSpeech:
    def __init__(self, config: SpeechConfig | None = None) -> None:
        self.config = config or SpeechConfig.from_env()

    def is_tts_ready(self) -> bool:
        return self.config.tts_enabled and self.config.has_credentials()

    def is_asr_ready(self) -> bool:
        return self.config.asr_enabled and self.config.has_credentials()

    def is_operator_ready(self) -> bool:
        return bool(self.config.operator_polish_enabled and self.config.operator_api_key and self.config.operator_model)

    def format_operator_report(self, text: str, *, source: str = "codex") -> dict[str, Any]:
        clean_text = str(text or "").strip()
        if not clean_text:
            return {"success": False, "error": "empty operator report input"}
        if not self.is_operator_ready():
            return {"success": False, "error": "Doubao operator role model is not configured. Fill VOLCENGINE_API_KEY or ARK_API_KEY in .env."}

        try:
            import requests
        except ImportError as exc:
            raise VolcengineSpeechError("requests is not installed. Run pip install -r requirements.txt.") from exc

        payload = build_operator_report_payload(self.config, clean_text, source=source)
        started = time.time()
        response = requests.post(
            self.config.operator_endpoint,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.operator_api_key}",
            },
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise VolcengineSpeechError(f"Doubao operator model returned non-JSON HTTP {response.status_code}.") from exc
        if response.status_code >= 400:
            return {"success": False, "error": f"HTTP {response.status_code}: {compact_json(body, 500)}", "raw": body}
        report_text = extract_chat_completion_text(body).strip()
        if not report_text:
            return {"success": False, "error": "empty operator model response", "raw": body}
        return {
            "success": True,
            "text": report_text,
            "raw_text": report_text,
            "model": self.config.operator_model,
            "inference_latency": time.time() - started,
        }

    def format_phone_agent_reply(
        self,
        text: str,
        *,
        source: str = "voice",
        skill_context: str = "",
        fallback_text: str = "",
    ) -> dict[str, Any]:
        clean_text = str(text or "").strip()
        if not clean_text:
            return {"success": False, "error": "empty phone agent input"}
        if not self.is_operator_ready():
            return {"success": False, "error": "phone agent role model is not configured"}

        try:
            import requests
        except ImportError as exc:
            raise VolcengineSpeechError("requests is not installed. Run pip install -r requirements.txt.") from exc

        payload = build_phone_agent_reply_payload(
            self.config,
            clean_text,
            source=source,
            skill_context=skill_context,
            fallback_text=fallback_text,
        )
        started = time.time()
        response = requests.post(
            self.config.operator_endpoint,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.operator_api_key}",
            },
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise VolcengineSpeechError(f"Phone agent model returned non-JSON HTTP {response.status_code}.") from exc
        if response.status_code >= 400:
            return {"success": False, "error": f"HTTP {response.status_code}: {compact_json(body, 500)}", "raw": body}
        reply_text = extract_chat_completion_text(body).strip()
        if not reply_text:
            return {"success": False, "error": "empty phone agent model response", "raw": body}
        return {
            "success": True,
            "text": reply_text,
            "raw_text": reply_text,
            "model": self.config.operator_model,
            "inference_latency": time.time() - started,
        }

    def synthesize_to_file(
        self,
        text: str,
        output_path: Path,
        *,
        speech_rate: int = 0,
        loudness_rate: int = 0,
        pitch: int = 0,
    ) -> Path:
        if not self.is_tts_ready():
            raise VolcengineSpeechError("Doubao TTS is not configured. Fill VOLCENGINE_API_KEY in .env.")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        response = self.request_tts_response(
            text,
            speech_rate=speech_rate,
            loudness_rate=loudness_rate,
            pitch=pitch,
        )
        try:
            audio = self._collect_tts_audio(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
        if not audio:
            raise VolcengineSpeechError("Doubao TTS returned no audio payload.")

        audio = normalize_wav_audio(audio)
        output_path.write_bytes(audio)
        return output_path

    def request_tts_response(
        self,
        text: str,
        *,
        speech_rate: int = 0,
        loudness_rate: int = 0,
        pitch: int = 0,
        audio_format: str | None = None,
        sample_rate: int | None = None,
    ) -> Any:
        if not self.is_tts_ready():
            raise VolcengineSpeechError("Doubao TTS is not configured. Fill VOLCENGINE_API_KEY in .env.")

        try:
            import requests
        except ImportError as exc:
            raise VolcengineSpeechError("requests is not installed. Run pip install -r requirements.txt.") from exc

        request_id = str(uuid.uuid4())
        headers = {
            "Content-Type": "application/json",
            "X-Api-Resource-Id": self.config.tts_resource_id,
            "X-Api-Request-Id": request_id,
        }
        headers.update(auth_headers(self.config))
        payload = self.build_tts_payload(
            text,
            speech_rate=speech_rate,
            loudness_rate=loudness_rate,
            pitch=pitch,
            audio_format=audio_format,
            sample_rate=sample_rate,
        )

        response = requests.post(
            self.config.tts_endpoint,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=60,
            stream=True,
        )
        if response.status_code >= 400:
            raise VolcengineSpeechError(f"Doubao TTS HTTP {response.status_code}: {response.text[:500]}")
        return response

    def build_tts_payload(
        self,
        text: str,
        *,
        speech_rate: int = 0,
        loudness_rate: int = 0,
        pitch: int = 0,
        audio_format: str | None = None,
        sample_rate: int | None = None,
    ) -> dict[str, Any]:
        additions: dict[str, Any] = {
            "disable_markdown_filter": self.config.tts_disable_markdown_filter,
            "disable_emoji_filter": self.config.tts_disable_emoji_filter,
        }
        if self.config.tts_explicit_language:
            additions["explicit_language"] = self.config.tts_explicit_language
        if self.config.tts_explicit_dialect:
            additions["explicit_dialect"] = self.config.tts_explicit_dialect

        req_params: dict[str, Any] = {
            "text": text,
            "speaker": self.config.tts_speaker,
            "audio_params": {
                "format": audio_format or self.config.tts_format,
                "sample_rate": int(sample_rate or self.config.tts_sample_rate),
                "speech_rate": max(-50, min(100, int(speech_rate))),
                "loudness_rate": max(-50, min(100, int(loudness_rate))),
            },
            "additions": json.dumps(additions, ensure_ascii=False),
            "post_process": {"pitch": max(-12, min(12, int(pitch)))},
        }
        if self.config.tts_model:
            req_params["model"] = self.config.tts_model

        return {
            "user": {"uid": "ai-desk-phone"},
            "req_params": req_params,
        }

    def _collect_tts_audio(self, response: Any) -> bytes:
        return b"".join(self.iter_tts_audio_chunks(response))

    def iter_tts_audio_chunks(self, response: Any) -> Any:
        content_type = str(response.headers.get("Content-Type", "")).lower()
        if "text/event-stream" not in content_type and "json" not in content_type:
            body = response.content
            if body:
                yield body
            return

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
                yield chunk

    def synthesize_tts_pcm_chunks(
        self,
        text: str,
        *,
        speech_rate: int = 0,
        loudness_rate: int = 0,
        pitch: int = 0,
        sample_rate: int | None = None,
    ) -> Any:
        response = self.request_tts_response(
            text,
            speech_rate=speech_rate,
            loudness_rate=loudness_rate,
            pitch=pitch,
            audio_format="pcm",
            sample_rate=sample_rate or self.config.tts_sample_rate,
        )
        try:
            yield from self.iter_tts_audio_chunks(response)
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()

    def list_speakers(self, resource_ids: list[str] | None = None, voice_types: list[str] | None = None) -> dict[str, Any]:
        if not self.config.app_key or not self.config.access_key:
            return {
                "ok": False,
                "error": "音色列表刷新需要火山 OpenAPI 访问密钥，已保留当前默认音色。",
                "speakers": [],
            }

        try:
            import requests
        except ImportError as exc:
            raise VolcengineSpeechError("requests is not installed. Run pip install -r requirements.txt.") from exc

        body: dict[str, Any] = {
            "ResourceIDs": [resource_id for resource_id in (resource_ids or [self.config.tts_resource_id]) if resource_id],
        }
        if voice_types:
            body["VoiceTypes"] = voice_types
        response = signed_openapi_request(
            self.config,
            action="ListSpeakers",
            version="2025-05-20",
            body=body,
            region=os.getenv("VOLCENGINE_REGION", "cn-beijing"),
            service="speech_saas_prod",
        )
        speakers = normalize_speaker_list(response)
        return {"ok": True, "speakers": speakers, "raw_count": len(speakers)}

    def transcribe_wav(self, wav_path: Path) -> dict[str, Any]:
        pcm, sample_rate = read_wav_mono_pcm(wav_path)
        return self.transcribe_pcm(pcm, sample_rate)

    def transcribe_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> dict[str, Any]:
        if not self.is_asr_ready():
            return {"success": False, "error": "Doubao ASR is not configured. Fill VOLCENGINE_API_KEY in .env."}
        return asyncio.run(self._transcribe_pcm_async(pcm_bytes, sample_rate))

    def start_streaming_asr(
        self,
        sample_rate: int,
        *,
        on_result: Callable[[dict[str, Any]], None] | None = None,
    ) -> StreamingAsrSession:
        if not self.is_asr_ready():
            raise VolcengineSpeechError("Doubao ASR is not configured. Fill VOLCENGINE_API_KEY in .env.")
        session = StreamingAsrSession(self, sample_rate, on_result=on_result)
        session.start()
        return session

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
        init_payload = build_asr_init_payload(self.config, sample_rate)
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


def signed_openapi_request(
    config: SpeechConfig,
    *,
    action: str,
    version: str,
    body: dict[str, Any],
    region: str,
    service: str,
) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:
        raise VolcengineSpeechError("requests is not installed. Run pip install -r requirements.txt.") from exc

    host = "open.volcengineapi.com"
    method = "POST"
    canonical_uri = "/"
    canonical_querystring = f"Action={action}&Version={version}"
    body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload_hash = hashlib.sha256(body_bytes).hexdigest()
    now = time.gmtime()
    x_date = time.strftime("%Y%m%dT%H%M%SZ", now)
    short_date = time.strftime("%Y%m%d", now)
    signed_headers = "host;x-content-sha256;x-date"
    canonical_headers = f"host:{host}\nx-content-sha256:{payload_hash}\nx-date:{x_date}\n"
    canonical_request = "\n".join(
        [method, canonical_uri, canonical_querystring, canonical_headers, signed_headers, payload_hash]
    )
    credential_scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        ["HMAC-SHA256", x_date, credential_scope, hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()]
    )
    signing_key = volcengine_signing_key(config.access_key, short_date, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        f"HMAC-SHA256 Credential={config.app_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    url = f"https://{host}/?{canonical_querystring}"
    response = requests.post(
        url,
        headers={
            "Authorization": authorization,
            "Content-Type": "application/json",
            "Host": host,
            "X-Content-Sha256": payload_hash,
            "X-Date": x_date,
        },
        data=body_bytes,
        timeout=20,
    )
    try:
        payload = response.json()
    except ValueError as exc:
        raise VolcengineSpeechError(f"Volcengine OpenAPI returned non-JSON HTTP {response.status_code}.") from exc
    if response.status_code >= 400:
        raise VolcengineSpeechError(f"Volcengine OpenAPI HTTP {response.status_code}: {payload}")
    metadata = payload.get("ResponseMetadata") if isinstance(payload, dict) else None
    error = metadata.get("Error") if isinstance(metadata, dict) else None
    if error:
        message = error.get("Message") or error.get("Code") or str(error)
        raise VolcengineSpeechError(f"Volcengine OpenAPI error: {message}")
    return payload


def volcengine_signing_key(secret_key: str, date: str, region: str, service: str) -> bytes:
    key = secret_key.encode("utf-8")
    for value in [date, region, service, "request"]:
        key = hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()
    return key


def normalize_speaker_list(payload: dict[str, Any]) -> list[dict[str, str]]:
    seen: set[str] = set()
    speakers: list[dict[str, str]] = []
    for raw in walk_dicts(payload):
        speaker_id = first_text(
            raw,
            "SpeakerID",
            "SpeakerId",
            "speaker_id",
            "speaker",
            "VoiceType",
            "voice_type",
            "VoiceID",
            "VoiceId",
            "voice_id",
        )
        if not speaker_id or speaker_id in seen:
            continue
        name = first_text(raw, "Name", "SpeakerName", "VoiceName", "DisplayName", "Alias", "Title") or speaker_id
        model = first_text(raw, "ResourceID", "ResourceId", "resource_id", "Model", "ModelName", "model")
        language = first_text(raw, "Language", "language", "Lang", "lang")
        voice_type = first_text(raw, "VoiceType", "voice_type", "Type", "type")
        speakers.append(
            {
                "id": speaker_id,
                "name": name,
                "model": model,
                "language": language,
                "type": voice_type,
            }
        )
        seen.add(speaker_id)
    return speakers


def walk_dicts(value: Any) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if isinstance(value, dict):
        results.append(value)
        for child in value.values():
            results.extend(walk_dicts(child))
    elif isinstance(value, list):
        for item in value:
            results.extend(walk_dicts(item))
    return results


def first_text(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


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
