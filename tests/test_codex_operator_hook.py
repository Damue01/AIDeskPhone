import io
import json
import unittest
from unittest.mock import patch

from tools import codex_operator_hook


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, limit: int | None = None) -> bytes:
        del limit
        return json.dumps(self.payload).encode("utf-8")


class CodexOperatorHookTest(unittest.TestCase):
    def test_build_payload_prefers_codex_text_over_default_message(self) -> None:
        raw = json.dumps({"hook_event_name": "Stop", "last_message": "改好了配置页面。"}, ensure_ascii=False)

        payload = codex_operator_hook.build_hook_payload(["Codex 当前任务已完成。"], raw)

        self.assertEqual(payload["text"], "改好了配置页面。")
        self.assertEqual(payload["event"], "turn-ended")
        self.assertIn("codex_event", payload)

    def test_build_payload_does_not_turn_unknown_json_into_spoken_text(self) -> None:
        raw = json.dumps({"hook_event_name": "Stop", "cwd": "E:\\Ai2Work\\AIDeskPhone"}, ensure_ascii=False)

        payload = codex_operator_hook.build_hook_payload(["Codex 当前任务已完成。"], raw)

        self.assertEqual(payload["text"], "Codex 当前任务已完成。")

    def test_callback_disabled_status_skips_post(self) -> None:
        requests: list[str] = []

        def fake_urlopen(request, timeout=0):
            del timeout
            requests.append(request.full_url)
            return FakeResponse({"callback_enabled": False})

        with (
            patch.object(codex_operator_hook.urllib.request, "urlopen", side_effect=fake_urlopen),
            patch.object(codex_operator_hook.sys, "stdin", io.StringIO("")),
            patch.object(codex_operator_hook.sys, "argv", ["hook.py", "Codex 当前任务已完成。"]),
        ):
            code = codex_operator_hook.main()

        self.assertEqual(code, 0)
        self.assertEqual(requests, ["http://127.0.0.1:8765/api/replies"])


if __name__ == "__main__":
    unittest.main()
