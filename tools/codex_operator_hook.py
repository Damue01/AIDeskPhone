from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_HOOK_URL = "http://127.0.0.1:8765/api/ai/hook"
DEFAULT_STATUS_PATH = "/api/replies"
TEXT_KEYS = (
    "text",
    "reply",
    "summary",
    "message",
    "final_message",
    "last_message",
    "assistant_message",
    "output",
    "content",
)


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


def extract_stdin_text(stdin_payload: str) -> tuple[str, object | None]:
    raw = stdin_payload.strip()
    if not raw:
        return "", None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, None
    return find_text(payload), payload


def build_hook_payload(args: list[str], stdin_payload: str) -> dict[str, object]:
    stdin_text, codex_event = extract_stdin_text(stdin_payload)
    default_text = " ".join(item.strip() for item in args if item.strip()).strip()
    text = stdin_text or default_text or os.getenv("AI_DESK_PHONE_DEFAULT_HOOK_TEXT", "").strip()
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

    payload = build_hook_payload(sys.argv[1:], read_stdin_payload())
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
