from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MESSAGE = "Codex 当前任务已完成，请查看对话结果。"
DEFAULT_UPSTREAM_NOTIFY_COMMAND = (
    "codex-computer-use.exe",
    "turn-ended",
)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def read_stdin_payload() -> str:
    try:
        if sys.stdin is not None and not sys.stdin.closed and not sys.stdin.isatty():
            return sys.stdin.read(20_000)
    except OSError:
        return ""
    return ""


def parse_upstream_notify_command() -> tuple[str, ...]:
    override = os.getenv("CODEX_AI_DESK_PHONE_UPSTREAM_NOTIFY", "").strip()
    if override:
        return tuple(shlex.split(override, posix=False))
    return DEFAULT_UPSTREAM_NOTIFY_COMMAND


def run_command(
    runner: Runner,
    command: Sequence[str],
    stdin_payload: str,
    *,
    env: dict[str, str] | None = None,
    timeout: float = 8.0,
) -> None:
    if not command:
        return
    try:
        runner(
            list(command),
            input=stdin_payload,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except Exception as exc:
        print(f"[ai-desk-phone] notify step skipped: {exc}", file=sys.stderr)


def run_notifications(
    stdin_payload: str,
    *,
    cwd: Path | None = None,
    runner: Runner = subprocess.run,
    upstream_command: Sequence[str] | None = None,
    python_executable: str | None = None,
) -> int:
    upstream = tuple(upstream_command) if upstream_command is not None else parse_upstream_notify_command()
    run_command(runner, upstream, stdin_payload)

    hook_script = REPO_ROOT / "tools" / "codex_operator_hook.py"
    if not hook_script.exists():
        print(f"[ai-desk-phone] operator hook skipped: {hook_script} not found", file=sys.stderr)
        return 0

    python = python_executable or sys.executable
    hook_env = os.environ.copy()
    hook_env["AI_DESK_PHONE_NOTIFY_CWD"] = str(cwd or Path.cwd())
    run_command(runner, (python, "-X", "utf8", str(hook_script), DEFAULT_MESSAGE), stdin_payload, env=hook_env)
    return 0


def main() -> int:
    return run_notifications(read_stdin_payload())


if __name__ == "__main__":
    raise SystemExit(main())
