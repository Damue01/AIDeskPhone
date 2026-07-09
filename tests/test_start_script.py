from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_script_cleans_old_console_processes_before_launch() -> None:
    script = (ROOT / "Start_AI_Desk_Phone.bat").read_text(encoding="utf-8")

    cleanup_index = script.index("Cleaning old AI Desk Phone console processes")
    launch_index = script.index('"%PYTHON%" tools\\ai_desk_phone_console.py')

    assert cleanup_index < launch_index
    assert "Get-CimInstance Win32_Process" in script
    assert "ai_desk_phone_console\\.py" in script
    assert "Stop-Process -Id $proc.ProcessId -Force" in script
    assert "Get-NetTCPConnection -LocalPort" in script
    assert '--no-simulation' in script
    assert '--host 0.0.0.0' in script
