import tempfile
import unittest
from pathlib import Path

from tools.ai_desk_phone_console import AppState, ConsoleConfig


class HardwareStatusTest(unittest.TestCase):
    def test_hardware_status_reports_udp_and_simulation_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)

            status = app.hardware_status()

        self.assertTrue(status["ok"])
        self.assertFalse(status["udp_listening"])
        self.assertFalse(status["real_device_connected"])
        self.assertFalse(status["simulation_enabled"])
        self.assertIsNone(status["current_sample"])


if __name__ == "__main__":
    unittest.main()
