from __future__ import annotations

import argparse
import ctypes
from collections import deque
from dataclasses import asdict, dataclass, field
import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import queue
import re
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import serial
    from serial.tools import list_ports
except ImportError:  # pragma: no cover - exercised on machines without pyserial
    serial = None
    list_ports = None


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
        "label": "方案一：接线员模式",
        "description": "文字输入任务完成后，按 1 秒响、4 秒停循环提醒；摘机后停止。",
    },
    "doubao": {
        "label": "方案二：豆包语音",
        "description": "抬起电话后进入语音报告或全双工对话；当前先保留模式入口。",
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
    def __init__(self, config: ConsoleConfig, config_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        self.machine = HookStateMachine(config)
        self.sender = WindowsHotkeySender()
        self.lock = threading.Lock()
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
        self.pending_report_text: str | None = None

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

    def add_sample(self, sample: SensorSample) -> None:
        state = self.interpreted_state_for_sample(sample)
        if state:
            self.last_state = state
        with self.lock:
            should_clear_alert = self.alerting and state == "RELEASED"
        if should_clear_alert:
            self.clear_ai_alert("摘机接听")

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
        if self.send_serial_command(build_device_config_command(config)):
            self.add_state_log("配置已保存到电脑，并已发送给 ESP32 写入板子。")
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
            }

    def handle_sample(self, sample: SensorSample) -> None:
        self.add_sample(sample)

    def run_action_for_state(self, state: str) -> None:
        command_type = "simulate_press" if state == "PRESSED" else "simulate_release"
        command = json.dumps({"type": command_type}, separators=(",", ":"))
        if self.send_serial_command(command):
            self.add_action_log(f"已发送板子模拟命令：{command_type}")

    def has_hardware_link(self) -> bool:
        with self.udp_lock:
            udp_ready = self.udp_socket is not None and self.udp_device_address is not None
        return udp_ready or self.is_serial_connected()

    def run_hardware_command(self, command: str, *, log: bool = True) -> bool:
        if self.send_device_command(command):
            if log:
                self.add_action_log(f"硬件测试命令：{command}")
            return True
        return False

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
        self.run_hardware_command("led_on", log=False)
        self.add_action_log("接线员模式已启动：1 秒响、4 秒停；摘机后停止，90 秒无人接听后切忙音。")
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
            self.pending_report_text = None
            stop_event = self.alert_stop_event
        stop_event.set()
        self.publish_alert_status()
        buzzer_ok = self.run_hardware_command("ring_off")
        led_ok = self.run_hardware_command("led_off")
        self.add_action_log(f"接线员提醒已停止：{reason}")
        return was_alerting or buzzer_ok or led_ok

    def run_ai_hook_signal(self, source: str = "ai", text: str | None = None) -> bool:
        del text
        source = (source or "ai").strip() or "ai"
        if not self.has_hardware_link():
            self.add_action_log(f"接线员 hook 触发失败：没有可用的 ESP32 Wi-Fi 或串口链路（{source}）。")
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
            self.pending_report_text = None
        self.publish_alert_status()
        alert_thread.start()
        self.add_action_log(f"接线员 hook 已收到：{source}，开始 1 秒响、4 秒停。")
        return True

    def set_test_pins(self, hook_pin: int, buzzer_pin: int, led_pin: int = 20) -> bool:
        command = json.dumps(
            {"type": "set_pins", "hook_pin": hook_pin, "buzzer_pin": buzzer_pin, "led_pin": led_pin},
            separators=(",", ":"),
        )
        if self.send_device_command(command):
            self.add_action_log(f"测试引脚已发送：开关 GPIO{hook_pin}，蜂鸣器 GPIO{buzzer_pin}，LED GPIO{led_pin}")
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


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Desk Phone 本地控制台</title>
  <style>
    :root {
      color-scheme: light;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
      --bg: #f5f6f8;
      --panel: #ffffff;
      --panel-soft: #f8fafc;
      --border: #d9e0ea;
      --border-strong: #b9c3d1;
      --text: #172033;
      --muted: #667085;
      --accent: #1f6fd1;
      --good: #0f7a5f;
      --warn: #a76510;
      --danger: #b42318;
      --dark: #111827;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); font-size: 14px; }
    header {
      display: grid; grid-template-columns: minmax(280px, 1fr) auto; gap: 24px; align-items: start;
      padding: 16px 20px; border-bottom: 1px solid var(--border); background: #fff;
      position: sticky; top: 0; z-index: 2;
    }
    h1 { font-size: 20px; margin: 0 0 4px; line-height: 1.25; }
    h2 { font-size: 16px; margin: 0; line-height: 1.3; }
    h3 { font-size: 14px; margin: 0 0 10px; line-height: 1.3; }
    .subtitle { margin: 0; color: var(--muted); line-height: 1.45; }
    main {
      display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(360px, 0.85fr);
      gap: 16px; padding: 16px;
    }
    .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 16px; }
    .panel-header { display: flex; justify-content: space-between; gap: 16px; align-items: start; margin-bottom: 14px; }
    .panel-note { margin: 4px 0 0; color: var(--muted); line-height: 1.45; }
    .status { display: grid; grid-template-columns: repeat(4, max-content); gap: 8px 18px; align-items: baseline; font-size: 12px; color: var(--muted); }
    .status-item { white-space: nowrap; }
    .status-value { color: var(--text); font-weight: 700; }
    .status-value.good { color: var(--good); }
    .status-value.warn { color: var(--warn); }
    .readout-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
    .metric { border: 1px solid var(--border); border-radius: 8px; padding: 12px; min-height: 92px; background: #fff; }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 7px; line-height: 1.35; }
    .value { font-size: 30px; line-height: 1.1; font-weight: 750; word-break: break-word; }
    .state-pressed { color: var(--good); }
    .state-released { color: var(--warn); }
    .state-line { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin: 12px 0 0; }
    .state-line div { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; background: var(--panel-soft); }
    .state-line strong { display: block; margin-top: 4px; color: var(--text); font-size: 16px; }
    canvas { width: 100%; height: 220px; border: 1px solid var(--border); border-radius: 8px; background: #fbfdff; display: block; }
    #digitalChart { height: 160px; margin-top: 12px; }
    .section-divider { margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border); }
    .control-block { border: 1px solid var(--border); border-radius: 8px; padding: 12px; background: #fff; }
    .control-block + .control-block { margin-top: 12px; }
    .mode-row { display: grid; gap: 10px; margin-bottom: 10px; }
    .segmented { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); border: 1px solid var(--border-strong); border-radius: 7px; overflow: hidden; background: #fff; }
    .segmented button { border: 0; border-radius: 0; border-right: 1px solid var(--border); min-height: 38px; }
    .segmented button:last-child { border-right: 0; }
    .segmented button.active { background: var(--accent); color: #fff; }
    .mode-hint, .callout { color: var(--muted); font-size: 12px; line-height: 1.5; min-height: 18px; }
    .callout { border-left: 3px solid var(--accent); padding: 8px 10px; background: var(--panel-soft); }
    .pin-grid, .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
    .pin-grid { grid-template-columns: repeat(3, minmax(0, 1fr)); }
    .button-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 10px; }
    label { display: grid; gap: 6px; font-size: 13px; color: #263448; }
    input, select, textarea {
      width: 100%; min-height: 34px; border: 1px solid #cbd5e1; border-radius: 6px;
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
      width: 100%; min-height: 34px; text-align: left; background: #f8fafc;
      display: flex; justify-content: space-between; align-items: center; gap: 10px;
    }
    .capture::after { content: "录入"; color: var(--muted); font-size: 12px; }
    .capture.active { border-color: var(--accent); box-shadow: 0 0 0 2px rgba(18, 104, 214, 0.14); }
    .capture.active::after { content: "按键中"; color: var(--accent); }
    .buttons { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 12px; }
    .save-status { margin-top: 0; min-height: 34px; display: inline-flex; align-items: center; }
    .save-status.ok { color: var(--good); }
    .save-status.warn { color: var(--warn); }
    button {
      border: 1px solid #b8c4d6; border-radius: 6px; background: #fff; color: #132033;
      padding: 8px 10px; cursor: pointer; font: inherit; min-height: 36px;
    }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    button.danger { border-color: #e6b2ad; color: var(--danger); }
    details { border-top: 1px solid var(--border); padding-top: 10px; }
    details + details { margin-top: 12px; }
    summary { cursor: pointer; font-weight: 700; margin-bottom: 10px; }
    .logs { padding: 0; overflow: hidden; grid-column: 1 / -1; }
    .log-head { display:flex; justify-content:space-between; align-items:center; gap: 12px; padding: 12px 14px; }
    .log-grid { display: grid; grid-template-columns: 1.35fr 1fr 1fr; gap: 1px; background: #243145; }
    pre {
      margin: 0; min-height: 250px; max-height: 360px; overflow: auto; padding: 12px;
      background: var(--dark); color: #dbeafe; white-space: pre-wrap; font: 12px/1.45 Consolas, monospace;
    }
    .hint { color: var(--muted); font-size: 12px; margin-top: 6px; }
    .muted { color: var(--muted); }
    @media (max-width: 980px) {
      header { grid-template-columns: 1fr; }
      .status { grid-template-columns: 1fr 1fr; }
      main { grid-template-columns: 1fr; padding: 12px; }
      .log-grid, .readout-grid, .state-line, .form-grid, .action-row, .pin-grid { grid-template-columns: 1fr; }
      .form-wide { grid-column: auto; }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>HG113 AI Desk Phone 控制台</h1>
      <p class="subtitle">电脑端负责页面、波形和业务逻辑；ESP32-C3 只上报 GPIO 状态并执行蜂鸣器、LED 命令。</p>
    </div>
    <div class="status">
      <div class="status-item">服务 <span id="conn" class="status-value">连接中</span></div>
      <div class="status-item">串口 <span id="serialStatus" class="status-value warn">扫描中</span></div>
      <div class="status-item">设备 <span id="deviceStatus" class="status-value">未发现</span></div>
      <div class="status-item">最新 <span id="lastSample" class="status-value">暂无数据</span></div>
    </div>
  </header>

  <main>
    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>电话状态与波形</h2>
          <p class="panel-note">先看这里：状态是否跳变、波形是否稳定、当前提醒是否已经触发。</p>
        </div>
      </div>
      <div class="readout-grid">
        <div class="metric"><div class="label">ADC 数值</div><div id="adcValue" class="value">--</div></div>
        <div class="metric"><div class="label">Digital 状态</div><div id="digitalValue" class="value">--</div></div>
        <div class="metric"><div class="label">解释状态</div><div id="stateValue" class="value">--</div></div>
      </div>
      <canvas id="digitalChart" width="900" height="170"></canvas>
      <div class="hint">拨动摘挂机开关时，数字波形应该在 HIGH 和 LOW 之间跳变。</div>
      <div class="state-line">
        <div>接线员<strong id="alertState">未触发</strong></div>
        <div>蜂鸣器<strong id="buzzerState">未知</strong></div>
        <div>LED<strong id="ledState">未知</strong></div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>方案与测试</h2>
          <p class="panel-note">先选业务模式，再调 GPIO 和判定方向。</p>
        </div>
      </div>

      <div class="control-block">
        <h3>业务模式</h3>
        <input id="business_mode" type="hidden" value="codex">
        <div class="segmented" aria-label="业务模式">
          <button id="modeCodexBtn" type="button" onclick="selectBusinessMode('codex')">接线员模式</button>
          <button id="modeDoubaoBtn" type="button" onclick="selectBusinessMode('doubao')">豆包聊天</button>
        </div>
        <div id="businessModeHint" class="callout">文字输入任务完成后，电话按 1 秒响、4 秒停循环提醒；摘机后停止。</div>
        <div class="button-row">
          <button class="primary" onclick="postAiHook()">触发接线员提醒</button>
          <button onclick="clearAiAlert()">停止提醒</button>
        </div>
      </div>

      <div class="control-block">
        <h3>摘挂机判定</h3>
        <input id="hook_scheme" type="hidden" value="scheme1">
        <div class="segmented" aria-label="开关判定方案">
          <button id="scheme1Btn" type="button" onclick="selectHookScheme('scheme1')">方案 1</button>
          <button id="scheme2Btn" type="button" onclick="selectHookScheme('scheme2')">方案 2</button>
        </div>
        <div id="hookSchemeHint" class="mode-hint">方案 1：HIGH = 按下，LOW = 抬起</div>
      </div>

      <div class="control-block">
        <h3>硬件测试</h3>
        <div class="pin-grid">
          <label>开关 GPIO
            <input id="hookPinInput" type="number" min="0" max="48" value="0">
          </label>
          <label>蜂鸣器 GPIO
            <input id="buzzerPinInput" type="number" min="0" max="48" value="21">
          </label>
          <label>LED GPIO
            <input id="ledPinInput" type="number" min="0" max="48" value="20">
          </label>
        </div>
        <div class="button-row">
          <button onclick="applyHardwarePins()">应用引脚</button>
          <button class="primary" onclick="postHardwareCommand('beep')">响一下</button>
          <button onclick="postHardwareCommand('ring_on')">持续响</button>
          <button class="danger" onclick="postHardwareCommand('ring_off')">停止响</button>
          <button onclick="postHardwareCommand('led_on')">LED 亮</button>
          <button onclick="postHardwareCommand('led_off')">LED 灭</button>
        </div>
      </div>

      <details>
        <summary>动作配置</summary>
        <div class="form-grid">
          <label class="form-wide">动作执行
            <select id="enable_actions">
              <option value="true">开启</option>
              <option value="false">只记录日志</option>
            </select>
          </label>
          <input id="press_action_text" type="hidden">
          <input id="release_action_text" type="hidden">
          <div class="form-wide preset-row">
            <div class="label">动作预设</div>
            <div class="preset-actions">
              <button type="button" onclick="applyPreset('current')">套用方案一</button>
              <button type="button" onclick="applyPreset('voice_call')">套用方案二</button>
            </div>
            <div class="hint">套用后点击“保存配置”，会通过 USB 写入 ESP32；Wi-Fi 通信仍由电脑端页面负责。</div>
          </div>
          <div class="action-editor">
            <div class="action-title">按下动作</div>
            <div class="action-row">
              <label>第一段快捷键
                <button id="press_primary_hotkey_button" class="capture" type="button" onclick="startHotkeyCapture('press_primary_hotkey')">点击后按键</button>
                <input id="press_primary_hotkey" type="hidden">
              </label>
              <label>延迟（毫秒）
                <input id="press_delay_ms" type="number" min="0" step="50">
              </label>
              <label>延迟后按键（可选）
                <button id="press_follow_hotkey_button" class="capture" type="button" onclick="startHotkeyCapture('press_follow_hotkey')">无</button>
                <input id="press_follow_hotkey" type="hidden">
              </label>
            </div>
          </div>
          <div class="action-editor">
            <div class="action-title">释放动作</div>
            <div class="action-row">
              <label>第一段快捷键
                <button id="release_primary_hotkey_button" class="capture" type="button" onclick="startHotkeyCapture('release_primary_hotkey')">点击后按键</button>
                <input id="release_primary_hotkey" type="hidden">
              </label>
              <label>延迟（毫秒）
                <input id="release_delay_ms" type="number" min="0" step="50">
              </label>
              <label>延迟后按键（可选）
                <button id="release_follow_hotkey_button" class="capture" type="button" onclick="startHotkeyCapture('release_follow_hotkey')">无</button>
                <input id="release_follow_hotkey" type="hidden">
              </label>
            </div>
          </div>
        </div>
      </details>

      <div class="buttons">
        <button class="primary" onclick="saveConfig()">保存配置</button>
        <button onclick="postAction('/api/simulate/press')">模拟按下</button>
        <button onclick="postAction('/api/simulate/release')">模拟释放</button>
        <button onclick="clearLogs()">清空日志</button>
      </div>
      <div id="saveStatus" class="hint save-status">尚未保存本次修改</div>
      <div class="hint">快捷键支持 Ctrl、Windows、Shift、Alt、字母、数字、Enter、Space、Tab、Esc。</div>
    </section>

    <section class="logs">
      <div class="log-head">
        <h2>实时日志</h2>
        <div class="buttons" style="margin:0">
          <button onclick="togglePause()" id="pauseBtn">暂停</button>
          <button onclick="exportLogs()">导出</button>
        </div>
      </div>
      <div class="log-grid">
        <pre id="rawLog">串口原始日志等待中...</pre>
        <pre id="stateLog">板子判定日志等待中...</pre>
        <pre id="actionLog">动作日志等待中...</pre>
      </div>
    </section>
  </main>

  <script>
    const maxLogLines = 220;
    let config = {};
    let samples = [];
    let paused = false;
    const rawLogs = [];
    const stateLogs = [];
    const actionLogs = [];
    const hookSchemeDescriptions = {
      scheme1: "方案 1：HIGH = 按下，LOW = 抬起",
      scheme2: "方案 2：LOW = 按下，HIGH = 抬起"
    };
    const businessModeDescriptions = {
      codex: "接线员模式：文字输入任务完成后，电话 1 秒响、4 秒停循环提醒；摘机后停止，约 90 秒无人接听切忙音。",
      doubao: "抬起电话后进入豆包语音报告或全双工对话；当前先保留模式入口。"
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
      setStatusValue("deviceStatus", device || "未发现", device ? "good" : "");
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
        setSaveStatus(result.ok ? "接线员提醒已触发：1 秒响、4 秒停，摘机后停止。" : "接线员提醒发送失败", result.ok ? "ok" : "warn");
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
      $("lastSample").textContent = sample.adc_synthetic
        ? `GPIO${sample.pin ?? ""} ${sample.digital}`.trim()
        : `ADC ${sample.adc}`;
      samples.push(sample);
      while (samples.length > 180) samples.shift();
      drawDigitalChart();
    }

    function drawDigitalChart() {
      const canvas = $("digitalChart");
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#fbfdff";
      ctx.fillRect(0, 0, w, h);

      ctx.strokeStyle = "#e2e8f0";
      ctx.lineWidth = 1;
      for (const y of [36, h - 36]) {
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      }

      ctx.font = "13px Microsoft YaHei, sans-serif";
      ctx.fillStyle = "#64748b";
      ctx.fillText("HIGH", 10, 28);
      ctx.fillText("LOW", 10, h - 18);
      ctx.fillText("GPIO 开关波形", w - 130, 28);

      if (samples.length < 2) return;
      const yFor = sample => (sample.digital === "LOW" || sample.digital_value === 0) ? h - 36 : 36;

      ctx.strokeStyle = "#0f8a5f";
      ctx.lineWidth = 3;
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


class ConsoleHandler(BaseHTTPRequestHandler):
    app: AppState

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/":
            self.send_bytes(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif route == "/api/config":
            self.send_json(self.app.config.to_dict())
        elif route == "/api/action-presets":
            self.send_json(action_presets())
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
            text = str(data.get("text", "") or "")
            ok = self.app.run_ai_hook_signal(source, text)
            self.send_json({"ok": ok, "source": source})
        elif route == "/api/alert/clear":
            data = self.read_json()
            reason = str(data.get("reason", "manual"))
            ok = self.app.clear_ai_alert(reason)
            self.send_json({"ok": ok})
        elif route == "/api/simulate/press":
            self.app.add_state_log("手动模拟按下。")
            self.app.run_action_for_state("PRESSED")
            self.send_json({"ok": True})
        elif route == "/api/simulate/release":
            self.app.add_state_log("手动模拟释放。")
            self.app.run_action_for_state("RELEASED")
            self.send_json({"ok": True})
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
    args = parser.parse_args()

    config = load_config(args.config)
    if args.no_actions:
        config.enable_actions = False

    app = AppState(config, args.config)
    stop = threading.Event()
    preferred_port = normalize_port_name(args.port)

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
