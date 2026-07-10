import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
NOTIFY_PATH = REPO_ROOT / ".codex" / "hooks" / "ai_desk_phone_notify.py"


def load_notify_module():
    spec = importlib.util.spec_from_file_location("ai_desk_phone_notify", NOTIFY_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CodexNotifyHookTest(unittest.TestCase):
    def test_notification_payload_prefers_codex_json_argument(self) -> None:
        notify = load_notify_module()

        with patch.object(sys, "argv", ["notify.py", '{"type":"agent-turn-complete"}']):
            payload = notify.read_notification_payload()

        self.assertEqual(payload, '{"type":"agent-turn-complete"}')

    def test_repo_notify_preserves_upstream_and_calls_phone_hook(self) -> None:
        notify = load_notify_module()
        calls: list[tuple[tuple[str, ...], str]] = []

        def fake_runner(command, **kwargs):
            calls.append((tuple(str(part) for part in command), kwargs.get("input", "")))
            return subprocess.CompletedProcess(command, 0)

        code = notify.run_notifications(
            '{"last_message":"配置好了 hook"}',
            cwd=REPO_ROOT,
            runner=fake_runner,
            upstream_command=("codex-computer-use.exe", "turn-ended"),
            python_executable="python",
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], ("codex-computer-use.exe", "turn-ended"))
        self.assertEqual(calls[0][1], '{"last_message":"配置好了 hook"}')
        self.assertEqual(calls[1][0][0], "python")
        self.assertEqual(calls[1][0][1:3], ("-X", "utf8"))
        self.assertEqual(calls[1][0][3], str(REPO_ROOT / "tools" / "codex_operator_hook.py"))
        self.assertIn("Codex 当前任务已完成", calls[1][0][4])
        self.assertEqual(calls[1][1], '{"last_message":"配置好了 hook"}')

    def test_non_repo_notify_still_calls_phone_hook(self) -> None:
        notify = load_notify_module()
        calls: list[tuple[tuple[str, ...], str]] = []

        def fake_runner(command, **kwargs):
            calls.append((tuple(str(part) for part in command), kwargs.get("input", "")))
            return subprocess.CompletedProcess(command, 0)

        code = notify.run_notifications(
            '{"last_message":"done outside cwd"}',
            cwd=Path.home(),
            runner=fake_runner,
            upstream_command=("codex-computer-use.exe", "turn-ended"),
            python_executable=sys.executable,
        )

        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], ("codex-computer-use.exe", "turn-ended"))
        self.assertEqual(calls[1][0][0], sys.executable)
        self.assertEqual(calls[1][0][1:3], ("-X", "utf8"))
        self.assertEqual(calls[1][0][3], str(REPO_ROOT / "tools" / "codex_operator_hook.py"))
        self.assertEqual(calls[1][1], '{"last_message":"done outside cwd"}')


if __name__ == "__main__":
    unittest.main()
