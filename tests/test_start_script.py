import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class StartScriptsTest(unittest.TestCase):
    def test_start_script_cleans_old_console_processes_before_launch(self) -> None:
        script = (ROOT / "Start_AI_Desk_Phone.bat").read_text(encoding="utf-8")

        cleanup_index = script.index("Cleaning old AI Desk Phone console processes")
        launch_index = script.index('"%PYTHON%" tools\\ai_desk_phone_console.py')

        self.assertLess(cleanup_index, launch_index)
        self.assertIn("Get-CimInstance Win32_Process -Filter", script)
        self.assertIn("ai_desk_phone_console\\.py", script)
        self.assertIn("Stop-Process -Id $owner -Force", script)
        self.assertIn("Get-NetTCPConnection -LocalPort", script)
        self.assertIn("owned by an unrelated process", script)

    def test_start_script_uses_local_http_and_has_explicit_simulator_mode(self) -> None:
        script = (ROOT / "Start_AI_Desk_Phone.bat").read_text(encoding="utf-8")

        self.assertIn('if /I "%ARG1%"=="simulator"', script)
        self.assertIn("--host 127.0.0.1", script)
        self.assertNotIn("--host 0.0.0.0", script)
        self.assertIn("--no-serial --no-actions", script)
        self.assertIn("--simulation-only", script)
        self.assertIn("--no-simulation", script)

    def test_real_device_script_keeps_web_and_tcp_command_ports_separate(self) -> None:
        script = (ROOT / "scripts" / "Connect-RealDevice.ps1").read_text(encoding="utf-8")

        self.assertIn('[int]$WebPort = 8765', script)
        self.assertIn('[int]$TcpCommandPort = 8768', script)
        self.assertIn('if ($WebPort -eq $TcpCommandPort)', script)
        self.assertIn('"--tcp-command-port", "$TcpCommandPort"', script)
        self.assertIn("Stop-AIDeskPhoneConsoleOnWebPort $WebPort", script)
        self.assertIn("ai_desk_phone_console\\.py", script)
        self.assertIn("tools[\\\\/]+ai_desk_phone_console\\.py", script)
        self.assertNotIn('Stop-PortOwner "UDP"', script)


if __name__ == "__main__":
    unittest.main()
