from __future__ import annotations

import argparse
import base64
import ctypes
from collections import deque
from dataclasses import asdict, dataclass, field
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import uuid

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised on machines without pyserial
    serial = None
    list_ports = None

try:
    from volcengine_speech import VolcengineSpeech, VolcengineSpeechError, upsert_dotenv_values
except ImportError:  # pragma: no cover - kept optional for partial deployments
    VolcengineSpeech = None  # type: ignore[assignment]
    VolcengineSpeechError = RuntimeError  # type: ignore[assignment]
    upsert_dotenv_values = None  # type: ignore[assignment]

try:
    from audio_recorder import AudioRecorder, AudioRecorderError
except ImportError:  # pragma: no cover - kept optional for partial deployments
    AudioRecorder = None  # type: ignore[assignment]
    AudioRecorderError = RuntimeError  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "config" / "ai_desk_phone_console.json"
DEFAULT_WEB_PORT = 8765
DEFAULT_BAUD = 115200
DEFAULT_UDP_TELEMETRY_PORT = 8766
DEFAULT_UDP_COMMAND_PORT = 8767
SERIAL_SCAN_INTERVAL_SECONDS = 2.0
SERIAL_LOG_INTERVAL_SECONDS = 15.0
FIRMWARE_DATA_WAIT_SECONDS = 3.0
OPERATOR_RING_ON_SECONDS = 1.0
OPERATOR_RING_OFF_SECONDS = 4.0
OPERATOR_RING_TIMEOUT_SECONDS = 90.0
OPERATOR_BUSY_ON_SECONDS = 0.5
OPERATOR_BUSY_OFF_SECONDS = 0.5
SIM_SAMPLE_INTERVAL_SECONDS = 1.0
SIM_AUTO_PULSE_INTERVAL_SECONDS = 8.0
SIM_AUTO_PULSE_SECONDS = 0.9
SIMULATED_REPLY_CHARS_PER_SECOND = 7.0
SIMULATED_REPLY_MIN_SECONDS = 1.5
SIMULATED_REPLY_MAX_SECONDS = 18.0
VOICE_TURN_MIN_SECONDS = 1.0
VOICE_TURN_SILENCE_SECONDS = 1.1
VOICE_TURN_MAX_SECONDS = 25.0
VOICE_RESTART_DELAY_SECONDS = 0.5
VOICE_LEVEL_THRESHOLD = 650.0

HOOK_SCHEMES: dict[str, dict[str, str]] = {
    "scheme1": {
        "label": "方案 1",
        "pressed_level": "HIGH",
        "description": "HIGH = 按下，LOW = 抬起",
    },
    "scheme2": {
        "label": "方案 2",
        "pressed_level": "LOW",
        "description": "LOW = 按下，HIGH = 抬起",
    },
}

BUSINESS_MODES: dict[str, dict[str, str]] = {
    "codex": {
        "label": "输入法 / 通讯员提醒",
        "description": "摘机触发开始输入快捷键；挂机触发结束/提交快捷键；任务完成后进入回话队列并呼叫桌面电话。",
    },
    "doubao": {
        "label": "Agent 通讯员模式",
        "description": "抬起电话后进入语音报告或全双工对话；Agent 可操作本机工具，高风险付款类动作仍需确认。",
    },
}


ACTION_PRESETS: dict[str, dict[str, str]] = {
    "current": {
        "label": "方案一：当前配置",
        "press_action_text": "控制键+Windows键+Shift键, 延迟1000毫秒, 回车",
        "release_action_text": "控制键+Windows键+Shift键",
    },
    "voice_call": {
        "label": "方案二：语音通话键",
        "press_action_text": "Ctrl+Alt+I",
        "release_action_text": "Ctrl+Alt+U",
    },
}


@dataclass
class SensorSample:
    ms: int
    adc: int
    digital: str
    raw_line: str
    firmware_state: str | None = None
    score: int | None = None
    pin: int | None = None
    hook: str | None = None
    buzzer: str | None = None
    led: str | None = None
    led_pin: int | None = None
    wifi_connected: bool | None = None
    wifi_ip: str | None = None
    wifi_rssi: int | None = None
    wifi_status: int | None = None
    wifi_disconnect_reason: int | None = None
    adc_synthetic: bool = False


@dataclass
class StateEvent:
    from_state: str
    to_state: str
    sample: SensorSample
    reason: str


@dataclass
class ConsoleConfig:
    business_mode: str = "codex"
    hook_scheme: str = "scheme1"
    adc_low_means_pressed: bool = True
    press_threshold: int = 75
    release_threshold: int = 92
    strong_low_press_threshold: int = 45
    strong_high_press_threshold: int = 120
    debounce_ms: int = 30
    press_lockout_ms: int = 350
    press_score_step: int = 2
    strong_press_score_step: int = 3
    release_score_step: int = 1
    score_max: int = 8
    score_trigger: int = 5
    peak_hold_ms: int = 350
    sample_interval_ms: int = 50
    press_action_text: str = "控制键+Windows键+Shift键"
    release_action_text: str = "控制键+Windows键+Shift键, 延迟1000毫秒, 回车"
    enable_actions: bool = True
    enable_callback: bool = True
    enable_tts_playback: bool = True
    tts_rate: int = 0
    tts_volume: int = 100
    audio_output_device: str = ""
    enable_voice_asr: bool = True
    voice_record_sample_rate: int = 16000
    voice_record_device: str = ""
    voice_auto_transcribe: bool = True
    voice_reply_policy: str = "silent"
    agent_permission_profile: str = "commander"

    def __post_init__(self) -> None:
        self.business_mode = normalize_business_mode(self.business_mode)
        self.hook_scheme = normalize_hook_scheme(self.hook_scheme)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsoleConfig":
        allowed = set(cls.__dataclass_fields__.keys())
        clean = {key: value for key, value in data.items() if key in allowed}
        return cls(**clean)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReplyTask:
    id: str
    source: str
    title: str
    text: str
    status: str = "queued"
    audio_path: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> ConsoleConfig:
    if not path.exists():
        return ConsoleConfig()

    with path.open("r", encoding="utf-8") as file:
        return ConsoleConfig.from_dict(json.load(file))


def save_config(path: Path, config: ConsoleConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(config.to_dict(), file, ensure_ascii=False, indent=2)
        file.write("\n")


def normalize_hook_scheme(value: Any) -> str:
    scheme = str(value or "scheme1").strip()
    return scheme if scheme in HOOK_SCHEMES else "scheme1"


def normalize_business_mode(value: Any) -> str:
    mode = str(value or "codex").strip()
    return mode if mode in BUSINESS_MODES else "codex"


def hook_pressed_level(config: ConsoleConfig) -> str:
    return HOOK_SCHEMES[normalize_hook_scheme(config.hook_scheme)]["pressed_level"]


def interpret_hook_state(digital: str, config: ConsoleConfig) -> str:
    return "PRESSED" if normalize_digital(digital) == hook_pressed_level(config) else "RELEASED"


def hook_state_label(state: str) -> str:
    return "按下" if state == "PRESSED" else "抬起"


WATCH_RE = re.compile(
    r"(?:WATCH GPIO1|PRESSED|RELEASED).*?"
    r"adc=(?P<adc>\d+)\s+digital=(?P<digital>LOW|HIGH)"
    r".*?(?:=>\s*(?P<state>PRESSED|RELEASED))?",
    re.IGNORECASE,
)


def normalize_digital(value: Any) -> str:
    if isinstance(value, str):
        upper = value.upper()
        if upper in {"LOW", "0"}:
            return "LOW"
        if upper in {"HIGH", "1"}:
            return "HIGH"
    return "HIGH" if int(value) else "LOW"


def parse_serial_line(line: str) -> SensorSample | None:
    raw_line = line.strip()
    if not raw_line:
        return None

    now_ms = int(time.monotonic() * 1000)

    if raw_line.startswith("{"):
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict) and "digital" in payload and ("adc" in payload or "hook_pin" in payload or "pin" in payload):
            digital = normalize_digital(payload["digital"])
            has_adc = "adc" in payload
            hook = payload.get("hook")
            firmware_state = payload.get("state")
            if firmware_state is None and isinstance(hook, str):
                hook_upper = hook.upper()
                if hook_upper == "OFF_HOOK":
                    firmware_state = "PRESSED"
                elif hook_upper == "ON_HOOK":
                    firmware_state = "RELEASED"

            return SensorSample(
                ms=int(payload.get("ms", now_ms)),
                adc=int(payload["adc"]) if has_adc else (0 if digital == "LOW" else 4095),
                digital=digital,
                raw_line=raw_line,
                firmware_state=firmware_state,
                score=payload.get("score"),
                pin=payload.get("pin", payload.get("hook_pin")),
                hook=hook,
                buzzer=payload.get("buzzer"),
                led=payload.get("led"),
                led_pin=payload.get("led_pin"),
                wifi_connected=payload.get("wifi_connected"),
                wifi_ip=payload.get("wifi_ip"),
                wifi_rssi=payload.get("wifi_rssi"),
                wifi_status=payload.get("wifi_status"),
                wifi_disconnect_reason=payload.get("wifi_disconnect_reason"),
                adc_synthetic=bool(payload.get("adc_synthetic", not has_adc)),
            )

    match = WATCH_RE.search(raw_line)
    if not match:
        return None

    state_match = re.search(r"=>\s*(PRESSED|RELEASED)", raw_line, re.IGNORECASE)

    return SensorSample(
        ms=now_ms,
        adc=int(match.group("adc")),
        digital=match.group("digital").upper(),
        raw_line=raw_line,
        firmware_state=state_match.group(1).upper() if state_match else None,
    )


KEY_ALIASES = {
    "ctrl": "ctrl",
    "control": "ctrl",
    "控制键": "ctrl",
    "左控制键": "ctrl",
    "win": "win",
    "windows": "win",
    "windows键": "win",
    "窗口键": "win",
    "shift": "shift",
    "shift键": "shift",
    "上档键": "shift",
    "alt": "alt",
    "alt键": "alt",
    "enter": "enter",
    "return": "enter",
    "回车": "enter",
    "回车键": "enter",
    "space": "space",
    "空格": "space",
    "tab": "tab",
    "制表键": "tab",
    "esc": "esc",
    "escape": "esc",
    "退出键": "esc",
}


def normalize_key_name(value: str) -> str:
    compact = re.sub(r"\s+", "", value).lower()
    compact = compact.replace("＋", "+")
    key = KEY_ALIASES.get(compact)
    if key is None and re.fullmatch(r"[a-z0-9]", compact):
        key = compact
    if key is None:
        raise ValueError(f"不支持的按键: {value}")
    return key


def action_presets() -> dict[str, dict[str, str]]:
    return {name: preset.copy() for name, preset in ACTION_PRESETS.items()}


def compact_hook_text(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:4000]


def extract_reply_text_from_hook(data: dict[str, Any]) -> str:
    for key in ("text", "reply", "summary", "message", "codex_payload"):
        text = compact_hook_text(data.get(key))
        if text:
            return text

    args = data.get("args")
    if isinstance(args, list):
        text = compact_hook_text(" ".join(str(item) for item in args))
        if text:
            return text

    return "任务已经完成，通讯员等待向首长回报。"


def parse_action_text(text: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    parts = [part.strip() for part in re.split(r"[,，;；]", text) if part.strip()]

    for part in parts:
        delay_match = re.search(r"(?:延迟|等待|delay)\s*(\d+)\s*(?:毫秒|ms)?", part, re.IGNORECASE)
        if delay_match:
            steps.append({"type": "delay", "ms": int(delay_match.group(1))})
            continue

        keys = [normalize_key_name(piece) for piece in re.split(r"[+＋]", part) if piece.strip()]
        if keys:
            steps.append({"type": "hotkey", "keys": keys})

    return steps


def canonical_action_text(text: str) -> str:
    canonical_steps: list[str] = []

    for step in parse_action_text(text):
        if step["type"] == "delay":
            canonical_steps.append(f"delay:{int(step['ms'])}")
        elif step["type"] == "hotkey":
            canonical_steps.append("+".join(step["keys"]))

    return ",".join(canonical_steps)


def canonical_steps_label(steps: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    for step in steps:
        if step["type"] == "delay":
            labels.append(f"delay:{int(step['ms'])}ms")
        elif step["type"] == "hotkey":
            labels.append("+".join(step["keys"]))
    return ", ".join(labels)


def build_device_config_command(config: ConsoleConfig) -> str:
    payload = {
        "type": "config",
        "config": {
            "hook_scheme": normalize_hook_scheme(config.hook_scheme),
            "adc_low_means_pressed": config.adc_low_means_pressed,
            "press_threshold": config.press_threshold,
            "release_threshold": config.release_threshold,
            "strong_low_press_threshold": config.strong_low_press_threshold,
            "strong_high_press_threshold": config.strong_high_press_threshold,
            "debounce_ms": config.debounce_ms,
            "press_lockout_ms": config.press_lockout_ms,
            "press_score_step": config.press_score_step,
            "strong_press_score_step": config.strong_press_score_step,
            "release_score_step": config.release_score_step,
            "score_max": config.score_max,
            "score_trigger": config.score_trigger,
            "peak_hold_ms": config.peak_hold_ms,
            "sample_interval_ms": config.sample_interval_ms,
            "enable_actions": config.enable_actions,
            "press_action": canonical_action_text(config.press_action_text),
            "release_action": canonical_action_text(config.release_action_text),
        },
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class WindowsHotkeySender:
    VK = {
        "ctrl": 0x11,
        "shift": 0x10,
        "alt": 0x12,
        "win": 0x5B,
        "enter": 0x0D,
        "space": 0x20,
        "tab": 0x09,
        "esc": 0x1B,
    }
    KEYEVENTF_KEYUP = 0x0002

    def __init__(self) -> None:
        self.user32 = getattr(getattr(ctypes, "windll", None), "user32", None)

    def send_steps(self, steps: list[dict[str, Any]]) -> None:
        for step in steps:
            if step["type"] == "delay":
                time.sleep(step["ms"] / 1000)
            elif step["type"] == "hotkey":
                self.send_hotkey(step["keys"])

    def send_hotkey(self, keys: list[str]) -> None:
        if self.user32 is None:
            return

        vk_codes = [self.VK[key] for key in keys]
        for vk in vk_codes:
            self.user32.keybd_event(vk, 0, 0, 0)
        time.sleep(0.04)
        for vk in reversed(vk_codes):
            self.user32.keybd_event(vk, 0, self.KEYEVENTF_KEYUP, 0)
        time.sleep(0.04)


class HookStateMachine:
    def __init__(self, config: ConsoleConfig) -> None:
        self.config = config
        self.score = 0
        self.stable_state = "RELEASED"
        self.raw_state = "RELEASED"
        self.last_change_ms = 0
        self.last_press_evidence_ms: int | None = None
        self.last_press_trigger_ms: int | None = None

    def update_config(self, config: ConsoleConfig) -> None:
        self.config = config
        self.score = 0
        self.stable_state = "RELEASED"
        self.raw_state = "RELEASED"
        self.last_change_ms = 0
        self.last_press_evidence_ms = None
        self.last_press_trigger_ms = None

    def is_press_evidence(self, adc: int) -> bool:
        if self.config.adc_low_means_pressed:
            return adc <= self.config.press_threshold
        return adc >= self.config.press_threshold

    def is_strong_press_evidence(self, adc: int) -> bool:
        if self.config.adc_low_means_pressed:
            return adc <= self.config.strong_low_press_threshold
        return adc >= self.config.strong_high_press_threshold

    def is_release_evidence(self, adc: int) -> bool:
        if self.config.adc_low_means_pressed:
            return adc >= self.config.release_threshold
        return adc <= self.config.release_threshold

    def has_recent_press_peak(self, sample: SensorSample) -> bool:
        if self.last_press_evidence_ms is None:
            return False
        return sample.ms - self.last_press_evidence_ms <= self.config.peak_hold_ms

    def update_score(self, sample: SensorSample) -> None:
        if self.is_strong_press_evidence(sample.adc):
            self.last_press_evidence_ms = sample.ms
            self.score = min(self.config.score_max, self.score + self.config.strong_press_score_step)
            return

        if self.is_press_evidence(sample.adc):
            self.last_press_evidence_ms = sample.ms
            self.score = min(self.config.score_max, self.score + self.config.press_score_step)
            return

        if self.is_release_evidence(sample.adc):
            if self.has_recent_press_peak(sample):
                return
            self.score = max(0, self.score - self.config.release_score_step)
            return

        if not self.has_recent_press_peak(sample) and self.score > 0:
            self.score = max(0, self.score - 1)

    def state_from_score(self, fallback: str) -> str:
        if self.score >= self.config.score_trigger:
            return "PRESSED"
        if self.score <= 0:
            return "RELEASED"
        return fallback

    def update(self, sample: SensorSample) -> list[StateEvent]:
        self.update_score(sample)
        next_raw_state = self.state_from_score(self.raw_state)

        if next_raw_state != self.raw_state:
            self.raw_state = next_raw_state
            self.last_change_ms = sample.ms

        events: list[StateEvent] = []
        if sample.ms - self.last_change_ms >= self.config.debounce_ms and self.raw_state != self.stable_state:
            previous = self.stable_state
            self.stable_state = self.raw_state
            events.append(
                StateEvent(
                    from_state=previous,
                    to_state=self.stable_state,
                    sample=sample,
                    reason=f"score={self.score} debounce={self.config.debounce_ms}ms",
                )
            )

        return events


class AppState:
    def __init__(self, config: ConsoleConfig, config_path: Path, simulation_enabled: bool = True) -> None:
        self.config = config
        self.config_path = config_path
        self.machine = HookStateMachine(config)
        self.sender = WindowsHotkeySender()
        self.lock = threading.RLock()
        self.subscribers: list[queue.Queue[dict[str, Any]]] = []
        self.raw_logs: deque[str] = deque(maxlen=300)
        self.state_logs: deque[str] = deque(maxlen=300)
        self.action_logs: deque[str] = deque(maxlen=300)
        self.samples: deque[dict[str, Any]] = deque(maxlen=240)
        self.current_sample: dict[str, Any] | None = None
        self.last_state = "RELEASED"
        self.serial_handle: Any = None
        self.serial_port: str | None = None
        self.serial_lock = threading.Lock()
        self.udp_socket: socket.socket | None = None
        self.udp_device_address: tuple[str, int] | None = None
        self.udp_last_seen: float | None = None
        self.udp_lock = threading.Lock()
        self.alerting = False
        self.alert_phase = "idle"
        self.alert_started_at: float | None = None
        self.alert_stop_event = threading.Event()
        self.alert_thread: threading.Thread | None = None
        self.alert_last_hook_state: str | None = None
        self.pending_report_text: str | None = None
        self.reply_queue: deque[ReplyTask] = deque()
        self.active_reply: ReplyTask | None = None
        self.completed_replies: deque[ReplyTask] = deque(maxlen=40)
        self.reply_counter = 0
        self.playback_thread: threading.Thread | None = None
        self.playback_stop_event = threading.Event()
        self.playback_process: subprocess.Popen[bytes] | None = None
        self.action_lock = threading.Lock()
        self.callback_session_active = False
        self.speech = VolcengineSpeech() if VolcengineSpeech is not None else None
        self.recorder = AudioRecorder() if AudioRecorder is not None else None
        self.voice_recording = False
        self.voice_recording_path: str | None = None
        self.voice_last_result: dict[str, Any] | None = None
        self.voice_last_error: str | None = None
        self.voice_session_id = 0
        self.voice_monitor_thread: threading.Thread | None = None
        self.voice_processing = False
        self.voice_cancel_reason: str | None = None
        self.simulation_enabled = simulation_enabled
        self.sim_hook_state = "PRESSED"
        self.sim_buzzer_on = False
        self.sim_led_on = False
        self.sim_hook_pin = 0
        self.sim_buzzer_pin = 21
        self.sim_led_pin = 20
        self.sim_wifi_ip = "192.0.2.113"
        self.sim_wifi_rssi = -48
        self.sim_started_at: float | None = time.monotonic() if simulation_enabled else None
        self.sim_last_pulse_at = time.monotonic()
        self.sim_auto_pulse_enabled = False

    def interpreted_state_for_sample(self, sample: SensorSample) -> str:
        if sample.digital:
            return interpret_hook_state(sample.digital, self.config)
        return sample.firmware_state or self.last_state

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        subscriber: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=200)
        with self.lock:
            self.subscribers.append(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: queue.Queue[dict[str, Any]]) -> None:
        with self.lock:
            if subscriber in self.subscribers:
                self.subscribers.remove(subscriber)

    def publish(self, event: dict[str, Any]) -> None:
        with self.lock:
            for subscriber in list(self.subscribers):
                try:
                    subscriber.put_nowait(event)
                except queue.Full:
                    pass

    def add_raw_log(self, line: str) -> None:
        text = line.strip()
        with self.lock:
            self.raw_logs.append(text)
        self.publish({"type": "raw_log", "text": text})

    def add_state_log(self, line: str) -> None:
        stamped = timestamped(line)
        with self.lock:
            self.state_logs.append(stamped)
        self.publish({"type": "state_log", "text": stamped})

    def add_action_log(self, line: str) -> None:
        stamped = timestamped(line)
        with self.lock:
            self.action_logs.append(stamped)
        self.publish({"type": "action_log", "text": stamped})

    def attach_serial(self, ser: Any, port: str) -> None:
        with self.serial_lock:
            self.serial_handle = ser
            self.serial_port = port
        self.publish({"type": "serial_status", "serial_connected": True, "port": port})

    def detach_serial(self, ser: Any) -> None:
        disconnected = False
        with self.serial_lock:
            if self.serial_handle is ser:
                self.serial_handle = None
                self.serial_port = None
                disconnected = True
        if disconnected:
            self.publish({"type": "serial_status", "serial_connected": False, "port": None})

    def is_serial_connected(self) -> bool:
        with self.serial_lock:
            return self.serial_handle is not None

    def current_serial_port(self) -> str | None:
        with self.serial_lock:
            return self.serial_port

    def attach_udp_socket(self, sock: socket.socket) -> None:
        with self.udp_lock:
            self.udp_socket = sock
        self.publish({"type": "udp_status", "udp_listening": True, "device": self.current_udp_device()})

    def detach_udp_socket(self, sock: socket.socket) -> None:
        detached = False
        with self.udp_lock:
            if self.udp_socket is sock:
                self.udp_socket = None
                self.udp_device_address = None
                self.udp_last_seen = None
                detached = True
        if detached:
            self.publish({"type": "udp_status", "udp_listening": False, "device": None})

    def update_udp_device(self, host: str, command_port: int) -> None:
        with self.udp_lock:
            self.udp_device_address = (host, command_port)
            self.udp_last_seen = time.monotonic()
            device = f"{host}:{command_port}"
        self.publish({"type": "udp_status", "udp_listening": True, "device": device})

    def current_udp_device(self) -> str | None:
        with self.udp_lock:
            if self.udp_device_address is None:
                return None
            return f"{self.udp_device_address[0]}:{self.udp_device_address[1]}"

    def simulation_label(self) -> str | None:
        with self.lock:
            if not self.simulation_enabled:
                return None
        return "模拟发送端 127.0.0.1"

    def simulation_status(self) -> dict[str, Any]:
        with self.lock:
            enabled = self.simulation_enabled
            hook_state = self.sim_hook_state
            pressed_level = hook_pressed_level(self.config)
            released_level = "LOW" if pressed_level == "HIGH" else "HIGH"
            started_at = self.sim_started_at
            return {
                "simulation_enabled": enabled,
                "simulation_device": "模拟发送端 127.0.0.1" if enabled else None,
                "simulation_hook_state": hook_state,
                "simulation_hook_label": hook_state_label(hook_state),
                "simulation_pressed_level": pressed_level,
                "simulation_released_level": released_level,
                "simulation_buzzer": "ON" if self.sim_buzzer_on else "OFF",
                "simulation_led": "ON" if self.sim_led_on else "OFF",
                "simulation_wifi_ip": self.sim_wifi_ip,
                "simulation_wifi_rssi": self.sim_wifi_rssi,
                "simulation_uptime_seconds": int(time.monotonic() - started_at) if enabled and started_at else 0,
                "simulation_auto_pulse_enabled": self.sim_auto_pulse_enabled,
                "simulation_sample_interval_seconds": SIM_SAMPLE_INTERVAL_SECONDS,
                "simulation_pulse_interval_seconds": SIM_AUTO_PULSE_INTERVAL_SECONDS,
            }

    def publish_simulation_status(self) -> None:
        self.publish({"type": "simulation_status", **self.simulation_status()})

    def next_reply_text_locked(self) -> str | None:
        if self.active_reply is not None:
            return self.active_reply.text
        if self.reply_queue:
            return self.reply_queue[0].text
        return None

    def reply_status(self) -> dict[str, Any]:
        with self.lock:
            return {
                "reply_queue": [reply.to_dict() for reply in self.reply_queue],
                "active_reply": self.active_reply.to_dict() if self.active_reply else None,
                "completed_replies": [reply.to_dict() for reply in self.completed_replies],
                "pending_report_text": self.pending_report_text,
                "playback_active": self.playback_thread is not None and self.playback_thread.is_alive(),
                "queue_size": len(self.reply_queue),
                "callback_enabled": self.config.enable_callback,
                "tts_enabled": self.config.enable_tts_playback,
                "audio_output_device": self.config.audio_output_device,
                "agent_permission_profile": self.config.agent_permission_profile,
                "callback_session_active": self.callback_session_active,
            }

    def publish_reply_status(self) -> None:
        self.publish({"type": "reply_status", **self.reply_status()})

    def speech_status(self) -> dict[str, Any]:
        if self.speech is None:
            return {"tts_ready": False, "asr_ready": False, "error": "volcengine_speech module is not available"}
        config = self.speech.config
        return {
            "tts_ready": self.speech.is_tts_ready(),
            "asr_ready": self.speech.is_asr_ready(),
            "credential_mode": config.credential_mode(),
            "tts_endpoint": config.tts_endpoint,
            "tts_resource_id": config.tts_resource_id,
            "tts_speaker": config.tts_speaker,
            "tts_format": config.tts_format,
            "asr_endpoint": config.asr_endpoint,
            "asr_resource_id": config.asr_resource_id,
            "asr_model": config.asr_model,
        }

    def update_speech_env(self, values: dict[str, str]) -> dict[str, Any]:
        if upsert_dotenv_values is None or VolcengineSpeech is None:
            return {"ok": False, "error": "volcengine_speech module is not available"}
        upsert_dotenv_values(values)
        with self.lock:
            self.speech = VolcengineSpeech()
        return {"ok": True, **self.speech_status()}

    def transcribe_audio_file(self, audio_path: Path) -> dict[str, Any]:
        if self.speech is None:
            return {"success": False, "error": "volcengine_speech module is not available"}
        if not audio_path.exists():
            return {"success": False, "error": f"audio file not found: {audio_path}"}
        try:
            return self.speech.transcribe_wav(audio_path)
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    def voice_status(self) -> dict[str, Any]:
        recorder_ready = self.recorder is not None
        recorder_dependency_ready = bool(self.recorder.dependencies_available()) if self.recorder is not None else False
        duration = self.recorder.current_duration_seconds() if self.recorder is not None else 0.0
        with self.lock:
            return {
                "voice_enabled": self.config.enable_voice_asr,
                "recorder_ready": recorder_ready,
                "recorder_dependency_ready": recorder_dependency_ready,
                "recording": self.voice_recording,
                "processing": self.voice_processing,
                "session_id": self.voice_session_id,
                "recording_duration_seconds": duration,
                "recording_path": self.voice_recording_path,
                "last_result": self.voice_last_result,
                "last_error": self.voice_last_error,
                "cancel_reason": self.voice_cancel_reason,
                "sample_rate": self.config.voice_record_sample_rate,
                "device": self.config.voice_record_device,
                "auto_transcribe": self.config.voice_auto_transcribe,
                "reply_policy": self.config.voice_reply_policy,
            }

    def publish_voice_status(self) -> None:
        self.publish({"type": "voice_status", **self.voice_status()})

    def start_voice_recording(self, reason: str = "manual") -> dict[str, Any]:
        with self.lock:
            if not self.config.enable_voice_asr:
                result = {"ok": False, "recording": False, "error": "voice ASR is disabled"}
                self.voice_last_error = result["error"]
                return result
            if self.voice_processing:
                return {"ok": False, "recording": False, "processing": True, "error": "voice turn is processing"}
            sample_rate = int(self.config.voice_record_sample_rate or 16000)
            device = self.config.voice_record_device.strip() or None

        if self.recorder is None:
            error = "audio_recorder module is not available"
            with self.lock:
                self.voice_last_error = error
            return {"ok": False, "recording": False, "error": error}
        if self.recorder.is_recording():
            with self.lock:
                session_id = self.voice_session_id
            return {"ok": True, "recording": True, "already_active": True, "session_id": session_id}

        try:
            self.recorder.start(sample_rate=sample_rate, channels=1, device=device)
        except AudioRecorderError as exc:
            with self.lock:
                self.voice_recording = False
                self.voice_last_error = str(exc)
            self.add_action_log(f"语音录音启动失败：{exc}")
            self.publish_voice_status()
            return {"ok": False, "recording": False, "error": str(exc)}

        output_path = ROOT / "data" / "recordings" / f"voice-{int(time.time())}-{uuid.uuid4().hex}.wav"
        with self.lock:
            self.voice_session_id += 1
            session_id = self.voice_session_id
            self.voice_recording = True
            self.voice_recording_path = str(output_path)
            self.voice_last_error = None
            self.voice_cancel_reason = None
        self.add_action_log(f"语音录音已启动：{reason}")
        self.publish_voice_status()
        return {"ok": True, "recording": True, "path": str(output_path), "session_id": session_id}

    def stop_voice_recording(
        self,
        reason: str = "manual",
        *,
        reply_behavior: str = "legacy",
        session_id: int | None = None,
    ) -> dict[str, Any]:
        if self.recorder is None or not self.recorder.is_recording():
            with self.lock:
                self.voice_recording = False
            self.publish_voice_status()
            return {"ok": False, "recording": False, "error": "recording is not active"}

        with self.lock:
            if session_id is not None and session_id != self.voice_session_id:
                return {"ok": False, "recording": self.voice_recording, "error": "voice session is stale"}
            output_path = Path(self.voice_recording_path or (ROOT / "data" / "recordings" / f"voice-{int(time.time())}-{uuid.uuid4().hex}.wav"))
            auto_transcribe = self.config.voice_auto_transcribe
            reply_policy = self.config.voice_reply_policy
            active_session_id = self.voice_session_id
            self.voice_processing = True

        try:
            recording = self.recorder.stop_to_wav(output_path)
        except AudioRecorderError as exc:
            with self.lock:
                self.voice_recording = False
                self.voice_processing = False
                self.voice_last_error = str(exc)
            self.add_action_log(f"语音录音停止失败：{exc}")
            self.publish_voice_status()
            return {"ok": False, "recording": False, "error": str(exc)}

        with self.lock:
            self.voice_recording = False
            self.voice_recording_path = str(recording.path)
        self.add_action_log(f"语音录音已停止：{reason}，时长 {recording.duration_seconds:.1f}s")

        transcript: dict[str, Any] | None = None
        if auto_transcribe:
            transcript = self.transcribe_audio_file(recording.path)
            with self.lock:
                self.voice_last_result = transcript
                self.voice_last_error = None if transcript.get("success") else str(transcript.get("error", "ASR failed"))
            if transcript.get("success"):
                text = str(transcript.get("text", "") or "").strip()
                self.add_action_log(f"豆包 ASR 识别结果：{text or '（空）'}")
                if text and reply_policy == "callback":
                    self.handle_voice_reply_text(text, reply_behavior, active_session_id)
            else:
                self.add_action_log(f"豆包 ASR 识别失败：{transcript.get('error')}")

        with self.lock:
            if active_session_id == self.voice_session_id:
                self.voice_processing = False
        self.publish_voice_status()
        return {"ok": True, "recording": False, "recording_file": recording.to_dict(), "transcript": transcript}

    def handle_voice_reply_text(self, text: str, reply_behavior: str, session_id: int) -> None:
        reply_text = f"首长，刚才识别到：{text}"
        with self.lock:
            still_current = session_id == self.voice_session_id
            phone_off_hook = self.last_state == "RELEASED"
            callback_enabled = self.config.enable_callback

        if reply_behavior == "direct":
            if not still_current or not phone_off_hook:
                self.add_action_log("语音回复已丢弃：电话已挂机或会话已取消。")
                return
            self.enqueue_reply("voice-asr", reply_text, title="语音识别回报")
            with self.lock:
                self.callback_session_active = True
            self.start_reply_playback("语音会话直接回报")
            return

        if reply_behavior == "none":
            return

        self.enqueue_reply("voice-asr", reply_text, title="语音识别回报")
        with self.lock:
            should_alert = self.last_state == "PRESSED" and callback_enabled
        if should_alert:
            self.start_operator_alert("voice-asr")

    def start_agent_voice_session(self, reason: str = "摘机通话") -> dict[str, Any]:
        with self.lock:
            if normalize_business_mode(self.config.business_mode) != "doubao":
                return {"ok": False, "error": "not in doubao mode"}
            if self.last_state != "RELEASED":
                return {"ok": False, "error": "phone is on-hook"}
            if self.active_reply is not None or (self.playback_thread is not None and self.playback_thread.is_alive()):
                return {"ok": False, "error": "reply playback is active"}

        result = self.start_voice_recording(reason)
        if not result.get("ok"):
            return result
        if result.get("already_active"):
            return result

        session_id = int(result.get("session_id", 0))
        thread = threading.Thread(target=self.voice_turn_monitor_worker, args=(session_id,), daemon=True)
        with self.lock:
            self.voice_monitor_thread = thread
        thread.start()
        return result

    def voice_turn_monitor_worker(self, session_id: int) -> None:
        while True:
            time.sleep(0.12)
            with self.lock:
                still_current = session_id == self.voice_session_id
                phone_off_hook = self.last_state == "RELEASED"
                processing = self.voice_processing
            if not still_current or not phone_off_hook or processing:
                return
            if self.recorder is None or not self.recorder.is_recording():
                return

            activity = self.recorder.voice_activity()
            duration = activity["duration_seconds"]
            silence = activity["silence_seconds"]
            has_voice = activity["peak_level"] >= VOICE_LEVEL_THRESHOLD
            if has_voice and duration >= VOICE_TURN_MAX_SECONDS:
                self.stop_voice_recording("达到最长语音轮次，自动提交", reply_behavior="direct", session_id=session_id)
                return
            if has_voice and duration >= VOICE_TURN_MIN_SECONDS and silence >= VOICE_TURN_SILENCE_SECONDS:
                self.stop_voice_recording("静音自动提交", reply_behavior="direct", session_id=session_id)
                return

    def cancel_agent_voice_session(self, reason: str = "电话挂机") -> None:
        with self.lock:
            self.voice_session_id += 1
            self.voice_cancel_reason = reason
            self.voice_processing = False
            self.voice_recording = False
            self.callback_session_active = False

        if self.recorder is not None and self.recorder.is_recording():
            self.recorder.cancel()
            self.add_action_log(f"语音会话已取消：{reason}")

        self.stop_reply_playback(reason, wait_seconds=0.8)
        self.clear_voice_replies(reason)
        self.clear_ai_alert(reason)
        self.publish_voice_status()

    def clear_voice_replies(self, reason: str) -> None:
        removed = 0
        with self.lock:
            kept: deque[ReplyTask] = deque()
            for reply in self.reply_queue:
                if reply.source == "voice-asr":
                    reply.status = "cleared"
                    reply.finished_at = time.time()
                    reply.error = reason
                    self.completed_replies.append(reply)
                    removed += 1
                else:
                    kept.append(reply)
            self.reply_queue = kept
            self.pending_report_text = self.next_reply_text_locked()
        if removed:
            self.add_action_log(f"语音回话已清除：{reason}（{removed} 条）")
            self.publish_reply_status()

    def enqueue_reply(self, source: str, text: str, title: str | None = None, audio_path: str | None = None) -> ReplyTask:
        source = (source or "ai").strip() or "ai"
        clean_text = compact_hook_text(text) or "任务已经完成，通讯员等待向首长回报。"
        with self.lock:
            self.reply_counter += 1
            reply = ReplyTask(
                id=f"reply-{int(time.time())}-{self.reply_counter}",
                source=source,
                title=title or f"{source} 回话",
                text=clean_text,
                audio_path=audio_path,
            )
            self.reply_queue.append(reply)
            self.pending_report_text = self.next_reply_text_locked()

        self.add_action_log(f"回话已入队：{reply.title}（队列 {self.reply_status()['queue_size']} 条）")
        self.publish_reply_status()
        return reply

    def clear_reply_queue(self, reason: str = "manual") -> None:
        self.stop_reply_playback(f"{reason} 清空回话队列", wait_seconds=0.8)
        with self.lock:
            for reply in self.reply_queue:
                reply.status = "cleared"
                reply.finished_at = time.time()
                self.completed_replies.append(reply)
            self.reply_queue.clear()
            if self.active_reply is not None:
                self.active_reply.status = "cleared"
                self.active_reply.finished_at = time.time()
                self.completed_replies.append(self.active_reply)
                self.active_reply = None
            self.pending_report_text = None
        self.clear_ai_alert(reason)
        self.add_action_log(f"回话队列已清空：{reason}")
        self.publish_reply_status()

    def start_reply_playback(self, reason: str = "manual") -> bool:
        with self.lock:
            if self.playback_thread is not None and self.playback_thread.is_alive():
                return True
            if self.active_reply is None and not self.reply_queue:
                return False
            if self.last_state != "RELEASED":
                return False
            stop_event = threading.Event()
            thread = threading.Thread(target=self.reply_playback_worker, args=(stop_event,), daemon=True)
            self.playback_stop_event = stop_event
            self.playback_thread = thread

        self.add_action_log(f"回话播放已启动：{reason}")
        thread.start()
        self.publish_reply_status()
        return True

    def stop_reply_playback(self, reason: str = "manual", wait_seconds: float = 0.0) -> bool:
        with self.lock:
            stop_event = self.playback_stop_event
            thread = self.playback_thread
            process = self.playback_process
            had_playback = self.active_reply is not None or (thread is not None and thread.is_alive())

        stop_event.set()
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass
        if (
            wait_seconds > 0
            and thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=wait_seconds)
        if had_playback:
            self.add_action_log(f"回话播放已停止：{reason}")
        self.publish_reply_status()
        return had_playback

    def start_doubao_tts_process(self, text: str, rate: int, volume: int) -> subprocess.Popen[bytes] | None:
        if self.speech is None or not self.speech.is_tts_ready() or sys.platform != "win32":
            return None

        output_path = ROOT / "data" / "tts" / f"reply-{int(time.time())}-{uuid.uuid4().hex}.wav"
        speed_ratio = max(0.5, min(1.5, 1.0 + rate * 0.05))
        volume_ratio = max(0.0, min(1.0, volume / 100))
        try:
            self.speech.synthesize_to_file(
                text,
                output_path,
                speed_ratio=speed_ratio,
                volume_ratio=volume_ratio,
                pitch_ratio=1.0,
            )
        except VolcengineSpeechError as exc:
            self.add_action_log(f"豆包 TTS 2.0 未完成，回退到本地 TTS：{exc}")
            return None
        except Exception as exc:
            self.add_action_log(f"豆包 TTS 2.0 异常，回退到本地 TTS：{exc}")
            return None

        self.add_action_log(f"豆包 TTS 2.0 音频已生成：{output_path.name}")
        return self.start_audio_file_process(output_path)

    def start_audio_file_process(self, audio_path: Path) -> subprocess.Popen[bytes] | None:
        if sys.platform != "win32":
            return None
        script = (
            "& { param($audioPath) "
            "Add-Type -AssemblyName System;"
            "$p=New-Object System.Media.SoundPlayer($audioPath);"
            "$p.PlaySync()"
            " }"
        )
        startupinfo = None
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            return subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                    str(audio_path),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
            )
        except OSError as exc:
            self.add_action_log(f"音频播放启动失败，改为模拟播放：{exc}")
            return None

    def start_windows_tts_process(self, text: str, rate: int, volume: int) -> subprocess.Popen[bytes] | None:
        if sys.platform != "win32":
            return None

        encoded_text = base64.b64encode(text.encode("utf-8")).decode("ascii")
        script = (
            "& { param($encodedText,$rate,$volume) "
            "$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encodedText));"
            "Add-Type -AssemblyName System.Speech;"
            "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
            "$s.Rate=[int]$rate;"
            "$s.Volume=[int]$volume;"
            "$s.SetOutputToDefaultAudioDevice();"
            "$s.Speak($t)"
            " }"
        )
        startupinfo = None
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            return subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                    encoded_text,
                    str(rate),
                    str(volume),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
            )
        except OSError as exc:
            self.add_action_log(f"Windows TTS 启动失败，改为模拟播放：{exc}")
            return None

    def start_tts_process(self, text: str) -> subprocess.Popen[bytes] | None:
        with self.lock:
            enabled = self.config.enable_tts_playback
            rate = max(-10, min(10, int(self.config.tts_rate)))
            volume = max(0, min(100, int(self.config.tts_volume)))

        if not enabled:
            return None

        return self.start_doubao_tts_process(text, rate, volume) or self.start_windows_tts_process(text, rate, volume)

    def wait_for_reply_audio(self, reply: ReplyTask, stop_event: threading.Event) -> tuple[bool, str | None]:
        process = self.start_tts_process(reply.text)
        with self.lock:
            self.playback_process = process

        if process is not None:
            while process.poll() is None:
                if stop_event.wait(0.1):
                    try:
                        process.terminate()
                    except OSError:
                        pass
                    return False, "stopped"
            if process.returncode == 0:
                return True, None
            return False, f"tts_exit_{process.returncode}"

        duration = max(
            SIMULATED_REPLY_MIN_SECONDS,
            min(SIMULATED_REPLY_MAX_SECONDS, len(reply.text) / SIMULATED_REPLY_CHARS_PER_SECOND),
        )
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            if stop_event.wait(0.1):
                return False, "stopped"
        return True, None

    def reply_playback_worker(self, stop_event: threading.Event) -> None:
        try:
            while not stop_event.is_set():
                with self.lock:
                    if self.last_state != "RELEASED":
                        break
                    if self.active_reply is None:
                        if not self.reply_queue:
                            break
                        self.active_reply = self.reply_queue.popleft()
                    reply = self.active_reply
                    reply.status = "playing"
                    reply.started_at = time.time()
                    self.pending_report_text = reply.text

                self.add_action_log(f"通讯员开始回报：{reply.title}")
                self.publish_reply_status()
                ok, error = self.wait_for_reply_audio(reply, stop_event)

                with self.lock:
                    if self.active_reply is reply:
                        reply.finished_at = time.time()
                        reply.status = "done" if ok else ("stopped" if error == "stopped" else "failed")
                        reply.error = error
                        self.completed_replies.append(reply)
                        self.active_reply = None
                        self.playback_process = None
                        self.pending_report_text = self.next_reply_text_locked()
                        should_continue = ok and self.last_state == "RELEASED" and bool(self.reply_queue)
                    else:
                        should_continue = False

                self.publish_reply_status()
                if ok:
                    self.add_action_log(f"通讯员回报完成：{reply.title}")
                else:
                    self.add_action_log(f"通讯员回报中止：{reply.title}（{error or 'unknown'}）")
                    break
                if not should_continue:
                    break
        finally:
            with self.lock:
                if self.playback_thread is threading.current_thread():
                    self.playback_thread = None
                self.playback_process = None
                has_waiting = bool(self.reply_queue)
                phone_on_hook = self.last_state == "PRESSED"
                phone_off_hook = self.last_state == "RELEASED"
                business_mode = normalize_business_mode(self.config.business_mode)
            self.publish_reply_status()
            if has_waiting and phone_on_hook:
                self.start_operator_alert("reply-queue")
            if (
                business_mode == "doubao"
                and phone_off_hook
                and not has_waiting
                and not stop_event.is_set()
            ):
                threading.Timer(
                    VOICE_RESTART_DELAY_SECONDS,
                    self.start_agent_voice_session,
                    args=("回报后继续通话",),
                ).start()

    def next_waiting_reply_text(self) -> str:
        with self.lock:
            if self.reply_queue:
                return self.reply_queue[0].text
        return "还有未播放的回话，通讯员等待接听。"

    def run_configured_shortcut_for_state(self, state: str) -> bool:
        with self.lock:
            if not self.config.enable_actions:
                return False
            action_text = self.config.press_action_text if state == "PRESSED" else self.config.release_action_text
            action_label = "挂机动作" if state == "PRESSED" else "摘机动作"
            business_mode = normalize_business_mode(self.config.business_mode)

        if business_mode != "codex":
            return False

        try:
            steps = parse_action_text(action_text)
        except ValueError as exc:
            self.add_action_log(f"{action_label}快捷键解析失败：{exc}")
            return False
        if not steps:
            return False

        thread = threading.Thread(target=self.run_shortcut_steps, args=(action_label, steps), daemon=True)
        thread.start()
        return True

    def run_shortcut_steps(self, action_label: str, steps: list[dict[str, Any]]) -> None:
        with self.action_lock:
            self.add_action_log(f"执行{action_label}快捷键：{canonical_steps_label(steps)}")
            self.sender.send_steps(steps)

    def set_simulation_enabled(self, enabled: bool) -> dict[str, Any]:
        with self.lock:
            changed = self.simulation_enabled != enabled
            self.simulation_enabled = enabled
            if enabled and self.sim_started_at is None:
                self.sim_started_at = time.monotonic()
                self.sim_last_pulse_at = time.monotonic()
            if not enabled:
                self.sim_started_at = None
            hook_state = self.sim_hook_state

        if changed:
            self.add_state_log("模拟发送端已启用，页面将使用本机生成的稳定 GPIO/Wi-Fi 信号。" if enabled else "模拟发送端已停用。")
        if enabled:
            self.emit_simulated_sample(hook_state, "模拟发送端启用")
        self.publish_simulation_status()
        return self.simulation_status()

    def simulated_digital_for_state(self, state: str) -> str:
        pressed_level = hook_pressed_level(self.config)
        released_level = "LOW" if pressed_level == "HIGH" else "HIGH"
        return pressed_level if state == "PRESSED" else released_level

    def emit_simulated_sample(self, state: str | None = None, reason: str = "manual", *, log_raw: bool = True) -> dict[str, Any] | None:
        with self.lock:
            if not self.simulation_enabled:
                return self.current_sample
            next_state = (state or self.sim_hook_state or "PRESSED").upper()
            if next_state not in {"PRESSED", "RELEASED"}:
                next_state = "PRESSED"
            self.sim_hook_state = next_state
            digital = self.simulated_digital_for_state(next_state)
            buzzer_on = self.sim_buzzer_on
            led_on = self.sim_led_on
            hook_pin = self.sim_hook_pin
            led_pin = self.sim_led_pin
            wifi_ip = self.sim_wifi_ip
            wifi_rssi = self.sim_wifi_rssi

        payload_for_log = {
            "type": "simulation",
            "reason": reason,
            "digital": digital,
            "hook_state": next_state,
            "hook_pin": hook_pin,
            "buzzer": "ON" if buzzer_on else "OFF",
            "led": "ON" if led_on else "OFF",
            "wifi_ip": wifi_ip,
        }
        if log_raw:
            self.add_raw_log(f"<sim> {json.dumps(payload_for_log, ensure_ascii=False, separators=(',', ':'))}")
        sample = SensorSample(
            ms=int(time.monotonic() * 1000),
            adc=0 if digital == "LOW" else 4095,
            digital=digital,
            raw_line=f"SIM {reason}",
            firmware_state=next_state,
            pin=hook_pin,
            hook="OFF_HOOK" if digital == "LOW" else "ON_HOOK",
            buzzer="ON" if buzzer_on else "OFF",
            led="ON" if led_on else "OFF",
            led_pin=led_pin,
            wifi_connected=True,
            wifi_ip=wifi_ip,
            wifi_rssi=wifi_rssi,
            wifi_status=3,
            wifi_disconnect_reason=0,
            adc_synthetic=True,
        )
        self.handle_sample(sample)
        self.publish_simulation_status()
        with self.lock:
            return self.current_sample

    def send_udp_command(self, command: str) -> bool:
        payload = (command.strip() + "\n").encode("utf-8")
        with self.udp_lock:
            sock = self.udp_socket
            target = self.udp_device_address
        if sock is None or target is None:
            return False

        try:
            sock.sendto(payload, target)
        except OSError as exc:
            self.add_state_log(f"UDP command failed: {exc}")
            return False

        self.add_raw_log(f">udp {target[0]}:{target[1]} {command.strip()}")
        return True

    def send_device_command(self, command: str) -> bool:
        if self.send_udp_command(command):
            return True
        return self.send_serial_command(command)

    def send_serial_command(self, command: str) -> bool:
        with self.serial_lock:
            if self.serial_handle is None:
                self.add_state_log("串口尚未连接，无法写入板子配置。")
                return False

            try:
                self.serial_handle.write((command.strip() + "\n").encode("utf-8"))
                self.serial_handle.flush()
            except Exception as exc:
                self.add_state_log(f"写入板子失败：{exc}")
                return False

        self.add_raw_log(f"> {command.strip()}")
        return True

    def handle_hook_transition(self, previous_state: str, state: str) -> None:
        del previous_state
        if state == "RELEASED":
            with self.lock:
                has_callback = self.alerting or self.active_reply is not None or bool(self.reply_queue)
                alerting = self.alerting
                business_mode = normalize_business_mode(self.config.business_mode)
            if alerting:
                self.clear_ai_alert("摘机接听")
            if has_callback:
                with self.lock:
                    self.callback_session_active = True
                self.start_reply_playback("摘机接听")
                return
            if business_mode == "doubao":
                self.start_agent_voice_session("电话抬起")
                return
            self.run_configured_shortcut_for_state(state)
            return

        if state == "PRESSED":
            with self.lock:
                playing = self.active_reply is not None
                callback_session_active = self.callback_session_active
                self.callback_session_active = False
                business_mode = normalize_business_mode(self.config.business_mode)
            if business_mode == "doubao":
                self.cancel_agent_voice_session("电话挂机")
                return
            if playing or callback_session_active:
                self.stop_reply_playback("挂机停止播放", wait_seconds=0.8)
                return
            self.run_configured_shortcut_for_state(state)

    def add_sample(self, sample: SensorSample) -> None:
        state = self.interpreted_state_for_sample(sample)
        previous_state = self.last_state
        if state:
            self.last_state = state
        if state and state != previous_state:
            source_label = "模拟信号" if sample.raw_line.startswith("SIM ") else "设备信号"
            self.add_state_log(f"{source_label}判定：{hook_state_label(previous_state)} -> {hook_state_label(state)}")
            self.handle_hook_transition(previous_state, state)

        scheme = normalize_hook_scheme(self.config.hook_scheme)
        business_mode = normalize_business_mode(self.config.business_mode)
        with self.lock:
            alerting = self.alerting
            alert_phase = self.alert_phase
            alert_started_at = self.alert_started_at
            alert_elapsed_seconds = int(time.monotonic() - alert_started_at) if alert_started_at else 0
            pending_report_text = self.pending_report_text
        payload = {
            "ms": sample.ms,
            "adc": sample.adc,
            "digital": sample.digital,
            "digital_value": 0 if sample.digital == "LOW" else 1,
            "firmware_state": sample.firmware_state,
            "python_state": state,
            "hook_scheme": scheme,
            "hook_scheme_label": HOOK_SCHEMES[scheme]["label"],
            "hook_scheme_description": HOOK_SCHEMES[scheme]["description"],
            "business_mode": business_mode,
            "business_mode_label": BUSINESS_MODES[business_mode]["label"],
            "pressed_level": hook_pressed_level(self.config),
            "hook_label": hook_state_label(state),
            "alerting": alerting,
            "alert_phase": alert_phase,
            "alert_elapsed_seconds": alert_elapsed_seconds,
            "pending_report_text": pending_report_text,
            "sample_source": "simulation" if sample.raw_line.startswith("SIM ") else "device",
            "score": sample.score,
            "pin": sample.pin,
            "hook": sample.hook,
            "buzzer": sample.buzzer,
            "led": sample.led,
            "led_pin": sample.led_pin,
            "wifi_connected": sample.wifi_connected,
            "wifi_ip": sample.wifi_ip,
            "wifi_rssi": sample.wifi_rssi,
            "wifi_status": sample.wifi_status,
            "wifi_disconnect_reason": sample.wifi_disconnect_reason,
            "adc_synthetic": sample.adc_synthetic,
        }
        with self.lock:
            self.current_sample = payload
            self.samples.append(payload)
        self.publish({"type": "sample", "sample": payload})

    def set_hook_scheme(self, scheme: str) -> ConsoleConfig:
        scheme = normalize_hook_scheme(scheme)
        with self.lock:
            self.config.hook_scheme = scheme
            save_config(self.config_path, self.config)
            current_sample = self.current_sample
            if current_sample is not None and current_sample.get("digital") is not None:
                state = interpret_hook_state(current_sample["digital"], self.config)
                current_sample["python_state"] = state
                current_sample["hook_scheme"] = scheme
                current_sample["hook_scheme_label"] = HOOK_SCHEMES[scheme]["label"]
                current_sample["hook_scheme_description"] = HOOK_SCHEMES[scheme]["description"]
                current_sample["pressed_level"] = hook_pressed_level(self.config)
                current_sample["hook_label"] = hook_state_label(state)
                self.last_state = state
        self.add_state_log(f"已切换开关判定：{HOOK_SCHEMES[scheme]['label']}（{HOOK_SCHEMES[scheme]['description']}）")
        self.publish({"type": "config", "config": self.config.to_dict()})
        if current_sample is not None:
            self.publish({"type": "sample", "sample": current_sample})
        return self.config

    def set_business_mode(self, mode: str) -> ConsoleConfig:
        mode = normalize_business_mode(mode)
        with self.lock:
            self.config.business_mode = mode
            save_config(self.config_path, self.config)
            current_sample = self.current_sample
            if current_sample is not None:
                current_sample["business_mode"] = mode
                current_sample["business_mode_label"] = BUSINESS_MODES[mode]["label"]
        self.add_state_log(f"已切换业务模式：{BUSINESS_MODES[mode]['label']}")
        self.publish({"type": "config", "config": self.config.to_dict()})
        if current_sample is not None:
            self.publish({"type": "sample", "sample": current_sample})
        return self.config

    def update_config(self, config: ConsoleConfig) -> None:
        save_config(self.config_path, config)
        with self.lock:
            self.config = config
            simulation_enabled = self.simulation_enabled
            sim_state = self.sim_hook_state
        if self.is_serial_connected() and self.send_serial_command(build_device_config_command(config)):
            self.add_state_log("配置已保存到电脑，并已发送给 ESP32 写入板子。")
        elif simulation_enabled:
            self.add_state_log("配置已保存到电脑，模拟发送端已按当前配置刷新样本。")
            self.emit_simulated_sample(sim_state, "配置同步")
        else:
            self.add_state_log("配置已保存到电脑，但没有成功写入 ESP32。")
        self.publish({"type": "config", "config": config.to_dict()})

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "config": self.config.to_dict(),
                "raw_logs": list(self.raw_logs),
                "state_logs": list(self.state_logs),
                "action_logs": list(self.action_logs),
                "samples": list(self.samples),
                "current_sample": self.current_sample,
                "state": self.machine.stable_state,
                "serial_connected": self.is_serial_connected(),
                "serial_port": self.current_serial_port(),
                "udp_device": self.current_udp_device(),
                "alerting": self.alerting,
                "alert_phase": self.alert_phase,
                "alert_elapsed_seconds": int(time.monotonic() - self.alert_started_at) if self.alert_started_at else 0,
                "pending_report_text": self.pending_report_text,
                **self.voice_status(),
                **self.reply_status(),
                **self.simulation_status(),
            }

    def handle_sample(self, sample: SensorSample) -> None:
        self.add_sample(sample)

    def run_action_for_state(self, state: str) -> bool:
        command_type = "simulate_press" if state == "PRESSED" else "simulate_release"
        with self.lock:
            simulation_enabled = self.simulation_enabled
        if simulation_enabled:
            self.add_state_log(f"手动模拟{'按下' if state == 'PRESSED' else '抬起'}。")
            self.emit_simulated_sample(state, command_type)
            if state == "RELEASED":
                with self.lock:
                    alerting = self.alerting
                if alerting:
                    self.clear_ai_alert("模拟摘机")
            return True

        command = json.dumps({"type": command_type}, separators=(",", ":"))
        if self.send_serial_command(command):
            self.add_action_log(f"已发送板子模拟命令：{command_type}")
            return True
        return False

    def has_hardware_link(self) -> bool:
        with self.udp_lock:
            udp_ready = self.udp_socket is not None and self.udp_device_address is not None
        return udp_ready or self.is_serial_connected()

    def run_hardware_command(self, command: str, *, log: bool = True) -> bool:
        if self.has_hardware_link() and self.send_device_command(command):
            if log:
                self.add_action_log(f"硬件测试命令：{command}")
            return True
        with self.lock:
            simulation_enabled = self.simulation_enabled
        if simulation_enabled:
            return self.apply_simulated_hardware_command(command, log=log)
        return False

    def apply_simulated_hardware_command(self, command: str, *, log: bool = True) -> bool:
        normalized = command.strip().lower()
        if normalized.startswith("{"):
            try:
                payload = json.loads(normalized)
                normalized = str(payload.get("type", normalized)).strip().lower()
            except json.JSONDecodeError:
                pass

        beep_duration_seconds: float | None = None
        with self.lock:
            if normalized in {"beep", "ring", "ring_once"}:
                self.sim_buzzer_on = True
                self.sim_led_on = True
                beep_duration_seconds = 0.6
            elif normalized in {"ring_on", "buzzer_on"}:
                self.sim_buzzer_on = True
                self.sim_led_on = True
            elif normalized in {"ring_off", "buzzer_off"}:
                self.sim_buzzer_on = False
                self.sim_led_on = False
            elif normalized == "led_on":
                self.sim_led_on = True
            elif normalized == "led_off":
                self.sim_led_on = False
            elif normalized == "ping":
                pass
            else:
                return False

        if log:
            self.add_action_log(f"模拟硬件命令：{normalized}")
        self.emit_simulated_sample(reason=f"模拟硬件 {normalized}")

        if beep_duration_seconds is not None:
            timer = threading.Timer(beep_duration_seconds, self.apply_simulated_hardware_command, args=("ring_off",), kwargs={"log": False})
            timer.daemon = True
            timer.start()
        return True

    def publish_alert_status(self) -> None:
        with self.lock:
            alert_started_at = self.alert_started_at
            payload = {
                "type": "alert_status",
                "alerting": self.alerting,
                "alert_phase": self.alert_phase,
                "alert_elapsed_seconds": int(time.monotonic() - alert_started_at) if alert_started_at else 0,
                "pending_report_text": self.pending_report_text,
            }
        self.publish(payload)

    def set_alert_phase(self, phase: str, *, alerting: bool | None = None) -> None:
        with self.lock:
            if alerting is not None:
                self.alerting = alerting
            self.alert_phase = phase
        self.publish_alert_status()

    def stop_alert_thread(self, wait_seconds: float = 0.0) -> None:
        with self.lock:
            stop_event = self.alert_stop_event
            alert_thread = self.alert_thread
        stop_event.set()
        if (
            wait_seconds > 0
            and alert_thread is not None
            and alert_thread.is_alive()
            and alert_thread is not threading.current_thread()
        ):
            alert_thread.join(timeout=wait_seconds)

    def operator_alert_worker(self, stop_event: threading.Event) -> None:
        self.add_action_log("接线员模式已启动：蜂鸣器和 LED 同步 1 秒响/亮、4 秒停/灭；摘机后停止，90 秒无人接听后切忙音。")
        deadline = time.monotonic() + OPERATOR_RING_TIMEOUT_SECONDS

        while not stop_event.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            self.set_alert_phase("ring", alerting=True)
            self.run_hardware_command("ring_on", log=False)
            if stop_event.wait(min(OPERATOR_RING_ON_SECONDS, remaining)):
                break

            self.run_hardware_command("ring_off", log=False)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            self.set_alert_phase("pause", alerting=True)
            if stop_event.wait(min(OPERATOR_RING_OFF_SECONDS, remaining)):
                break

        if stop_event.is_set():
            self.run_hardware_command("ring_off", log=False)
            self.run_hardware_command("led_off", log=False)
            return

        self.run_hardware_command("ring_off", log=False)
        self.set_alert_phase("busy", alerting=True)
        self.add_action_log("接线员模式久叫无人接听，已切换忙音；摘机或手动停止后关闭。")

        while not stop_event.is_set():
            self.run_hardware_command("ring_on", log=False)
            if stop_event.wait(OPERATOR_BUSY_ON_SECONDS):
                break
            self.run_hardware_command("ring_off", log=False)
            if stop_event.wait(OPERATOR_BUSY_OFF_SECONDS):
                break

        self.run_hardware_command("ring_off", log=False)
        self.run_hardware_command("led_off", log=False)

    def clear_ai_alert(self, reason: str = "manual") -> bool:
        with self.lock:
            was_alerting = self.alerting or self.alert_phase != "idle"
            self.alerting = False
            self.alert_phase = "idle"
            self.alert_started_at = None
            self.alert_last_hook_state = None
            self.pending_report_text = self.next_reply_text_locked()
            stop_event = self.alert_stop_event
        stop_event.set()
        self.publish_alert_status()
        buzzer_ok = self.run_hardware_command("ring_off")
        led_ok = self.run_hardware_command("led_off")
        self.add_action_log(f"接线员提醒已停止：{reason}")
        return was_alerting or buzzer_ok or led_ok

    def start_operator_alert(self, source: str = "ai") -> bool:
        source = (source or "ai").strip() or "ai"
        with self.lock:
            simulation_enabled = self.simulation_enabled
        if not self.has_hardware_link() and not simulation_enabled:
            self.add_action_log(f"接线员 hook 触发失败：没有可用的 ESP32 Wi-Fi、串口或模拟链路（{source}）。")
            return False

        self.stop_alert_thread(wait_seconds=0.8)
        stop_event = threading.Event()
        alert_thread = threading.Thread(target=self.operator_alert_worker, args=(stop_event,), daemon=True)
        with self.lock:
            self.alert_stop_event = stop_event
            self.alert_thread = alert_thread
            self.alerting = True
            self.alert_phase = "ring"
            self.alert_started_at = time.monotonic()
            self.alert_last_hook_state = self.last_state
            self.pending_report_text = self.next_reply_text_locked()
        self.publish_alert_status()
        alert_thread.start()
        self.add_action_log(f"接线员 hook 已收到：{source}，开始 1 秒响/亮、4 秒停/灭。")
        return True

    def run_ai_hook_signal(self, source: str = "ai", text: str | None = None) -> bool:
        source = (source or "ai").strip() or "ai"
        self.enqueue_reply(source, text or "任务已经完成，通讯员等待向首长回报。")
        with self.lock:
            already_off_hook = self.last_state == "RELEASED"
            callback_enabled = self.config.enable_callback
        if already_off_hook:
            with self.lock:
                self.callback_session_active = True
            return self.start_reply_playback(f"{source} hook")
        if not callback_enabled:
            self.add_action_log(f"回话已入队但未呼叫：回话开关已关闭（{source}）。")
            return True
        return self.start_operator_alert(source)

    def set_test_pins(self, hook_pin: int, buzzer_pin: int, led_pin: int = 20) -> bool:
        command = json.dumps(
            {"type": "set_pins", "hook_pin": hook_pin, "buzzer_pin": buzzer_pin, "led_pin": led_pin},
            separators=(",", ":"),
        )
        if self.has_hardware_link() and self.send_device_command(command):
            self.add_action_log(f"测试引脚已发送：开关 GPIO{hook_pin}，蜂鸣器 GPIO{buzzer_pin}，LED GPIO{led_pin}")
            return True
        with self.lock:
            simulation_enabled = self.simulation_enabled
            if simulation_enabled:
                self.sim_hook_pin = hook_pin
                self.sim_buzzer_pin = buzzer_pin
                self.sim_led_pin = led_pin
        if simulation_enabled:
            self.add_action_log(f"模拟测试引脚已应用：开关 GPIO{hook_pin}，蜂鸣器 GPIO{buzzer_pin}，LED GPIO{led_pin}")
            self.emit_simulated_sample(reason="模拟引脚配置")
            return True
        return False


def timestamped(text: str) -> str:
    return f"{time.strftime('%H:%M:%S')} {text}"


def normalize_port_name(port: str | None) -> str | None:
    value = (port or "").strip()
    return value or None


def describe_port(port: Any) -> str:
    description = getattr(port, "description", "") or getattr(port, "name", "") or "串口"
    return f"{port.device} ({description})"


def is_system_serial_port(port: Any) -> bool:
    identity = " ".join(
        str(getattr(port, attr, "") or "")
        for attr in ("description", "manufacturer", "hwid")
    ).upper()
    return port.device.upper() == "COM1" and ("ACPI" in identity or "PNP0501" in identity)


def is_usb_serial_candidate(port: Any) -> bool:
    if getattr(port, "vid", None) == 0x303A and getattr(port, "pid", None) == 0x1001:
        return True

    identity = " ".join(
        str(getattr(port, attr, "") or "")
        for attr in ("description", "manufacturer", "hwid")
    ).upper()
    return not is_system_serial_port(port) and ("USB" in identity or getattr(port, "vid", None) is not None)


def visible_serial_ports() -> list[Any]:
    if list_ports is None:
        return []

    return list(list_ports.comports())


def describe_visible_ports(ports: list[Any]) -> str:
    if not ports:
        return "无"

    descriptions = []
    for port in ports:
        label = describe_port(port)
        if is_system_serial_port(port):
            label += "，系统内置通信端口，已忽略"
        descriptions.append(label)
    return "；".join(descriptions)


def serial_port_candidates(preferred_port: str | None = None) -> tuple[list[str], str]:
    ports = visible_serial_ports()
    candidates: list[str] = []

    def add_candidate(device: str | None) -> None:
        if device and device not in candidates:
            candidates.append(device)

    preferred = normalize_port_name(preferred_port)
    if preferred:
        for port in ports:
            if port.device.upper() == preferred.upper() and is_usb_serial_candidate(port):
                add_candidate(port.device)
                break

    for port in ports:
        if getattr(port, "vid", None) == 0x303A and getattr(port, "pid", None) == 0x1001:
            add_candidate(port.device)

    for port in ports:
        if is_usb_serial_candidate(port):
            add_candidate(port.device)

    return candidates, describe_visible_ports(ports)


def find_default_port() -> str | None:
    candidates, _visible_ports = serial_port_candidates()
    return candidates[0] if candidates else None


def handle_board_line(app: AppState, line: str) -> bool:
    sample = parse_serial_line(line)
    if sample is not None:
        app.handle_sample(sample)
        return True

    if not line.startswith("{"):
        return False

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False

    event_type = payload.get("type")
    if event_type == "event":
        state = payload.get("state", "?")
        action = payload.get("action", "")
        adc = payload.get("adc", "?")
        score = payload.get("score", "?")
        app.last_state = state
        app.add_state_log(f"板子事件：{payload.get('event')} state={state} adc={adc} score={score}")
        if action:
            app.add_action_log(f"板子执行动作：{action}")
    elif event_type == "config_saved":
        app.add_state_log("ESP32 已确认配置写入板子。")
    elif event_type == "config":
        app.add_state_log("ESP32 已回传当前板载配置。")
    elif event_type == "ble":
        app.add_state_log(f"BLE 状态：{payload.get('state')}")
    elif event_type == "buzzer":
        app.add_action_log(
            f"蜂鸣器：{payload.get('state')} pin={payload.get('pin')} freq={payload.get('freq_hz', '-')}"
        )
    elif event_type == "led":
        app.add_action_log(f"LED：{payload.get('state')} pin={payload.get('pin')}")
    elif event_type == "pins":
        app.add_state_log(
            f"测试固件引脚：开关 GPIO{payload.get('hook_pin')}，蜂鸣器 GPIO{payload.get('buzzer_pin')}，LED GPIO{payload.get('led_pin')}"
        )
    elif event_type == "error":
        app.add_state_log(f"ESP32 错误：{payload.get('message')}")
    elif event_type == "hello":
        app.add_state_log(f"ESP32 固件：{payload.get('version') or payload.get('fw')}")

    return True


def serial_worker(app: AppState, preferred_port: str | None, baud: int, stop: threading.Event) -> None:
    if serial is None:
        app.add_state_log("缺少 pyserial，无法读取串口。")
        return

    preferred_port = normalize_port_name(preferred_port)
    last_wait_message = ""
    last_wait_log_at = 0.0
    last_failure_by_port: dict[str, tuple[str, float]] = {}

    while not stop.is_set():
        candidates, visible_ports = serial_port_candidates(preferred_port)
        if not candidates:
            prefix = f"优先端口 {preferred_port} 当前不可见，" if preferred_port else ""
            message = f"{prefix}没有找到 ESP32 串口，正在等待 USB 重新枚举。当前可见端口：{visible_ports}。"
            now = time.monotonic()
            if message != last_wait_message or now - last_wait_log_at >= SERIAL_LOG_INTERVAL_SECONDS:
                app.add_state_log(message)
                last_wait_message = message
                last_wait_log_at = now
            stop.wait(SERIAL_SCAN_INTERVAL_SECONDS)
            continue

        last_wait_message = ""
        opened_port = False
        for port in candidates:
            if stop.is_set():
                break

            try:
                app.add_state_log(f"正在打开 {port}，波特率 {baud}。")
                with serial.Serial(port, baud, timeout=0.2, write_timeout=1.0) as ser:
                    opened_port = True
                    ser.dtr = False
                    ser.rts = False
                    initial_command = json.dumps({"type": "ping"}, separators=(",", ":"))
                    app.add_state_log(f"{port} 已打开，正在确认固件通信。")
                    try:
                        ser.write((initial_command + "\n").encode("utf-8"))
                        ser.flush()
                    except Exception as exc:
                        raise RuntimeError(f"初始配置读取命令写入失败：{exc}") from exc

                    app.attach_serial(ser, port)
                    last_failure_by_port.pop(port, None)
                    app.add_state_log(f"{port} 已连接，开始读取传感器日志。")
                    app.add_raw_log(f"> {initial_command}")
                    connected_at = time.monotonic()
                    saw_rom_banner = False
                    saw_firmware_data = False
                    reported_no_firmware_data = False

                    try:
                        while not stop.is_set():
                            raw = ser.readline()
                            if not raw:
                                if (
                                    not saw_firmware_data
                                    and not reported_no_firmware_data
                                    and time.monotonic() - connected_at >= FIRMWARE_DATA_WAIT_SECONDS
                                ):
                                    if saw_rom_banner:
                                        app.add_state_log(
                                            "串口只收到 ESP-ROM 启动信息，还没有收到 AI Desk Phone 固件数据；"
                                            "板子可能停在下载模式，检查 BOOT 是否被按住，或复位/重新烧录固件。"
                                        )
                                    else:
                                        app.add_state_log(
                                            "串口已打开，但还没有收到固件数据；如果页面没有 GPIO 数字波形，"
                                            "请复位板子或重新插拔 USB。"
                                        )
                                    reported_no_firmware_data = True
                                continue

                            line = raw.decode("utf-8", errors="replace").strip()
                            app.add_raw_log(line)
                            if line.startswith("ESP-ROM") or line.startswith("Build:"):
                                saw_rom_banner = True
                            if handle_board_line(app, line):
                                saw_firmware_data = True
                    finally:
                        app.detach_serial(ser)
            except Exception as exc:  # pragma: no cover - hardware/environment dependent
                message = f"{port} 串口读取失败：{exc}"
                now = time.monotonic()
                last_failure, last_failure_at = last_failure_by_port.get(port, ("", 0.0))
                if message != last_failure or now - last_failure_at >= SERIAL_LOG_INTERVAL_SECONDS:
                    app.add_state_log(message)
                    last_failure_by_port[port] = (message, now)

            if opened_port:
                break

        stop.wait(SERIAL_SCAN_INTERVAL_SECONDS)


def udp_worker(app: AppState, telemetry_port: int, command_port: int, stop: threading.Event) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(0.5)

    try:
        sock.bind(("", telemetry_port))
    except OSError as exc:
        app.add_state_log(f"UDP listen failed on port {telemetry_port}: {exc}")
        sock.close()
        return

    app.attach_udp_socket(sock)
    app.add_state_log(f"UDP listening on 0.0.0.0:{telemetry_port}; device commands will use port {command_port}.")

    try:
        while not stop.is_set():
            try:
                raw, address = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError as exc:
                if not stop.is_set():
                    app.add_state_log(f"UDP receive failed: {exc}")
                break

            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            app.update_udp_device(address[0], command_port)
            app.add_raw_log(f"<udp {address[0]}:{address[1]}> {line}")
            handle_board_line(app, line)
    finally:
        app.detach_udp_socket(sock)
        sock.close()


def simulation_worker(app: AppState, stop: threading.Event) -> None:
    while not stop.is_set():
        with app.lock:
            enabled = app.simulation_enabled
            auto_pulse = app.sim_auto_pulse_enabled
            current_state = app.sim_hook_state
            should_pulse = enabled and auto_pulse and (time.monotonic() - app.sim_last_pulse_at >= SIM_AUTO_PULSE_INTERVAL_SECONDS)

        if enabled and should_pulse:
            with app.lock:
                app.sim_last_pulse_at = time.monotonic()
            app.add_state_log("模拟发送端自动产生一次摘挂机脉冲。")
            app.emit_simulated_sample("PRESSED", "自动脉冲按下")
            if stop.wait(SIM_AUTO_PULSE_SECONDS):
                break
            with app.lock:
                still_enabled = app.simulation_enabled
            if still_enabled:
                app.emit_simulated_sample("RELEASED", "自动脉冲复位")
        elif enabled:
            app.emit_simulated_sample(current_state, "持续心跳", log_raw=False)

        stop.wait(SIM_SAMPLE_INTERVAL_SECONDS)


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>HG113 本地控制台</title>
  <style>
    :root {
      color-scheme: light;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
      --bg: #f5f6f8;
      --panel: #ffffff;
      --panel-soft: #f8fafc;
      --border: #d8dee8;
      --border-strong: #b8c0cc;
      --text: #17202e;
      --muted: #6b7280;
      --red: #8b1e1e;
      --red-dark: #661515;
      --red-soft: #f8eeee;
      --good: #18735c;
      --warn: #8b1e1e;
      --danger: #a32020;
      --dark: #111827;
    }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body { margin: 0; color: var(--text); font-size: 14px; background: var(--bg); }
    h1, h2, h3, p { margin-top: 0; }
    h1 { font-size: 22px; margin-bottom: 3px; letter-spacing: 0; line-height: 1.15; font-weight: 700; color: var(--text); }
    h2 { font-size: 16px; margin: 0; line-height: 1.35; font-weight: 700; }
    h3 { font-size: 14px; margin: 0 0 10px; line-height: 1.35; font-weight: 700; color: var(--text); }
    .masthead { background: var(--panel); border-bottom: 1px solid var(--border); }
    .masthead-inner {
      max-width: 1180px; margin: 0 auto; min-height: 64px; display: flex;
      justify-content: space-between; gap: 16px; align-items: center; padding: 14px 4px;
    }
    .brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
    .subtitle {
      margin: 0; color: var(--muted); line-height: 1.2; font-size: 12px;
      word-spacing: 3px; font-family: "Segoe UI", Arial, sans-serif;
    }
    .primary-nav { background: var(--panel); border-bottom: 1px solid var(--border); }
    .nav-inner { max-width: 1180px; margin: 0 auto; display: grid; grid-template-columns: repeat(4, 1fr); }
    .nav-inner a {
      color: var(--muted); text-decoration: none; text-align: center; padding: 8px;
      border-left: 1px solid var(--border); font-weight: 600; min-height: 34px; font-size: 14px;
    }
    .nav-inner a:hover { background: var(--panel-soft); color: var(--text); }
    .nav-inner a:last-child { border-right: 1px solid var(--border); }
    .nav-inner a.active { color: var(--red); box-shadow: inset 0 -2px 0 var(--red); }
    .page-wrap { max-width: 1180px; margin: 16px auto 0; padding: 0 4px 22px; }
    .crumb {
      display: none;
    }
    main.portal-main { display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, 0.72fr); gap: 14px 18px; }
    .panel {
      background: var(--panel); border: 1px solid var(--border); border-radius: 4px;
      padding: 14px 16px; box-shadow: none;
    }
    .panel-header { display: flex; justify-content: space-between; gap: 16px; align-items: start; margin-bottom: 10px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
    .panel-header h2::before { content: ""; }
    .panel-note { display: none; }
    .service-status {
      display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 1px; background: var(--border);
      border: 1px solid var(--border); margin-bottom: 12px;
    }
    .status-item { background: var(--panel-soft); padding: 10px; white-space: nowrap; color: var(--muted); font-size: 13px; }
    .status-value { color: var(--text); font-weight: 700; }
    .status-value.good { color: var(--good); }
    .status-value.warn { color: var(--warn); }
    .monitor-switches { display: none; }
    .monitor-row { display: grid; grid-template-columns: minmax(0, 1fr) 48px; align-items: center; gap: 16px; font-size: 13px; color: #333942; }
    .switch-toggle { width: 48px; height: 22px; background: #cfcfcf; border: 1px solid #c8c8c8; position: relative; justify-self: end; }
    .switch-toggle::after { content: ""; width: 14px; height: 14px; background: #fff; border: 1px solid #e7e7e7; position: absolute; top: 3px; left: 4px; }
    .switch-toggle.active { background: var(--red-dark); border-color: var(--red-dark); }
    .switch-toggle.active::after { left: 28px; }
    .readout-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); margin-bottom: 10px; }
    .metric { padding: 10px 11px; min-height: 66px; background: var(--panel); }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 5px; line-height: 1.35; }
    .value { font-size: 20px; line-height: 1.15; font-weight: 800; word-break: break-word; font-variant-numeric: tabular-nums; }
    .state-pressed { color: var(--good); }
    .state-released { color: var(--warn); }
    .state-line { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); margin: 12px 0 0; }
    .state-line div { padding: 10px 12px; background: var(--panel-soft); }
    .state-line strong { display: block; margin-top: 4px; color: var(--text); font-size: 16px; }
    canvas { width: 100%; height: 150px; border: 1px solid var(--border); border-radius: 2px; background: #fff; display: block; }
    #digitalChart { height: 150px; margin-top: 10px; }
    .section-divider { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border); }
    .control-block { border: 1px solid var(--border); border-radius: 2px; padding: 12px; background: var(--panel); }
    .control-block + .control-block { margin-top: 12px; }
    .mode-row { display: grid; gap: 10px; margin-bottom: 10px; }
    .segmented { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid var(--border); border-radius: 3px; overflow: hidden; background: var(--panel); }
    .segmented button { border: 0; border-radius: 0; border-right: 1px solid var(--border); min-height: 38px; }
    .segmented button:last-child { border-right: 0; }
    .segmented button.active { background: var(--red); color: #fff; }
    .mode-hint, .callout { color: var(--muted); font-size: 12px; line-height: 1.5; min-height: 18px; }
    .callout { border-left: 3px solid var(--red); padding: 8px 10px; background: var(--red-soft); }
    .pin-grid, .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .pin-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .button-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }
    .simulation-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); margin: 10px 0; }
    .simulation-grid div { background: var(--panel); padding: 10px 11px; min-height: 62px; color: var(--muted); }
    .simulation-grid strong { display: block; margin-top: 5px; color: var(--text); font-size: 15px; }
    label { display: grid; gap: 6px; font-size: 13px; color: var(--text); }
    input, select, textarea {
      width: 100%; min-height: 34px; border: 1px solid var(--border); border-radius: 2px;
      padding: 6px 9px; font: inherit; background: #fff; color: var(--text);
    }
    textarea { resize: vertical; min-height: 72px; line-height: 1.45; }
    input[type="checkbox"] { width: auto; height: auto; }
    .form-wide { grid-column: 1 / -1; }
    .preset-row { display: grid; gap: 8px; }
    .preset-actions { display: flex; gap: 8px; flex-wrap: wrap; }
    .action-editor { grid-column: 1 / -1; border-top: 1px solid var(--border); padding-top: 12px; }
    .action-title { font-weight: 700; margin-bottom: 9px; }
    .action-row { display: grid; grid-template-columns: minmax(0, 1fr) 112px; gap: 10px; align-items: end; }
    .action-row label:nth-child(3) { grid-column: 1 / -1; }
    .capture {
      width: 100%; min-height: 34px; text-align: left; background: var(--panel-soft);
      display: flex; justify-content: space-between; align-items: center; gap: 10px;
    }
    .capture::after { content: "录入"; color: var(--muted); font-size: 12px; }
    .capture.active { border-color: var(--red); box-shadow: 0 0 0 2px rgba(139, 30, 30, 0.12); }
    .capture.active::after { content: "按键中"; color: var(--red); }
    .buttons { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 12px; }
    .save-status { margin-top: 0; min-height: 34px; display: inline-flex; align-items: center; }
    .save-status.ok { color: var(--good); }
    .save-status.warn { color: var(--warn); }
    button {
      border: 1px solid var(--border); border-radius: 3px; background: #fff; color: var(--text);
      padding: 7px 11px; cursor: pointer; font: inherit; font-weight: 700; min-height: 33px; transition: background-color 160ms ease, color 160ms ease, transform 120ms ease;
    }
    button:hover { background: var(--panel-soft); }
    button:active { transform: translateY(1px); }
    button:focus-visible { outline: 2px solid rgba(139, 30, 30, 0.28); outline-offset: 2px; }
    button.primary { background: var(--red); color: #fff; border-color: var(--red); box-shadow: none; }
    button.primary:hover { background: var(--red-dark); }
    button.danger { border-color: #e6b2ad; color: var(--danger); }
    details { border-top: 1px solid var(--border); padding-top: 10px; }
    details + details { margin-top: 12px; }
    summary { cursor: pointer; font-weight: 700; margin-bottom: 10px; }
    .logs { padding: 0; overflow: hidden; grid-column: 1 / -1; }
    .log-head { display:flex; justify-content:space-between; align-items:center; gap: 12px; padding: 14px 16px; border-bottom: 1px solid var(--border); }
    .log-grid { display: grid; grid-template-columns: 1.35fr 1fr 1fr; gap: 1px; background: var(--border); border-top: 1px solid var(--border); }
    pre {
      margin: 0; min-height: 250px; max-height: 360px; overflow: auto; padding: 12px;
      background: #fff; color: #253044; white-space: pre-wrap; font: 12px/1.45 Consolas, monospace;
    }
    pre::before {
      content: attr(data-title); display: block; margin: -2px 0 8px; padding-bottom: 7px;
      color: var(--red-dark); border-bottom: 1px solid var(--border); font: 700 13px/1.35 "Microsoft YaHei", "Segoe UI", sans-serif;
    }
    .hint { color: var(--muted); font-size: 12px; margin-top: 6px; }
    .muted { color: var(--muted); }
    .full-width { grid-column: 1 / -1; }
    .hidden-config { display: none; }
    .info-board { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 1px; background: var(--border); border: 1px solid var(--border); }
    .info-board div { background: #fff; padding: 12px; min-height: 74px; }
    .info-board strong { display: block; margin-bottom: 5px; color: var(--red-dark); }
    @media (max-width: 980px) {
      h1 { font-size: 26px; }
      .masthead-inner { grid-template-columns: 1fr; }
      .service-status { grid-template-columns: 1fr 1fr; }
      main.portal-main { grid-template-columns: 1fr; }
      .log-grid, .readout-grid, .state-line, .form-grid, .action-row, .pin-grid, .simulation-grid, .info-board { grid-template-columns: 1fr; }
      .form-wide { grid-column: auto; }
      .page-wrap, .masthead-inner { padding-left: 12px; padding-right: 12px; }
    }
    @media (max-width: 640px) {
      .nav-inner { grid-template-columns: repeat(2, 1fr); }
    }
  </style>
</head>
<body>
  <header class="masthead">
    <div class="masthead-inner">
      <div class="brand">
        <div>
          <h1>HG113 本地控制台</h1>
          <p class="subtitle">模拟信号 / 电话状态 / 日志</p>
        </div>
      </div>
    </div>
  </header>

  <nav class="primary-nav" aria-label="主导航">
    <div class="nav-inner">
      <a class="active" href="#overview">首页</a>
      <a href="#config">功能配置</a>
      <a href="#simulation">模拟测试</a>
      <a href="#logs">系统日志</a>
      <a href="/simulator">模拟台</a>
    </div>
  </nav>

  <div class="page-wrap">

    <main class="portal-main">
      <section id="overview" class="panel full-width">
        <div class="panel-header">
          <div>
            <h2>GPIO 波形</h2>
            <p class="panel-note">优先查看 GPIO 波形和电话状态，其余信息只作为辅助判断。</p>
          </div>
        </div>
        <canvas id="digitalChart" width="900" height="150"></canvas>
        <div class="readout-grid">
          <div class="metric"><div class="label">电话状态</div><div id="stateValue" class="value">--</div></div>
          <div class="metric"><div class="label">GPIO 电平</div><div id="digitalValue" class="value">--</div></div>
          <div class="metric"><div class="label">数据来源</div><div id="sourceStatus" class="status-value">等待设备</div></div>
          <div class="metric"><div class="label">最近样本</div><div id="lastSample" class="status-value">暂无数据</div></div>
        </div>
        <div class="service-status">
          <div class="status-item">服务<br><span id="conn" class="status-value">连接中</span></div>
          <div class="status-item">串口<br><span id="serialStatus" class="status-value warn">扫描中</span></div>
          <div class="status-item">设备<br><span id="deviceStatus" class="status-value">未发现</span></div>
          <div class="status-item">蜂鸣器<br><span id="buzzerState" class="status-value">未知</span></div>
          <div class="status-item">LED<br><span id="ledState" class="status-value">未知</span></div>
        </div>
        <div class="state-line">
          <div>接线员提醒<strong id="alertState">未触发</strong></div>
          <div>数字值<strong id="adcValue">--</strong></div>
          <div>链路状态<strong id="simulationState">等待中</strong></div>
        </div>
      </section>

      <section id="config" class="panel">
        <div class="panel-header">
          <div>
            <h2>功能配置</h2>
            <p class="panel-note">配置电话在任务提醒、语音聊天和摘挂机判定中的工作方式。</p>
          </div>
        </div>
        <input id="business_mode" type="hidden" value="codex">
        <div class="segmented" aria-label="业务模式">
          <button id="modeCodexBtn" type="button" onclick="selectBusinessMode('codex')">机器人提醒</button>
          <button id="modeDoubaoBtn" type="button" onclick="selectBusinessMode('doubao')">语音聊天</button>
        </div>
        <div id="businessModeHint" class="callout">任务结束时呼叫桌面电话；摘机后停止提醒。</div>
        <div class="button-row">
          <button class="primary" onclick="postAiHook()">触发接线员提醒</button>
          <button onclick="clearAiAlert()">停止提醒</button>
        </div>
      </section>

      <section id="simulation" class="panel">
        <div class="panel-header">
          <div>
            <h2>模拟发送端</h2>
            <p class="panel-note">模拟端会持续发送心跳；摘机和挂机由按钮手动触发。</p>
          </div>
        </div>
        <div class="simulation-grid">
          <div>虚拟 GPIO<strong id="simulationHook">--</strong></div>
          <div>运行时长<strong id="simulationUptime">--</strong></div>
          <div>虚拟 Wi-Fi<strong id="simulationWifi">--</strong></div>
          <div>虚拟输出<strong id="simulationOutput">--</strong></div>
        </div>
        <div class="button-row">
          <button class="primary" id="simPressedBtn" onclick="postSimulationState('PRESSED')">模拟按下</button>
          <button id="simReleasedBtn" onclick="postSimulationState('RELEASED')">模拟抬起</button>
          <button onclick="setSimulationEnabled(true)">启用模拟链路</button>
          <button onclick="setSimulationEnabled(false)">停用模拟链路</button>
        </div>
        <div id="simulationHint" class="hint">模拟端启用后，每秒刷新一次波形；使用按钮手动生成摘机或挂机信号。</div>
        <div id="saveStatus" class="hint save-status">就绪</div>
      </section>

      <div class="hidden-config" aria-hidden="true">
        <input id="hook_scheme" type="hidden" value="scheme1">
        <input id="hookPinInput" type="hidden" value="0">
        <input id="buzzerPinInput" type="hidden" value="21">
        <input id="ledPinInput" type="hidden" value="20">
        <input id="enable_actions" type="hidden" value="false">
        <input id="press_action_text" type="hidden">
        <input id="release_action_text" type="hidden">
        <input id="press_primary_hotkey" type="hidden">
        <input id="press_delay_ms" type="hidden" value="0">
        <input id="press_follow_hotkey" type="hidden">
        <input id="release_primary_hotkey" type="hidden">
        <input id="release_delay_ms" type="hidden" value="0">
        <input id="release_follow_hotkey" type="hidden">
        <button id="scheme1Btn" type="button"></button>
        <button id="scheme2Btn" type="button"></button>
        <button id="press_primary_hotkey_button" type="button"></button>
        <button id="press_follow_hotkey_button" type="button"></button>
        <button id="release_primary_hotkey_button" type="button"></button>
        <button id="release_follow_hotkey_button" type="button"></button>
        <span id="hookSchemeHint"></span>
      </div>

    <section id="logs" class="logs panel">
      <div class="log-head">
        <h2>实时日志</h2>
        <div class="buttons" style="margin:0">
          <button onclick="togglePause()" id="pauseBtn">暂停</button>
          <button onclick="exportLogs()">导出</button>
        </div>
      </div>
      <div class="log-grid">
        <pre id="rawLog" data-title="原始上报">串口原始日志等待中...</pre>
        <pre id="stateLog" data-title="状态判定">板子判定日志等待中...</pre>
        <pre id="actionLog" data-title="动作执行">动作日志等待中...</pre>
      </div>
    </section>
  </main>
  </div>

  <script>
    const maxLogLines = 220;
    let config = {};
    let samples = [];
    let paused = false;
    let realDevice = "";
    let resizeTimer = null;
    let simulation = {
      simulation_enabled: false,
      simulation_device: "",
      simulation_hook_state: "PRESSED",
      simulation_hook_label: "按下",
      simulation_pressed_level: "HIGH",
      simulation_released_level: "LOW",
      simulation_buzzer: "OFF",
      simulation_led: "OFF",
      simulation_wifi_ip: "",
      simulation_wifi_rssi: 0,
      simulation_uptime_seconds: 0,
      simulation_auto_pulse_enabled: true,
      simulation_sample_interval_seconds: 1,
      simulation_pulse_interval_seconds: 8
    };
    const rawLogs = [];
    const stateLogs = [];
    const actionLogs = [];
    const hookSchemeDescriptions = {
      scheme1: "方案 1：HIGH = 按下，LOW = 抬起",
      scheme2: "方案 2：LOW = 按下，HIGH = 抬起"
    };
    const businessModeDescriptions = {
      codex: "机器人提醒：任务完成后呼叫桌面电话，摘机即确认收到。",
      doubao: "语音聊天：抬起听筒后进入语音报告或全双工对话。"
    };
    let actionPresets = {
      current: {
        press_action_text: "控制键+Windows键+Shift键, 延迟1000毫秒, 回车",
        release_action_text: "控制键+Windows键+Shift键"
      },
      voice_call: {
        press_action_text: "Ctrl+Alt+I",
        release_action_text: "Ctrl+Alt+U"
      }
    };
    let activeCapture = null;

    const keyAliases = new Map([
      ["ctrl", "ctrl"], ["control", "ctrl"], ["控制键", "ctrl"], ["左控制键", "ctrl"],
      ["win", "win"], ["windows", "win"], ["windows键", "win"], ["窗口键", "win"], ["meta", "win"], ["gui", "win"],
      ["shift", "shift"], ["shift键", "shift"], ["上档键", "shift"],
      ["alt", "alt"], ["alt键", "alt"],
      ["enter", "enter"], ["return", "enter"], ["回车", "enter"], ["回车键", "enter"],
      ["space", "space"], ["空格", "space"],
      ["tab", "tab"], ["制表键", "tab"],
      ["esc", "esc"], ["escape", "esc"], ["退出键", "esc"]
    ]);
    const keyLabels = {
      ctrl: "Ctrl", win: "Windows", alt: "Alt", shift: "Shift",
      enter: "Enter", space: "Space", tab: "Tab", esc: "Esc"
    };
    const modifierOrder = ["ctrl", "win", "alt", "shift"];
    const modifierKeys = new Set(modifierOrder);
    const tabId = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const consoleChannel = "BroadcastChannel" in window ? new BroadcastChannel("ai-desk-phone-console") : null;
    let events = null;

    function $(id) { return document.getElementById(id); }

    function pushLog(target, line) {
      target.push(line);
      while (target.length > maxLogLines) target.shift();
    }

    function setStatusValue(id, text, tone = "") {
      const node = $(id);
      if (!node) return;
      node.textContent = text;
      node.className = `status-value ${tone}`.trim();
    }

    function setSerialStatus(isConnected, port = "") {
      setStatusValue("serialStatus", isConnected ? (port || "已连接") : "扫描中", isConnected ? "good" : "warn");
    }

    function setDeviceStatus(device = "") {
      realDevice = device || "";
      const fallbackDevice = simulation.simulation_enabled ? (simulation.simulation_device || "模拟发送端") : "未发现";
      setStatusValue("deviceStatus", realDevice || fallbackDevice, (realDevice || simulation.simulation_enabled) ? "good" : "");
      renderSourceStatus();
    }

    function renderSourceStatus() {
      const sourceText = realDevice ? "真实设备" : (simulation.simulation_enabled ? "模拟信号" : "等待设备");
      const tone = realDevice || simulation.simulation_enabled ? "good" : "warn";
      setStatusValue("sourceStatus", sourceText, tone);
    }

    function setSimulationStatus(nextSimulation = {}) {
      simulation = {...simulation, ...nextSimulation};
      const enabled = Boolean(simulation.simulation_enabled);
      const hookText = `${simulation.simulation_hook_label || "--"} / ${simulation.simulation_hook_state || "--"}`;
      const wifiText = enabled ? `${simulation.simulation_wifi_ip || "192.0.2.113"} / ${simulation.simulation_wifi_rssi ?? "--"} dBm` : "关闭";
      const outputText = `蜂鸣器 ${simulation.simulation_buzzer || "OFF"} / LED ${simulation.simulation_led || "OFF"}`;
      const uptime = Number(simulation.simulation_uptime_seconds || 0);
      $("simulationState").textContent = enabled ? "启用" : "关闭";
      $("simulationState").className = enabled ? "state-pressed" : "";
      $("simulationHook").textContent = hookText;
      $("simulationUptime").textContent = enabled ? `${Math.floor(uptime / 60)}分${uptime % 60}秒` : "--";
      $("simulationWifi").textContent = wifiText;
      $("simulationOutput").textContent = outputText;
      $("simPressedBtn").textContent = `模拟按下 (${simulation.simulation_pressed_level || "HIGH"})`;
      $("simReleasedBtn").textContent = `模拟抬起 (${simulation.simulation_released_level || "LOW"})`;
      $("simulationHint").textContent = enabled
        ? `模拟端运行中：每 ${simulation.simulation_sample_interval_seconds || 1} 秒发送心跳，每 ${simulation.simulation_pulse_interval_seconds || 8} 秒自动短按。`
        : "模拟链路已关闭：页面只等待真实 ESP32 串口或 UDP 数据。";
      setDeviceStatus(realDevice);
    }

    function updateAlertStatus(alerting, phase = "") {
      const phaseLabels = {
        idle: "未触发",
        ring: "响铃 1 秒",
        pause: "间歇 4 秒",
        busy: "忙音"
      };
      const alertText = alerting ? (phaseLabels[phase] || "提醒中") : "未触发";
      $("alertState").textContent = alertText;
      $("alertState").className = alerting ? "state-pressed" : "";
    }

    function setSaveStatus(text, tone = "") {
      const node = $("saveStatus");
      node.textContent = text;
      node.className = `hint save-status ${tone}`.trim();
    }

    function renderLogs() {
      if (paused) return;
      $("rawLog").textContent = rawLogs.join("\n") || "串口原始日志等待中...";
      $("stateLog").textContent = stateLogs.join("\n") || "板子判定日志等待中...";
      $("actionLog").textContent = actionLogs.join("\n") || "动作日志等待中...";
      for (const id of ["rawLog", "stateLog", "actionLog"]) {
        const node = $(id);
        node.scrollTop = node.scrollHeight;
      }
    }

    function normalizeKeyToken(value) {
      const compact = value.trim().replace(/\s+/g, "").toLowerCase();
      if (keyAliases.has(compact)) return keyAliases.get(compact);
      if (/^[a-z0-9]$/.test(compact)) return compact;
      return "";
    }

    function orderedHotkey(keys) {
      const unique = [...new Set(keys.filter(Boolean))];
      const modifiers = modifierOrder.filter(key => unique.includes(key));
      const normalKeys = unique.filter(key => !modifierKeys.has(key));
      return [...modifiers, ...normalKeys].join("+");
    }

    function normalizeHotkeyText(text) {
      const keys = text.split(/[+＋]/).map(normalizeKeyToken).filter(Boolean);
      return orderedHotkey(keys);
    }

    function displayHotkey(value) {
      if (!value) return "无";
      return value.split("+").map(key => keyLabels[key] || key.toUpperCase()).join(" + ");
    }

    function setHotkeyField(id, value) {
      const canonical = normalizeHotkeyText(value || "");
      $(id).value = canonical;
      $(`${id}_button`).textContent = canonical ? displayHotkey(canonical) : "无";
    }

    function parseActionTextForEditor(text) {
      const result = {primary: "", delay: 0, follow: ""};
      const parts = String(text || "").split(/[,，;；]/).map(part => part.trim()).filter(Boolean);
      for (const part of parts) {
        const delayMatch = part.match(/(?:延迟|等待|delay)\s*(\d+)\s*(?:毫秒|ms)?/i);
        if (delayMatch) {
          result.delay = Number(delayMatch[1]);
          continue;
        }

        const hotkey = normalizeHotkeyText(part);
        if (!hotkey) continue;
        if (!result.primary) result.primary = hotkey;
        else if (!result.follow) result.follow = hotkey;
      }
      return result;
    }

    function setActionEditor(prefix, text) {
      const action = parseActionTextForEditor(text);
      setHotkeyField(`${prefix}_primary_hotkey`, action.primary);
      $(`${prefix}_delay_ms`).value = String(action.delay || 0);
      setHotkeyField(`${prefix}_follow_hotkey`, action.follow);
      $(`${prefix}_action_text`).value = composeActionText(prefix);
    }

    function composeActionText(prefix) {
      const steps = [];
      const primary = $(`${prefix}_primary_hotkey`).value;
      const delay = Number($(`${prefix}_delay_ms`).value || 0);
      const follow = $(`${prefix}_follow_hotkey`).value;
      if (primary) steps.push(primary);
      if (delay > 0) steps.push(`延迟${delay}毫秒`);
      if (follow) steps.push(follow);
      return steps.join(", ");
    }

    function setConfigForm(nextConfig) {
      config = nextConfig;
      for (const [key, value] of Object.entries(config)) {
        const node = $(key);
        if (!node) continue;
        node.value = String(value);
      }
      applyHookSchemeUi(config.hook_scheme || "scheme1");
      applyBusinessModeUi(config.business_mode || "codex");
      setActionEditor("press", config.press_action_text || "");
      setActionEditor("release", config.release_action_text || "");
    }

    function getConfigForm() {
      const numeric = [
        "press_threshold", "release_threshold", "strong_low_press_threshold",
        "strong_high_press_threshold", "debounce_ms", "press_lockout_ms"
      ];
      const next = {...config};
      for (const key of numeric) {
        const node = $(key);
        if (node) next[key] = Number(node.value);
      }
      next.business_mode = $("business_mode").value || "codex";
      next.hook_scheme = $("hook_scheme").value || "scheme1";
      const adcPolarity = $("adc_low_means_pressed");
      if (adcPolarity) next.adc_low_means_pressed = adcPolarity.value === "true";
      const enableActions = $("enable_actions");
      if (enableActions) next.enable_actions = enableActions.value === "true";
      next.press_action_text = composeActionText("press");
      next.release_action_text = composeActionText("release");
      $("press_action_text").value = next.press_action_text;
      $("release_action_text").value = next.release_action_text;
      return next;
    }

    async function loadActionPresets() {
      const response = await fetch("/api/action-presets");
      actionPresets = await response.json();
    }

    function applyPreset(name) {
      const preset = actionPresets[name];
      if (!preset) return;
      setActionEditor("press", preset.press_action_text);
      setActionEditor("release", preset.release_action_text);
      config = {
        ...config,
        press_action_text: composeActionText("press"),
        release_action_text: composeActionText("release")
      };
    }

    function addEventModifiers(event, keys) {
      if (event.ctrlKey) keys.add("ctrl");
      if (event.metaKey) keys.add("win");
      if (event.altKey) keys.add("alt");
      if (event.shiftKey) keys.add("shift");
    }

    function keyFromEvent(event) {
      if (event.key === "Control") return "ctrl";
      if (event.key === "Meta") return "win";
      if (event.key === "Alt") return "alt";
      if (event.key === "Shift") return "shift";
      if (event.key === "Enter") return "enter";
      if (event.key === " ") return "space";
      if (event.key === "Tab") return "tab";
      if (event.key === "Escape") return "esc";
      if (/^[a-z0-9]$/i.test(event.key)) return event.key.toLowerCase();
      return "";
    }

    function renderActiveCapture() {
      if (!activeCapture) return;
      const hotkey = orderedHotkey([...activeCapture.keys]);
      $(`${activeCapture.id}_button`).textContent = hotkey ? displayHotkey(hotkey) : "按下快捷键";
    }

    function finishHotkeyCapture() {
      if (!activeCapture) return;
      const id = activeCapture.id;
      const hotkey = orderedHotkey([...activeCapture.keys]);
      setHotkeyField(id, hotkey);
      $(`${id}_button`).classList.remove("active");
      activeCapture = null;
    }

    function startHotkeyCapture(id) {
      if (activeCapture) finishHotkeyCapture();
      activeCapture = {id, keys: new Set()};
      const button = $(`${id}_button`);
      button.classList.add("active");
      button.textContent = "按下快捷键";
      button.focus();
    }

    document.addEventListener("keydown", event => {
      if (!activeCapture) return;
      event.preventDefault();
      event.stopPropagation();

      if (event.key === "Backspace" || event.key === "Delete") {
        activeCapture.keys.clear();
        finishHotkeyCapture();
        return;
      }

      addEventModifiers(event, activeCapture.keys);
      const key = keyFromEvent(event);
      if (key) activeCapture.keys.add(key);
      renderActiveCapture();
      if (key && !modifierKeys.has(key)) finishHotkeyCapture();
    }, true);

    document.addEventListener("keyup", event => {
      if (!activeCapture) return;
      event.preventDefault();
      event.stopPropagation();

      const key = keyFromEvent(event);
      if (key) activeCapture.keys.add(key);
      renderActiveCapture();

      const onlyModifiers = [...activeCapture.keys].every(keyName => modifierKeys.has(keyName));
      if (onlyModifiers && !event.ctrlKey && !event.metaKey && !event.altKey && !event.shiftKey) {
        finishHotkeyCapture();
      }
    }, true);

    async function fetchJson(url, options = {}, timeoutMs = 30000) {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), timeoutMs);
      try {
        const response = await fetch(url, {...options, signal: controller.signal});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return await response.json();
      } catch (error) {
        if (error.name === "AbortError") throw new Error("请求超时，请关闭其他控制台标签页后刷新重试");
        throw error;
      } finally {
        clearTimeout(timeout);
      }
    }

    async function loadConfig() {
      setConfigForm(await fetchJson("/api/config"));
      drawDigitalChart();
    }

    async function saveConfig() {
      setSaveStatus("正在保存配置...");
      try {
        const savedConfig = await fetchJson("/api/config", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(getConfigForm())
        }, 30000);
        setConfigForm(savedConfig);
        drawDigitalChart();
        setSaveStatus("配置已保存到电脑，并已发送给 ESP32；等待板子确认。", "ok");
      } catch (error) {
        setSaveStatus(`保存失败：${error.message}`, "warn");
      }
    }

    async function postAction(url) {
      await fetch(url, {method: "POST"});
    }

    async function postHardwareCommand(command) {
      try {
        const result = await fetchJson(`/api/hardware/${command}`, {method: "POST"}, 10000);
        setSaveStatus(result.ok ? `硬件命令已发送：${command}` : `硬件命令发送失败：${command}`, result.ok ? "ok" : "warn");
      } catch (error) {
        setSaveStatus(`硬件命令失败：${error.message}`, "warn");
      }
    }

    function applyHookSchemeUi(scheme) {
      const normalized = hookSchemeDescriptions[scheme] ? scheme : "scheme1";
      $("hook_scheme").value = normalized;
      $("hookSchemeHint").textContent = hookSchemeDescriptions[normalized];
      $("scheme1Btn").classList.toggle("active", normalized === "scheme1");
      $("scheme2Btn").classList.toggle("active", normalized === "scheme2");
    }

    function applyBusinessModeUi(mode) {
      const normalized = businessModeDescriptions[mode] ? mode : "codex";
      $("business_mode").value = normalized;
      $("businessModeHint").textContent = businessModeDescriptions[normalized];
      $("modeCodexBtn").classList.toggle("active", normalized === "codex");
      $("modeDoubaoBtn").classList.toggle("active", normalized === "doubao");
    }

    async function selectBusinessMode(mode) {
      applyBusinessModeUi(mode);
      try {
        const savedConfig = await fetchJson("/api/business-mode", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({business_mode: mode})
        }, 10000);
        setConfigForm(savedConfig);
        setSaveStatus(`${businessModeDescriptions[savedConfig.business_mode || "codex"]}`, "ok");
      } catch (error) {
        setSaveStatus(`业务模式切换失败：${error.message}`, "warn");
      }
    }

    async function selectHookScheme(scheme) {
      applyHookSchemeUi(scheme);
      try {
        const savedConfig = await fetchJson("/api/hook-scheme", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({hook_scheme: scheme})
        }, 10000);
        setConfigForm(savedConfig);
        setSaveStatus(`${hookSchemeDescriptions[savedConfig.hook_scheme || "scheme1"]} 已生效`, "ok");
      } catch (error) {
        setSaveStatus(`方案切换失败：${error.message}`, "warn");
      }
    }

    async function postAiHook() {
      try {
        const result = await fetchJson("/api/ai/hook", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({source: "web"})
        }, 10000);
        setSaveStatus(result.ok ? "接线员提醒已触发：1 秒响/亮、4 秒停/灭，摘机后停止。" : "接线员提醒发送失败", result.ok ? "ok" : "warn");
      } catch (error) {
        setSaveStatus(`接线员提醒失败：${error.message}`, "warn");
      }
    }

    async function clearAiAlert() {
      try {
        const result = await fetchJson("/api/alert/clear", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({reason: "web"})
        }, 10000);
        setSaveStatus(result.ok ? "提醒已停止" : "停止提醒命令未发送到设备", result.ok ? "ok" : "warn");
      } catch (error) {
        setSaveStatus(`停止提醒失败：${error.message}`, "warn");
      }
    }

    async function setSimulationEnabled(enabled) {
      try {
        const result = await fetchJson("/api/simulation", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({enabled})
        }, 10000);
        setSimulationStatus(result);
        setSaveStatus(enabled ? "模拟链路已启用。" : "模拟链路已关闭。", result.ok ? "ok" : "warn");
      } catch (error) {
        setSaveStatus(`模拟链路切换失败：${error.message}`, "warn");
      }
    }

    async function postSimulationState(state) {
      const route = state === "PRESSED" ? "/api/simulate/press" : "/api/simulate/release";
      try {
        const result = await fetchJson(route, {method: "POST"}, 10000);
        setSimulationStatus(result);
        setSaveStatus(result.ok ? `已生成模拟${state === "PRESSED" ? "按下" : "抬起"}信号。` : "模拟信号未发送", result.ok ? "ok" : "warn");
      } catch (error) {
        setSaveStatus(`模拟信号失败：${error.message}`, "warn");
      }
    }

    async function applyHardwarePins() {
      const hookPin = Number($("hookPinInput").value || 0);
      const buzzerPin = Number($("buzzerPinInput").value || 21);
      const ledPin = Number($("ledPinInput").value || 20);
      try {
        const result = await fetchJson("/api/hardware/pins", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({hook_pin: hookPin, buzzer_pin: buzzerPin, led_pin: ledPin})
        }, 10000);
        setSaveStatus(result.ok ? `测试引脚已应用：开关 GPIO${hookPin}，蜂鸣器 GPIO${buzzerPin}，LED GPIO${ledPin}` : "测试引脚发送失败", result.ok ? "ok" : "warn");
      } catch (error) {
        setSaveStatus(`测试引脚失败：${error.message}`, "warn");
      }
    }

    function clearLogs() {
      rawLogs.length = 0;
      stateLogs.length = 0;
      actionLogs.length = 0;
      renderLogs();
    }

    function togglePause() {
      paused = !paused;
      $("pauseBtn").textContent = paused ? "继续" : "暂停";
      renderLogs();
    }

    function exportLogs() {
      const body = [
        "# 串口原始日志", rawLogs.join("\n"), "",
        "# 板子判定日志", stateLogs.join("\n"), "",
        "# 动作日志", actionLogs.join("\n")
      ].join("\n");
      const blob = new Blob([body], {type: "text/plain;charset=utf-8"});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "ai-desk-phone-console-log.txt";
      link.click();
      URL.revokeObjectURL(link.href);
    }

    function updateSample(sample) {
      $("adcValue").textContent = sample.adc_synthetic ? `${sample.digital === "LOW" ? 0 : 1} / 数字` : sample.adc;
      $("digitalValue").textContent = sample.digital;
      $("stateValue").textContent = sample.hook_label || (sample.python_state === "PRESSED" ? "按下" : "抬起");
      $("stateValue").className = "value " + (sample.python_state === "PRESSED" ? "state-pressed" : "state-released");
      if (sample.hook_scheme) applyHookSchemeUi(sample.hook_scheme);
      if (sample.pin !== null && sample.pin !== undefined) $("hookPinInput").value = sample.pin;
      if (sample.led_pin !== null && sample.led_pin !== undefined) $("ledPinInput").value = sample.led_pin;
      if (sample.business_mode) applyBusinessModeUi(sample.business_mode);
      updateAlertStatus(Boolean(sample.alerting), sample.alert_phase || "");
      $("buzzerState").textContent = sample.buzzer || "未知";
      $("ledState").textContent = sample.led || "未知";
      const sampleSource = sample.sample_source === "simulation" ? "模拟" : "设备";
      $("lastSample").textContent = sample.adc_synthetic
        ? `${sampleSource} GPIO${sample.pin ?? ""} ${sample.digital}`.trim()
        : `${sampleSource} ADC ${sample.adc}`;
      samples.push(sample);
      while (samples.length > 180) samples.shift();
      drawDigitalChart();
    }

    function drawDigitalChart() {
      const canvas = $("digitalChart");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const dpr = Math.max(window.devicePixelRatio || 1, 1);
      const rect = canvas.getBoundingClientRect();
      const w = Math.max(320, Math.floor(rect.width || 900));
      const h = 150;
      const pixelWidth = Math.floor(w * dpr);
      const pixelHeight = Math.floor(h * dpr);
      if (canvas.width !== pixelWidth || canvas.height !== pixelHeight) {
        canvas.width = pixelWidth;
        canvas.height = pixelHeight;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, w, h);

      ctx.strokeStyle = "#e5e7eb";
      ctx.lineWidth = 1;
      for (const y of [30, h - 30]) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      }

      ctx.font = "11px Microsoft YaHei, sans-serif";
      ctx.fillStyle = "#746f66";
      ctx.fillText("HIGH", 10, 22);
      ctx.fillText("LOW", 10, h - 12);
      ctx.fillText("GPIO", w - 48, 22);

      if (samples.length < 2) return;
      const yFor = sample => (sample.digital === "LOW" || sample.digital_value === 0) ? h - 30 : 30;

      ctx.strokeStyle = "#8b1e1e";
      ctx.lineWidth = 1.45;
      ctx.beginPath();
      samples.forEach((sample, index) => {
        const x = samples.length === 1 ? 0 : (w * index) / (samples.length - 1);
        const y = yFor(sample);
        if (index === 0) {
          ctx.moveTo(x, y);
          return;
        }
        const prevX = (w * (index - 1)) / (samples.length - 1);
        const prevY = yFor(samples[index - 1]);
        ctx.lineTo(x, prevY);
        ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    function connectEvents() {
      if (events) events.close();
      if (consoleChannel) consoleChannel.postMessage({type: "active", tabId});
      events = new EventSource("/events");
      events.onopen = () => {
        setStatusValue("conn", "已连接", "good");
      };
      events.onerror = () => {
        setStatusValue("conn", "重连中", "warn");
      };
      events.onmessage = event => {
        const payload = JSON.parse(event.data);
        if (payload.type === "snapshot") {
          setConfigForm(payload.config);
          setSimulationStatus(payload);
          setSerialStatus(payload.serial_connected, payload.serial_port);
          setDeviceStatus(payload.udp_device || "");
          updateAlertStatus(Boolean(payload.alerting), payload.alert_phase || "");
          samples = payload.samples || [];
          rawLogs.splice(0, rawLogs.length, ...(payload.raw_logs || []));
          stateLogs.splice(0, stateLogs.length, ...(payload.state_logs || []));
          actionLogs.splice(0, actionLogs.length, ...(payload.action_logs || []));
          if (payload.current_sample) updateSample(payload.current_sample);
          renderLogs();
          drawDigitalChart();
        } else if (payload.type === "config") {
          setConfigForm(payload.config);
        } else if (payload.type === "raw_log") {
          pushLog(rawLogs, payload.text);
          renderLogs();
        } else if (payload.type === "state_log") {
          pushLog(stateLogs, payload.text);
          if (payload.text.includes("ESP32 已确认配置写入板子")) {
            setSaveStatus("ESP32 已确认配置写入板子。", "ok");
          } else if (payload.text.includes("没有成功写入 ESP32")) {
            setSaveStatus("配置已保存到电脑，但板子未写入。", "warn");
          }
          renderLogs();
        } else if (payload.type === "serial_status") {
          setSerialStatus(payload.serial_connected, payload.port);
        } else if (payload.type === "udp_status") {
          setDeviceStatus(payload.device || "");
        } else if (payload.type === "simulation_status") {
          setSimulationStatus(payload);
        } else if (payload.type === "alert_status") {
          updateAlertStatus(Boolean(payload.alerting), payload.alert_phase || "");
        } else if (payload.type === "action_log") {
          pushLog(actionLogs, payload.text);
          renderLogs();
        } else if (payload.type === "sample") {
          updateSample(payload.sample);
        }
      };
    }

    if (consoleChannel) {
      consoleChannel.onmessage = event => {
        if (event.data?.type !== "active" || event.data.tabId === tabId) return;
        if (events) {
          events.close();
          events = null;
        }
        setStatusValue("conn", "被其他标签接管", "warn");
      };
    }

    window.addEventListener("beforeunload", () => {
      if (events) events.close();
      if (consoleChannel) consoleChannel.close();
    });
    window.addEventListener("resize", () => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(drawDigitalChart, 80);
    });

    async function init() {
      await loadActionPresets();
      await loadConfig();
      connectEvents();
    }

    init();
  </script>
</body>
</html>
"""


SIMULATOR_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Desk Phone 模拟台</title>
  <style>
    :root {
      --ink: #191713;
      --muted: #6f6a60;
      --line: #ded8cb;
      --panel: #fffdf8;
      --paper: #f4efe4;
      --red: #8b1e1e;
      --green: #256a45;
      --blue: #255a7a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--paper);
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      background: #fffaf0;
    }
    h1 { margin: 0; font-size: 20px; letter-spacing: 0; }
    a { color: var(--red); text-decoration: none; }
    main {
      display: grid;
      grid-template-columns: minmax(300px, 420px) 1fr;
      gap: 14px;
      padding: 14px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }
    h2 { margin: 0 0 12px; font-size: 15px; }
    .stack { display: grid; gap: 14px; }
    .status-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .metric {
      min-height: 76px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fffaf2;
    }
    .metric span { display: block; color: var(--muted); font-size: 12px; }
    .metric strong {
      display: block;
      margin-top: 8px;
      font-size: 18px;
      overflow-wrap: anywhere;
    }
    label { display: grid; gap: 6px; color: var(--muted); font-size: 12px; }
    input, textarea, select {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 9px 10px;
      font: inherit;
      color: var(--ink);
      background: #fffefa;
    }
    textarea { min-height: 118px; resize: vertical; }
    .row {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .buttons { display: flex; flex-wrap: wrap; gap: 8px; }
    button {
      min-height: 38px;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 12px;
      font: inherit;
      color: var(--ink);
      background: #fffefa;
      cursor: pointer;
    }
    button.primary { border-color: var(--red); background: var(--red); color: #fff; }
    button.blue { border-color: var(--blue); color: var(--blue); }
    button.green { border-color: var(--green); color: var(--green); }
    .queue {
      display: grid;
      gap: 8px;
      align-content: start;
    }
    .reply {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #fffefa;
    }
    .reply strong { display: block; margin-bottom: 4px; }
    .reply p { margin: 0; color: var(--muted); line-height: 1.5; }
    .pill {
      display: inline-block;
      border-radius: 999px;
      padding: 2px 8px;
      margin-left: 6px;
      background: #ece4d6;
      color: var(--muted);
      font-size: 12px;
    }
    .good { color: var(--green); }
    .warn { color: var(--red); }
    pre {
      margin: 0;
      min-height: 180px;
      max-height: 320px;
      overflow: auto;
      white-space: pre-wrap;
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      background: #171510;
      color: #f6eddc;
      font-size: 12px;
    }
    @media (max-width: 860px) {
      main { grid-template-columns: 1fr; }
      .row, .status-grid { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AI Desk Phone 模拟台</h1>
    <a href="/">主控制台</a>
  </header>
  <main>
    <div class="stack">
      <section>
        <h2>电话状态</h2>
        <div class="status-grid">
          <div class="metric"><span>听筒</span><strong id="hookState">--</strong></div>
          <div class="metric"><span>提醒</span><strong id="alertState">--</strong></div>
          <div class="metric"><span>播放</span><strong id="playbackState">--</strong></div>
          <div class="metric"><span>队列</span><strong id="queueState">--</strong></div>
          <div class="metric"><span>录音</span><strong id="voiceState">--</strong></div>
          <div class="metric"><span>语音</span><strong id="speechState">--</strong></div>
        </div>
        <div class="buttons" style="margin-top:10px">
          <button class="green" onclick="simulate('RELEASED')">模拟抬起</button>
          <button class="primary" onclick="simulate('PRESSED')">模拟按下</button>
          <button onclick="setSimulation(true)">启用模拟链路</button>
          <button onclick="setSimulation(false)">停用模拟链路</button>
        </div>
      </section>

      <section>
        <h2>快捷键与音频</h2>
        <label><input id="enableActions" type="checkbox">启用摘挂机快捷键</label>
        <label>摘机动作<input id="offHookAction" placeholder="ctrl+win+shift"></label>
        <label>挂机动作<input id="onHookAction" placeholder="ctrl+win+shift, 延迟1000毫秒, enter"></label>
        <label><input id="enableCallback" type="checkbox">任务完成后回话</label>
        <label><input id="enableTts" type="checkbox">使用 Windows 语音播放</label>
        <label><input id="enableVoiceAsr" type="checkbox">豆包 ASR 语音识别</label>
        <div class="row">
          <label>语速<input id="ttsRate" type="number" min="-10" max="10" step="1"></label>
          <label>音量<input id="ttsVolume" type="number" min="0" max="100" step="1"></label>
        </div>
        <div class="row">
          <label>录音采样率<input id="voiceSampleRate" type="number" min="8000" max="48000" step="1000"></label>
          <label>回话策略
            <select id="voiceReplyPolicy">
              <option value="silent">只记录</option>
              <option value="callback">识别后回话</option>
            </select>
          </label>
        </div>
        <label>录音设备<input id="voiceRecordDevice" placeholder="留空使用默认麦克风 / 电话蓝牙输入"></label>
        <label>音频设备备注<input id="audioOutputDevice" placeholder="默认输出 / 电话蓝牙模块"></label>
        <label>Agent 权限
          <select id="agentPermissionProfile">
            <option value="commander">首长模式</option>
            <option value="confirm_sensitive">敏感动作确认</option>
          </select>
        </label>
        <div class="buttons">
          <button class="primary" onclick="saveConfig()">保存配置</button>
        </div>
      </section>

      <section>
        <h2>豆包语音密钥</h2>
        <label>新版 API Key<input id="speechApiKey" type="password" autocomplete="off" placeholder="保存后写入本机 .env"></label>
        <label>TTS 音色<input id="speechSpeaker" value="zh_female_tianmeitaozi_uranus_bigtts"></label>
        <div class="buttons">
          <button class="primary" onclick="saveSpeechConfig()">保存语音密钥</button>
          <button onclick="refreshSpeechStatus()">刷新状态</button>
        </div>
      </section>
    </div>

    <div class="stack">
      <section>
        <h2>造一条回话</h2>
        <div class="row">
          <label>来源<input id="replySource" value="manual"></label>
          <label>标题<input id="replyTitle" value="通讯员回报"></label>
        </div>
        <label>内容<textarea id="replyText">首长，测试任务已经完成。当前队列和电话回话链路正常。</textarea></label>
        <div class="buttons">
          <button class="primary" onclick="enqueueReply()">入队并呼叫</button>
          <button class="blue" onclick="postHook()">模拟 Codex hook</button>
          <button onclick="stopPlayback()">停止播放</button>
          <button onclick="clearReplies()">清空队列</button>
        </div>
      </section>

      <section>
        <h2>回话队列</h2>
        <div id="queue" class="queue"></div>
      </section>

      <section>
        <h2>动作日志</h2>
        <pre id="logs">等待事件...</pre>
      </section>
    </div>
  </main>

  <script>
    const $ = id => document.getElementById(id);
    const logs = [];
    let config = {};
    let latestSample = null;
    let replyStatus = {reply_queue: [], active_reply: null, completed_replies: [], queue_size: 0, playback_active: false};
    let voiceStatus = {recording: false, last_result: null, last_error: null};
    let speechStatus = {credential_mode: "missing", tts_ready: false, asr_ready: false};

    function pushLog(text) {
      logs.push(text);
      while (logs.length > 80) logs.shift();
      $("logs").textContent = logs.join("\n") || "等待事件...";
      $("logs").scrollTop = $("logs").scrollHeight;
    }

    async function fetchJson(url, options = {}) {
      const response = await fetch(url, options);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return await response.json();
    }

    function setConfigForm(next) {
      config = next || {};
      $("enableActions").checked = Boolean(config.enable_actions);
      $("offHookAction").value = config.release_action_text || "";
      $("onHookAction").value = config.press_action_text || "";
      $("enableCallback").checked = config.enable_callback !== false;
      $("enableTts").checked = config.enable_tts_playback !== false;
      $("ttsRate").value = String(config.tts_rate ?? 0);
      $("ttsVolume").value = String(config.tts_volume ?? 100);
      $("audioOutputDevice").value = config.audio_output_device || "";
      $("enableVoiceAsr").checked = config.enable_voice_asr !== false;
      $("voiceSampleRate").value = String(config.voice_record_sample_rate ?? 16000);
      $("voiceRecordDevice").value = config.voice_record_device || "";
      $("voiceReplyPolicy").value = config.voice_reply_policy || "silent";
      $("agentPermissionProfile").value = config.agent_permission_profile || "commander";
    }

    async function saveConfig() {
      const next = {
        ...config,
        enable_actions: $("enableActions").checked,
        release_action_text: $("offHookAction").value,
        press_action_text: $("onHookAction").value,
        enable_callback: $("enableCallback").checked,
        enable_tts_playback: $("enableTts").checked,
        tts_rate: Number($("ttsRate").value || 0),
        tts_volume: Number($("ttsVolume").value || 100),
        audio_output_device: $("audioOutputDevice").value,
        enable_voice_asr: $("enableVoiceAsr").checked,
        voice_record_sample_rate: Number($("voiceSampleRate").value || 16000),
        voice_record_device: $("voiceRecordDevice").value,
        voice_auto_transcribe: true,
        voice_reply_policy: $("voiceReplyPolicy").value,
        agent_permission_profile: $("agentPermissionProfile").value
      };
      setConfigForm(await fetchJson("/api/config", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(next)
      }));
      pushLog("配置已保存。");
    }

    async function startVoice() {
      const result = await fetchJson("/api/voice/start", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({reason: "simulator"})
      });
      updateVoice(result);
      pushLog(result.ok ? "录音已开始。" : `录音启动失败：${result.error}`);
    }

    async function stopVoice() {
      const result = await fetchJson("/api/voice/stop", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({reason: "simulator"})
      });
      updateVoice(result);
      if (result.transcript?.success) {
        pushLog(`识别结果：${result.transcript.text || "（空）"}`);
      } else {
        pushLog(result.ok ? "录音已停止。" : `录音停止失败：${result.error}`);
      }
    }

    async function saveSpeechConfig() {
      const apiKey = $("speechApiKey").value.trim();
      const speaker = $("speechSpeaker").value.trim();
      if (!apiKey && !speaker) {
        pushLog("没有可保存的语音配置。");
        return;
      }
      const result = await fetchJson("/api/speech/config", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({api_key: apiKey, tts_speaker: speaker})
      });
      updateSpeech(result);
      if (result.ok) {
        $("speechApiKey").value = "";
        pushLog(`语音配置已保存：${result.credential_mode || "unknown"}`);
      } else {
        pushLog(`语音配置保存失败：${result.error || "unknown"}`);
      }
    }

    async function refreshSpeechStatus() {
      updateSpeech(await fetchJson("/api/speech/status"));
    }

    async function simulate(state) {
      const route = state === "PRESSED" ? "/api/simulate/press" : "/api/simulate/release";
      const result = await fetchJson(route, {method: "POST"});
      pushLog(state === "PRESSED" ? "模拟按下。" : "模拟抬起。");
      updateSimulation(result);
    }

    async function setSimulation(enabled) {
      const result = await fetchJson("/api/simulation", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({enabled})
      });
      pushLog(enabled ? "模拟链路已启用。" : "模拟链路已停用。");
      updateSimulation(result);
    }

    async function enqueueReply() {
      const result = await fetchJson("/api/replies", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          source: $("replySource").value || "manual",
          title: $("replyTitle").value || "通讯员回报",
          text: $("replyText").value
        })
      });
      updateReplies(result);
      pushLog("回话已入队。");
    }

    async function postHook() {
      const result = await fetchJson("/api/ai/hook", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          source: $("replySource").value || "codex",
          text: $("replyText").value
        })
      });
      pushLog(result.ok ? "Codex hook 已发送。" : "Codex hook 发送失败。");
    }

    async function stopPlayback() {
      const result = await fetchJson("/api/playback/stop", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({reason: "simulator"})
      });
      updateReplies(result);
      pushLog("播放停止命令已发送。");
    }

    async function clearReplies() {
      const result = await fetchJson("/api/replies/clear", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({reason: "simulator"})
      });
      updateReplies(result);
      pushLog("队列已清空。");
    }

    function updateSimulation(payload) {
      if (!payload) return;
      $("hookState").textContent = payload.simulation_hook_label || (latestSample?.hook_label || "--");
    }

    function updateSample(sample) {
      latestSample = sample;
      $("hookState").textContent = sample.hook_label || "--";
      $("alertState").textContent = sample.alerting ? `${sample.alert_phase || "ring"}` : "空闲";
    }

    function renderReply(reply, prefix = "") {
      if (!reply) return "";
      const text = String(reply.text || "").slice(0, 220);
      return `<div class="reply"><strong>${prefix}${escapeHtml(reply.title || reply.id)}<span class="pill">${escapeHtml(reply.status || "")}</span></strong><p>${escapeHtml(text)}</p></div>`;
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;"
      }[char]));
    }

    function updateReplies(payload) {
      if (!payload) return;
      replyStatus = {...replyStatus, ...payload};
      $("playbackState").textContent = replyStatus.playback_active ? "播放中" : "空闲";
      $("queueState").textContent = `${replyStatus.queue_size || 0} 条`;
      const rows = [];
      if (replyStatus.active_reply) rows.push(renderReply(replyStatus.active_reply, "当前："));
      for (const reply of replyStatus.reply_queue || []) rows.push(renderReply(reply));
      if (!rows.length) rows.push("<div class='reply'><strong>暂无回话</strong><p>队列为空。</p></div>");
      $("queue").innerHTML = rows.join("");
    }

    function updateVoice(payload) {
      if (!payload) return;
      voiceStatus = {...voiceStatus, ...payload};
      const recording = Boolean(voiceStatus.recording);
      const processing = Boolean(voiceStatus.processing);
      const result = voiceStatus.last_result || voiceStatus.transcript;
      $("voiceState").textContent = recording ? "录音中" : (processing ? "处理中" : (result?.success ? "已识别" : "空闲"));
    }

    function updateSpeech(payload) {
      if (!payload) return;
      speechStatus = {...speechStatus, ...payload};
      const ready = speechStatus.tts_ready && speechStatus.asr_ready;
      $("speechState").textContent = ready ? speechStatus.credential_mode : "未配置";
      if (speechStatus.tts_speaker) $("speechSpeaker").value = speechStatus.tts_speaker;
    }

    function connectEvents() {
      const events = new EventSource("/events");
      events.onmessage = event => {
        const payload = JSON.parse(event.data);
        if (payload.type === "snapshot") {
          setConfigForm(payload.config);
          updateSimulation(payload);
          updateReplies(payload);
          updateVoice(payload);
          if (payload.tts_ready !== undefined || payload.asr_ready !== undefined) updateSpeech(payload);
          if (payload.current_sample) updateSample(payload.current_sample);
          for (const line of payload.action_logs || []) pushLog(line);
        } else if (payload.type === "config") {
          setConfigForm(payload.config);
        } else if (payload.type === "sample") {
          updateSample(payload.sample);
        } else if (payload.type === "alert_status") {
          $("alertState").textContent = payload.alerting ? `${payload.alert_phase || "ring"}` : "空闲";
        } else if (payload.type === "reply_status") {
          updateReplies(payload);
        } else if (payload.type === "voice_status") {
          updateVoice(payload);
        } else if (payload.type === "simulation_status") {
          updateSimulation(payload);
        } else if (payload.type === "action_log") {
          pushLog(payload.text);
        }
      };
    }

    async function init() {
      setConfigForm(await fetchJson("/api/config"));
      updateReplies(await fetchJson("/api/replies"));
      updateSimulation(await fetchJson("/api/simulation"));
      updateVoice(await fetchJson("/api/voice/status"));
      updateSpeech(await fetchJson("/api/speech/status"));
      connectEvents();
    }
    init().catch(error => pushLog(`初始化失败：${error.message}`));
  </script>
</body>
</html>
"""


class ConsoleHandler(BaseHTTPRequestHandler):
    app: AppState

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            self.send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif route == "/simulator":
            self.send_bytes(SIMULATOR_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif route == "/api/config":
            self.send_json(self.app.config.to_dict())
        elif route == "/api/action-presets":
            self.send_json(action_presets())
        elif route == "/api/simulation":
            self.send_json(self.app.simulation_status())
        elif route == "/api/replies":
            self.send_json(self.app.reply_status())
        elif route == "/api/speech/status":
            self.send_json(self.app.speech_status())
        elif route == "/api/voice/status":
            self.send_json(self.app.voice_status())
        elif route == "/events":
            self.handle_events()
        else:
            self.send_error(404)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/config":
            data = self.read_json()
            config = ConsoleConfig.from_dict(data)
            self.app.add_state_log("收到保存配置请求。")
            self.app.update_config(config)
            self.send_json(config.to_dict())
        elif route == "/api/hook-scheme":
            data = self.read_json()
            config = self.app.set_hook_scheme(data.get("hook_scheme", "scheme1"))
            self.send_json(config.to_dict())
        elif route == "/api/business-mode":
            data = self.read_json()
            config = self.app.set_business_mode(data.get("business_mode", "codex"))
            self.send_json(config.to_dict())
        elif route in {"/api/ai/hook", "/hook"}:
            data = self.read_json()
            source = str(data.get("source", "ai"))
            text = extract_reply_text_from_hook(data)
            ok = self.app.run_ai_hook_signal(source, text)
            self.send_json({"ok": ok, "source": source})
        elif route == "/api/replies":
            data = self.read_json()
            source = str(data.get("source", "manual"))
            text = extract_reply_text_from_hook(data)
            title = str(data.get("title", "") or "") or None
            audio_path = str(data.get("audio_path", "") or "") or None
            self.app.enqueue_reply(source, text, title=title, audio_path=audio_path)
            with self.app.lock:
                already_off_hook = self.app.last_state == "RELEASED"
                callback_enabled = self.app.config.enable_callback
            if already_off_hook:
                with self.app.lock:
                    self.app.callback_session_active = True
                self.app.start_reply_playback("手动入队")
            elif callback_enabled:
                self.app.start_operator_alert(source)
            self.send_json({"ok": True, **self.app.reply_status()})
        elif route == "/api/replies/clear":
            data = self.read_json()
            reason = str(data.get("reason", "manual"))
            self.app.clear_reply_queue(reason)
            self.send_json({"ok": True, **self.app.reply_status()})
        elif route == "/api/playback/stop":
            data = self.read_json()
            reason = str(data.get("reason", "manual"))
            ok = self.app.stop_reply_playback(reason, wait_seconds=0.8)
            self.send_json({"ok": ok, **self.app.reply_status()})
        elif route == "/api/speech/transcribe-file":
            data = self.read_json()
            audio_path = Path(str(data.get("path", "") or "")).expanduser()
            result = self.app.transcribe_audio_file(audio_path)
            self.send_json(result)
        elif route == "/api/speech/config":
            data = self.read_json()
            values: dict[str, str] = {}
            api_key = str(data.get("api_key") or data.get("VOLCENGINE_API_KEY") or "").strip()
            speaker = str(data.get("tts_speaker") or data.get("DOUBAO_TTS_SPEAKER") or "").strip()
            if api_key:
                values["VOLCENGINE_API_KEY"] = api_key
            if speaker:
                values["DOUBAO_TTS_SPEAKER"] = speaker
            if not values:
                self.send_json({"ok": False, "error": "no speech configuration values were provided"})
                return
            self.send_json(self.app.update_speech_env(values))
        elif route == "/api/voice/start":
            data = self.read_json()
            reason = str(data.get("reason", "web"))
            self.send_json(self.app.start_voice_recording(reason))
        elif route == "/api/voice/stop":
            data = self.read_json()
            reason = str(data.get("reason", "web"))
            self.send_json(self.app.stop_voice_recording(reason))
        elif route == "/api/alert/clear":
            data = self.read_json()
            reason = str(data.get("reason", "manual"))
            ok = self.app.clear_ai_alert(reason)
            self.send_json({"ok": ok})
        elif route == "/api/simulation":
            data = self.read_json()
            enabled = bool(data.get("enabled", True))
            status = self.app.set_simulation_enabled(enabled)
            self.send_json({"ok": True, **status})
        elif route == "/api/simulate/press":
            ok = self.app.run_action_for_state("PRESSED")
            self.send_json({"ok": ok, **self.app.simulation_status()})
        elif route == "/api/simulate/release":
            ok = self.app.run_action_for_state("RELEASED")
            self.send_json({"ok": ok, **self.app.simulation_status()})
        elif route == "/api/hardware/beep":
            ok = self.app.run_hardware_command("beep")
            self.send_json({"ok": ok})
        elif route == "/api/hardware/ring_on":
            ok = self.app.run_hardware_command("ring_on")
            self.send_json({"ok": ok})
        elif route == "/api/hardware/ring_off":
            ok = self.app.run_hardware_command("ring_off")
            self.send_json({"ok": ok})
        elif route == "/api/hardware/led_on":
            ok = self.app.run_hardware_command("led_on")
            self.send_json({"ok": ok})
        elif route == "/api/hardware/led_off":
            ok = self.app.run_hardware_command("led_off")
            self.send_json({"ok": ok})
        elif route == "/api/hardware/pins":
            data = self.read_json()
            hook_pin = int(data.get("hook_pin", 0))
            buzzer_pin = int(data.get("buzzer_pin", 21))
            led_pin = int(data.get("led_pin", 20))
            ok = self.app.set_test_pins(hook_pin, buzzer_pin, led_pin)
            self.send_json({"ok": ok, "hook_pin": hook_pin, "buzzer_pin": buzzer_pin, "led_pin": led_pin})
        else:
            self.send_error(404)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def send_json(self, payload: dict[str, Any]) -> None:
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def send_bytes(self, payload: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def handle_events(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        subscriber = self.app.subscribe()
        try:
            self.write_sse({"type": "snapshot", **self.app.snapshot()})
            while True:
                try:
                    event = subscriber.get(timeout=15)
                    self.write_sse(event)
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            self.app.unsubscribe(subscriber)

    def write_sse(self, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False)
        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
        self.wfile.flush()


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def make_server(host: str, web_port: int, app: AppState) -> ThreadingHTTPServer:
    class BoundConsoleHandler(ConsoleHandler):
        pass

    BoundConsoleHandler.app = app
    return QuietThreadingHTTPServer((host, web_port), BoundConsoleHandler)


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Desk Phone 本地网页控制台。")
    parser.add_argument("--port", help="优先使用的 ESP32 串口；不填则持续自动扫描。")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--udp-port", type=int, default=DEFAULT_UDP_TELEMETRY_PORT)
    parser.add_argument("--device-command-port", type=int, default=DEFAULT_UDP_COMMAND_PORT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--no-serial", action="store_true", help="只启动网页，不打开串口。")
    parser.add_argument("--no-actions", action="store_true", help="只记录动作，不发送 Windows 快捷键。")
    parser.add_argument("--no-simulation", action="store_true", help="关闭本机模拟发送端，只使用真实 ESP32 数据。")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.no_actions:
        config.enable_actions = False

    app = AppState(config, args.config, simulation_enabled=not args.no_simulation)
    stop = threading.Event()
    preferred_port = normalize_port_name(args.port)

    if not args.no_simulation:
        app.add_state_log("本机模拟发送端已启动：没有 ESP32 时也会生成稳定 GPIO/Wi-Fi 样本。")
        app.emit_simulated_sample("PRESSED", "启动默认状态")

    simulation_thread = threading.Thread(target=simulation_worker, args=(app, stop), daemon=True)
    simulation_thread.start()

    udp_thread = threading.Thread(
        target=udp_worker,
        args=(app, args.udp_port, args.device_command_port, stop),
        daemon=True,
    )
    udp_thread.start()

    if args.no_serial:
        app.add_state_log("已按 --no-serial 启动，页面只用于调试配置和动作。")
    else:
        if preferred_port:
            app.add_state_log(f"串口优先使用 {preferred_port}；不可用时会继续扫描其他 USB 串口。")
        else:
            app.add_state_log("串口自动扫描已启动；插入或重插 ESP32-C3 后会自动连接。")
        thread = threading.Thread(target=serial_worker, args=(app, preferred_port, args.baud, stop), daemon=True)
        thread.start()

    server = make_server(args.host, args.web_port, app)
    url = f"http://localhost:{args.web_port}"
    print(f"AI Desk Phone 本地控制台已启动：{url}")
    print("按 Ctrl+C 停止。")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
