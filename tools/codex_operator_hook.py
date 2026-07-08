from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


DEFAULT_HOOK_URL = "http://127.0.0.1:8765/api/ai/hook"


def read_stdin_payload() -> str:
    try:
        if sys.stdin is not None and not sys.stdin.closed and not sys.stdin.isatty():
            return sys.stdin.read(20_000)
    except OSError:
        return ""
    return ""


def main() -> int:
    hook_url = os.getenv("AI_DESK_PHONE_HOOK_URL", DEFAULT_HOOK_URL)
    payload: dict[str, object] = {
        "source": os.getenv("AI_DESK_PHONE_SOURCE", "codex"),
        "event": "turn-ended",
        "ts": int(time.time()),
    }
    if sys.argv[1:]:
        payload["args"] = sys.argv[1:]

    stdin_payload = read_stdin_payload().strip()
    if stdin_payload:
        payload["codex_payload"] = stdin_payload

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
