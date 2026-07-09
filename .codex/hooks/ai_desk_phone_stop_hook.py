from __future__ import annotations

import runpy
import sys
from pathlib import Path


DEFAULT_MESSAGE = "Codex 当前任务已完成，请查看对话结果。"


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    hook_script = repo_root / "tools" / "codex_operator_hook.py"
    if not hook_script.exists():
        print(f"[ai-desk-phone] operator hook skipped: {hook_script} not found", file=sys.stderr)
        return 0

    original_argv = sys.argv[:]
    try:
        sys.argv = [str(hook_script), DEFAULT_MESSAGE]
        runpy.run_path(str(hook_script), run_name="__main__")
    finally:
        sys.argv = original_argv
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
