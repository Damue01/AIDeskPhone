from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_HOOK_URL = "http://127.0.0.1:8765/api/ai/hook"
DEFAULT_STATUS_PATH = "/api/replies"
SESSION_FALLBACK_LOOKBACK_SECONDS = 600
SESSION_FALLBACK_FILE_LIMIT = 80
TEXT_KEYS = (
    "text",
    "reply",
    "summary",
    "message",
    "final_message",
    "last_agent_message",
    "last_message",
    "assistant_message",
    "output",
    "content",
)


def normalized_path(value: str | os.PathLike[str] | None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return os.path.normcase(os.path.abspath(os.path.expanduser(text)))


def codex_home() -> Path:
    override = os.getenv("CODEX_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def read_stdin_payload() -> str:
    try:
        if sys.stdin is not None and not sys.stdin.closed and not sys.stdin.isatty():
            return sys.stdin.read(20_000)
    except OSError:
        return ""
    return ""


def status_url_for(hook_url: str) -> str:
    parsed = urllib.parse.urlparse(hook_url)
    return urllib.parse.urlunparse(parsed._replace(path=DEFAULT_STATUS_PATH, params="", query="", fragment=""))


def read_callback_enabled(hook_url: str) -> bool | None:
    if os.getenv("AI_DESK_PHONE_SKIP_CALLBACK_CHECK", "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    request = urllib.request.Request(status_url_for(hook_url), method="GET")
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            raw = response.read(20_000).decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and payload.get("callback_enabled") is False:
        return False
    return True


def text_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [text_from_value(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        for key in TEXT_KEYS:
            text = text_from_value(value.get(key))
            if text:
                return text
    return ""


def find_text(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in TEXT_KEYS:
            text = text_from_value(payload.get(key))
            if text:
                return text
        for value in payload.values():
            if isinstance(value, (dict, list)):
                text = find_text(value)
                if text:
                    return text
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, (dict, list)):
                text = find_text(item)
            if text:
                return text
    return ""


def parse_event_timestamp(value: Any, fallback: float) -> float:
    text = str(value or "").strip()
    if not text:
        return fallback
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return fallback


def task_complete_text(entry: Any) -> str:
    if not isinstance(entry, dict):
        return ""
    payload = entry.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "task_complete":
        return ""
    return text_from_value(payload.get("last_agent_message")) or text_from_value(payload)


def read_session_completion(path: Path) -> tuple[str, str, float]:
    session_cwd = ""
    latest_text = ""
    latest_at = path.stat().st_mtime
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", "", latest_at

    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("type") == "session_meta":
            payload = entry.get("payload")
            if isinstance(payload, dict):
                session_cwd = str(payload.get("cwd") or session_cwd)
        text = task_complete_text(entry)
        if text:
            latest_text = text
            latest_at = parse_event_timestamp(entry.get("timestamp"), path.stat().st_mtime)
    return session_cwd, latest_text, latest_at


def recent_session_completion_text(cwd: str | None = None) -> str:
    if os.getenv("AI_DESK_PHONE_DISABLE_SESSION_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}:
        return ""

    sessions_dir = codex_home() / "sessions"
    if not sessions_dir.exists():
        return ""

    now = time.time()
    try:
        lookback_seconds = int(os.getenv("AI_DESK_PHONE_SESSION_LOOKBACK_SECONDS", str(SESSION_FALLBACK_LOOKBACK_SECONDS)))
    except ValueError:
        lookback_seconds = SESSION_FALLBACK_LOOKBACK_SECONDS
    target_cwd = normalized_path(cwd)
    candidates: list[tuple[bool, float, str]] = []

    try:
        files = sorted(
            (path for path in sessions_dir.rglob("*.jsonl") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:SESSION_FALLBACK_FILE_LIMIT]
    except OSError:
        return ""

    for path in files:
        try:
            if lookback_seconds > 0 and now - path.stat().st_mtime > lookback_seconds:
                continue
            session_cwd, text, completed_at = read_session_completion(path)
        except OSError:
            continue
        if not text:
            continue
        cwd_matches = bool(target_cwd and normalized_path(session_cwd) == target_cwd)
        candidates.append((cwd_matches, completed_at, text))

    if not candidates:
        return ""
    if target_cwd and any(candidate[0] for candidate in candidates):
        candidates = [candidate for candidate in candidates if candidate[0]]
    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates[0][2]


def is_generic_completion_text(text: str, default_text: str = "") -> bool:
    clean = text.strip()
    if not clean:
        return True
    if default_text and clean == default_text.strip():
        return True
    lower = clean.lower()
    return (
        lower in {"turn-ended", "task_complete", "task-complete"}
        or ("codex" in lower and ("complete" in lower or "done" in lower))
        or ("codex" in lower and "完成" in clean and "查看" in clean)
    )


def is_completion_event(payload: object | None) -> bool:
    if not isinstance(payload, dict):
        return False
    event_type = str(payload.get("type") or "").strip().lower()
    nested = payload.get("payload")
    nested_type = str(nested.get("type") or "").strip().lower() if isinstance(nested, dict) else ""
    return event_type in {"turn-ended", "turn_ended", "task_complete"} or nested_type in {
        "turn-ended",
        "turn_ended",
        "task_complete",
    }


def should_use_session_fallback(stdin_payload: str, stdin_text: str, codex_event: object | None, default_text: str) -> bool:
    if not is_generic_completion_text(stdin_text, default_text):
        return False
    raw = stdin_payload.strip()
    if not raw:
        return True
    if codex_event is None:
        return raw.lower() in {"turn-ended", "turn_ended", "task_complete", "task-complete"}
    return is_completion_event(codex_event)


def extract_stdin_text(stdin_payload: str) -> tuple[str, object | None]:
    raw = stdin_payload.strip()
    if not raw:
        return "", None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    return find_text(payload), payload


def build_hook_payload(args: list[str], stdin_payload: str, *, cwd: str | None = None) -> dict[str, object]:
    stdin_text, codex_event = extract_stdin_text(stdin_payload)
    default_text = " ".join(item.strip() for item in args if item.strip()).strip()
    configured_default = os.getenv("AI_DESK_PHONE_DEFAULT_HOOK_TEXT", "").strip()
    fallback_text = ""
    if should_use_session_fallback(stdin_payload, stdin_text, codex_event, default_text):
        fallback_text = recent_session_completion_text(cwd or os.getenv("AI_DESK_PHONE_NOTIFY_CWD") or os.getcwd())
    text = fallback_text or stdin_text or default_text or configured_default
    payload: dict[str, object] = {
        "source": os.getenv("AI_DESK_PHONE_SOURCE", "codex"),
        "event": "turn-ended",
        "ts": int(time.time()),
    }
    if text:
        payload["text"] = text
    if codex_event is not None:
        payload["codex_event"] = codex_event
    return payload


def main() -> int:
    hook_url = os.getenv("AI_DESK_PHONE_HOOK_URL", DEFAULT_HOOK_URL)
    callback_enabled = read_callback_enabled(hook_url)
    if callback_enabled is False:
        print("[ai-desk-phone] operator hook skipped: callback is disabled")
        return 0

    payload = build_hook_payload(
        sys.argv[1:],
        read_stdin_payload(),
        cwd=os.getenv("AI_DESK_PHONE_NOTIFY_CWD") or os.getcwd(),
    )
    if not payload.get("text"):
        print("[ai-desk-phone] operator hook skipped: empty completion message")
        return 0

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        hook_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=2.0) as response:
            response.read()
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        print(f"[ai-desk-phone] operator hook skipped: {exc}", file=sys.stderr)
        return 0

    print("[ai-desk-phone] operator hook sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
