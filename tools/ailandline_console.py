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
DEFAULT_CONFIG_PATH = ROOT / "config" / "ailandline_console.json"
DEFAULT_WEB_PORT = 8765
DEFAULT_BAUD = 115200


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


@dataclass
class StateEvent:
    from_state: str
    to_state: str
    sample: SensorSample
    reason: str


@dataclass
class ConsoleConfig:
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

        if isinstance(payload, dict) and "adc" in payload and "digital" in payload:
            return SensorSample(
                ms=int(payload.get("ms", now_ms)),
                adc=int(payload["adc"]),
                digital=normalize_digital(payload["digital"]),
                raw_line=raw_line,
                firmware_state=payload.get("state"),
                score=payload.get("score"),
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
        self.serial_lock = threading.Lock()

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

    def attach_serial(self, ser: Any) -> None:
        with self.serial_lock:
            self.serial_handle = ser
        self.publish({"type": "serial_status", "serial_connected": True})

    def detach_serial(self, ser: Any) -> None:
        disconnected = False
        with self.serial_lock:
            if self.serial_handle is ser:
                self.serial_handle = None
                disconnected = True
        if disconnected:
            self.publish({"type": "serial_status", "serial_connected": False})

    def is_serial_connected(self) -> bool:
        with self.serial_lock:
            return self.serial_handle is not None

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
        state = sample.firmware_state or self.last_state
        if sample.firmware_state:
            self.last_state = sample.firmware_state
        payload = {
            "ms": sample.ms,
            "adc": sample.adc,
            "digital": sample.digital,
            "firmware_state": sample.firmware_state,
            "python_state": state,
            "score": sample.score,
        }
        with self.lock:
            self.current_sample = payload
            self.samples.append(payload)
        self.publish({"type": "sample", "sample": payload})

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
            }

    def handle_sample(self, sample: SensorSample) -> None:
        self.add_sample(sample)

    def run_action_for_state(self, state: str) -> None:
        command_type = "simulate_press" if state == "PRESSED" else "simulate_release"
        command = json.dumps({"type": command_type}, separators=(",", ":"))
        if self.send_serial_command(command):
            self.add_action_log(f"已发送板子模拟命令：{command_type}")


def timestamped(text: str) -> str:
    return f"{time.strftime('%H:%M:%S')} {text}"


def find_default_port() -> str | None:
    if list_ports is None:
        return None

    ports = list(list_ports.comports())
    for port in ports:
        if port.vid == 0x303A and port.pid == 0x1001:
            return port.device

    for port in ports:
        if port.device.upper() != "COM1":
            return port.device

    return None


def handle_board_line(app: AppState, line: str) -> None:
    sample = parse_serial_line(line)
    if sample is not None:
        app.handle_sample(sample)
        return

    if not line.startswith("{"):
        return

    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return

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
    elif event_type == "error":
        app.add_state_log(f"ESP32 错误：{payload.get('message')}")
    elif event_type == "hello":
        app.add_state_log(f"ESP32 固件：{payload.get('version')}")


def serial_worker(app: AppState, port: str, baud: int, stop: threading.Event) -> None:
    if serial is None:
        app.add_state_log("缺少 pyserial，无法读取串口。")
        return

    while not stop.is_set():
        try:
            app.add_state_log(f"正在打开 {port}，波特率 {baud}。")
            with serial.Serial(port, baud, timeout=0.2, write_timeout=1.0) as ser:
                ser.dtr = False
                ser.rts = False
                app.attach_serial(ser)
                app.add_state_log(f"{port} 已连接，开始读取传感器日志。")
                app.send_serial_command(json.dumps({"type": "get_config"}, separators=(",", ":")))

                try:
                    while not stop.is_set():
                        raw = ser.readline()
                        if not raw:
                            continue

                        line = raw.decode("utf-8", errors="replace").strip()
                        app.add_raw_log(line)
                        handle_board_line(app, line)
                finally:
                    app.detach_serial(ser)
        except Exception as exc:  # pragma: no cover - hardware/environment dependent
            app.add_state_log(f"串口读取失败：{exc}")
            time.sleep(2)


INDEX_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AiLandLine 本地控制台</title>
  <style>
    :root {
      color-scheme: light;
      font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
      --bg: #f4f7fb;
      --panel: #ffffff;
      --border: #d8e0ec;
      --text: #152033;
      --muted: #64748b;
      --accent: #1268d6;
      --good: #0f8a5f;
      --warn: #b7791f;
      --dark: #101827;
    }
    * { box-sizing: border-box; }
    body { margin: 0; background: var(--bg); color: var(--text); }
    header {
      display: flex; justify-content: space-between; align-items: center; gap: 16px;
      padding: 14px 18px; border-bottom: 1px solid var(--border); background: #fff;
      position: sticky; top: 0; z-index: 2;
    }
    h1 { font-size: 20px; margin: 0; }
    h2 { font-size: 15px; margin: 0 0 10px; }
    main {
      --config-width: 420px;
      position: relative; display: block; padding: 16px;
      padding-right: calc(var(--config-width) + 32px);
    }
    section { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 14px; }
    main > section:nth-of-type(1) { margin-bottom: 16px; }
    main > section:nth-of-type(2) {
      position: absolute; top: 16px; right: 16px; width: var(--config-width);
    }
    .status { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
    .pill { padding: 5px 9px; border-radius: 999px; font-size: 12px; background: #eef4ff; color: #1d4e89; }
    .pill.good { background: #e1f7ed; color: #0f6b49; }
    .pill.warn { background: #fff4d6; color: #8a5d0a; }
    .grid3 { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-bottom: 12px; }
    .metric { border: 1px solid var(--border); border-radius: 8px; padding: 12px; min-height: 94px; }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 7px; }
    .value { font-size: 30px; line-height: 1.1; font-weight: 750; word-break: break-word; }
    .state-pressed { color: var(--good); }
    .state-released { color: var(--warn); }
    canvas { width: 100%; height: 220px; border: 1px solid var(--border); border-radius: 8px; background: #fbfdff; }
    label { display: grid; gap: 6px; font-size: 13px; color: #263448; }
    input, select {
      width: 100%; height: 34px; border: 1px solid #cbd5e1; border-radius: 6px;
      padding: 6px 9px; font: inherit; background: #fff; color: var(--text);
    }
    input[type="checkbox"] { width: auto; height: auto; }
    .form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
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
    .help-list { grid-column: 1 / -1; display: grid; gap: 8px; border-top: 1px solid var(--border); padding-top: 12px; }
    .help-item { display: grid; grid-template-columns: 120px 1fr; gap: 10px; color: var(--muted); font-size: 12px; line-height: 1.45; }
    .help-item strong { color: #263448; font-size: 12px; }
    .buttons { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 12px; }
    .save-status { margin-top: 0; min-height: 34px; display: inline-flex; align-items: center; }
    .save-status.ok { color: var(--good); }
    .save-status.warn { color: var(--warn); }
    button {
      border: 1px solid #b8c4d6; border-radius: 6px; background: #fff; color: #132033;
      padding: 8px 10px; cursor: pointer; font: inherit;
    }
    button.primary { background: var(--accent); color: #fff; border-color: var(--accent); }
    .logs { padding: 0; overflow: hidden; }
    .log-head { display:flex; justify-content:space-between; align-items:center; gap: 12px; padding: 12px 14px; }
    .log-grid { display: grid; grid-template-columns: 1.35fr 1fr 1fr; gap: 1px; background: #243145; }
    pre {
      margin: 0; min-height: 250px; max-height: 360px; overflow: auto; padding: 12px;
      background: var(--dark); color: #dbeafe; white-space: pre-wrap; font: 12px/1.45 Consolas, monospace;
    }
    .hint { color: var(--muted); font-size: 12px; margin-top: 6px; }
    @media (max-width: 980px) {
      main {
        display: grid; grid-template-columns: 1fr; gap: 16px; padding: 16px;
      }
      main > section:nth-of-type(1) { margin-bottom: 0; }
      main > section:nth-of-type(2) { position: static; width: auto; }
      .log-grid, .grid3, .form-grid, .action-row, .help-item { grid-template-columns: 1fr; }
      .form-wide { grid-column: auto; }
    }
  </style>
</head>
<body>
  <header>
    <h1>AiLandLine 本地控制台</h1>
    <div class="status">
      <span id="conn" class="pill">正在连接服务</span>
      <span id="serialStatus" class="pill warn">串口未连接</span>
      <span id="lastSample" class="pill">暂无数据</span>
    </div>
  </header>

  <main>
    <section>
      <h2>实时输入</h2>
      <div class="grid3">
        <div class="metric"><div class="label">ADC 数值</div><div id="adcValue" class="value">--</div></div>
        <div class="metric"><div class="label">Digital 状态</div><div id="digitalValue" class="value">--</div></div>
        <div class="metric"><div class="label">板子判定</div><div id="stateValue" class="value">--</div></div>
      </div>
      <canvas id="chart" width="900" height="240"></canvas>
      <div class="hint">曲线使用 ESP32 回传的 ADC 数据绘制；阈值线来自右侧当前配置。</div>
    </section>

    <section>
      <h2>判定参数与动作</h2>
      <div class="form-grid">
        <label>ADC 极性
          <select id="adc_low_means_pressed">
            <option value="true">低 ADC = 按下</option>
            <option value="false">高 ADC = 按下</option>
          </select>
        </label>
        <label>动作执行
          <select id="enable_actions">
            <option value="true">开启</option>
            <option value="false">只记录日志</option>
          </select>
        </label>
        <label>按下阈值 <input id="press_threshold" type="number"></label>
        <label>释放阈值 <input id="release_threshold" type="number"></label>
        <label>强按下低阈值 <input id="strong_low_press_threshold" type="number"></label>
        <label>强按下高阈值 <input id="strong_high_press_threshold" type="number"></label>
        <label>消抖时间（毫秒） <input id="debounce_ms" type="number"></label>
        <label>按下锁定（毫秒） <input id="press_lockout_ms" type="number"></label>
        <input id="press_action_text" type="hidden">
        <input id="release_action_text" type="hidden">
        <div class="form-wide preset-row">
          <div class="label">动作预设</div>
          <div class="preset-actions">
            <button type="button" onclick="applyPreset('current')">套用方案一：当前配置</button>
            <button type="button" onclick="applyPreset('voice_call')">套用方案二：语音通话键</button>
          </div>
          <div class="hint">套用后点击“保存配置”，会通过 USB 写入 ESP32；之后断开 USB 也会按最后保存的配置执行。</div>
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
        <div class="help-list">
          <div class="help-item"><strong>按下阈值</strong><span>低 ADC 模式下，ADC 小于等于这个值会累计“按下”分数；高 ADC 模式则相反。</span></div>
          <div class="help-item"><strong>释放阈值</strong><span>低 ADC 模式下，ADC 大于等于这个值会释放；它应和按下阈值拉开距离，避免抖动。</span></div>
          <div class="help-item"><strong>强按下阈值</strong><span>用于识别更明显的一次按下，命中后分数增加更快。</span></div>
          <div class="help-item"><strong>消抖/锁定</strong><span>消抖过滤瞬间跳变；按下锁定防止一次拿起或放下被连续触发多次。</span></div>
        </div>
      </div>
      <div class="buttons">
        <button class="primary" onclick="saveConfig()">保存配置</button>
        <button onclick="postAction('/api/simulate/press')">模拟按下</button>
        <button onclick="postAction('/api/simulate/release')">模拟释放</button>
        <button onclick="clearLogs()">清空日志</button>
        <span id="saveStatus" class="hint save-status">尚未保存本次修改</span>
      </div>
      <div class="hint">快捷键支持 Ctrl、Windows、Shift、Alt、字母、数字、Enter、Space、Tab、Esc。浏览器可能无法稳定捕获 Windows 键，方案一已内置。</div>
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

    function $(id) { return document.getElementById(id); }

    function pushLog(target, line) {
      target.push(line);
      while (target.length > maxLogLines) target.shift();
    }

    function setSerialStatus(isConnected) {
      const node = $("serialStatus");
      node.textContent = isConnected ? "串口已连接" : "串口未连接";
      node.className = isConnected ? "pill good" : "pill warn";
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
      setActionEditor("press", config.press_action_text || "");
      setActionEditor("release", config.release_action_text || "");
    }

    function getConfigForm() {
      const numeric = [
        "press_threshold", "release_threshold", "strong_low_press_threshold",
        "strong_high_press_threshold", "debounce_ms", "press_lockout_ms"
      ];
      const next = {...config};
      for (const key of numeric) next[key] = Number($(key).value);
      next.adc_low_means_pressed = $("adc_low_means_pressed").value === "true";
      next.enable_actions = $("enable_actions").value === "true";
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

    async function loadConfig() {
      const response = await fetch("/api/config");
      setConfigForm(await response.json());
      drawChart();
    }

    async function saveConfig() {
      setSaveStatus("正在保存配置...");
      try {
        const response = await fetch("/api/config", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(getConfigForm())
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        setConfigForm(await response.json());
        drawChart();
        setSaveStatus("配置已保存，查看日志确认板子是否写入。", "ok");
      } catch (error) {
        setSaveStatus(`保存失败：${error.message}`, "warn");
      }
    }

    async function postAction(url) {
      await fetch(url, {method: "POST"});
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
      link.download = "ailandline-console-log.txt";
      link.click();
      URL.revokeObjectURL(link.href);
    }

    function updateSample(sample) {
      $("adcValue").textContent = sample.adc;
      $("digitalValue").textContent = sample.digital;
      $("stateValue").textContent = sample.python_state === "PRESSED" ? "按下" : "释放";
      $("stateValue").className = "value " + (sample.python_state === "PRESSED" ? "state-pressed" : "state-released");
      $("lastSample").textContent = `ADC ${sample.adc} / 分数 ${sample.score}`;
      samples.push(sample);
      while (samples.length > 180) samples.shift();
      drawChart();
    }

    function drawChart() {
      const canvas = $("chart");
      const ctx = canvas.getContext("2d");
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = "#fbfdff";
      ctx.fillRect(0, 0, w, h);
      ctx.strokeStyle = "#e2e8f0";
      ctx.lineWidth = 1;
      for (let i = 1; i < 5; i++) {
        const y = (h / 5) * i;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
      }
      const maxAdc = Math.max(140, ...samples.map(s => s.adc), Number(config.release_threshold || 0) + 20);
      function yFor(adc) { return h - Math.max(0, Math.min(1, adc / maxAdc)) * (h - 24) - 12; }
      function thresholdLine(value, color, text) {
        if (!value) return;
        const y = yFor(Number(value));
        ctx.setLineDash([8, 7]);
        ctx.strokeStyle = color;
        ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(w, y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.fillText(text, w - 170, y - 6);
      }
      ctx.font = "13px Microsoft YaHei, sans-serif";
      thresholdLine(config.press_threshold, "#0f8a5f", `按下阈值 ${config.press_threshold}`);
      thresholdLine(config.release_threshold, "#b7791f", `释放阈值 ${config.release_threshold}`);
      if (samples.length < 2) return;
      ctx.strokeStyle = "#1268d6";
      ctx.lineWidth = 3;
      ctx.beginPath();
      samples.forEach((sample, index) => {
        const x = samples.length === 1 ? 0 : (w * index) / (samples.length - 1);
        const y = yFor(sample.adc);
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
    }

    function connectEvents() {
      const events = new EventSource("/events");
      events.onopen = () => {
        $("conn").textContent = "服务已连接";
        $("conn").className = "pill good";
      };
      events.onerror = () => {
        $("conn").textContent = "服务断开，正在重连";
        $("conn").className = "pill";
      };
      events.onmessage = event => {
        const payload = JSON.parse(event.data);
        if (payload.type === "snapshot") {
          setConfigForm(payload.config);
          setSerialStatus(payload.serial_connected);
          samples = payload.samples || [];
          rawLogs.splice(0, rawLogs.length, ...(payload.raw_logs || []));
          stateLogs.splice(0, stateLogs.length, ...(payload.state_logs || []));
          actionLogs.splice(0, actionLogs.length, ...(payload.action_logs || []));
          if (payload.current_sample) updateSample(payload.current_sample);
          renderLogs();
          drawChart();
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
          setSerialStatus(payload.serial_connected);
        } else if (payload.type === "action_log") {
          pushLog(actionLogs, payload.text);
          renderLogs();
        } else if (payload.type === "sample") {
          updateSample(payload.sample);
        }
      };
    }

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
            self.app.update_config(config)
            self.send_json(config.to_dict())
        elif route == "/api/simulate/press":
            self.app.add_state_log("手动模拟按下。")
            self.app.run_action_for_state("PRESSED")
            self.send_json({"ok": True})
        elif route == "/api/simulate/release":
            self.app.add_state_log("手动模拟释放。")
            self.app.run_action_for_state("RELEASED")
            self.send_json({"ok": True})
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
    parser = argparse.ArgumentParser(description="AiLandLine 本地网页控制台。")
    parser.add_argument("--port", help="ESP32 串口，默认自动查找。")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--no-serial", action="store_true", help="只启动网页，不打开串口。")
    parser.add_argument("--no-actions", action="store_true", help="只记录动作，不发送 Windows 快捷键。")
    args = parser.parse_args()

    config = load_config(args.config)
    if args.no_actions:
        config.enable_actions = False

    app = AppState(config, args.config)
    stop = threading.Event()
    port = args.port or find_default_port()

    if args.no_serial:
        app.add_state_log("已按 --no-serial 启动，页面只用于调试配置和动作。")
    elif port:
        thread = threading.Thread(target=serial_worker, args=(app, port, args.baud, stop), daemon=True)
        thread.start()
    else:
        app.add_state_log("没有找到 ESP32 串口，页面仍可打开，但没有实时数据。")

    server = make_server(args.host, args.web_port, app)
    url = f"http://localhost:{args.web_port}"
    print(f"AiLandLine 本地控制台已启动：{url}")
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
