import tempfile
import unittest
from pathlib import Path

from tools.ai_desk_phone_console import AppState, ConsoleConfig, SensorSample, parse_serial_line


def sample(ms: int, digital: str, *, raw_line: str = "device") -> SensorSample:
    return SensorSample(
        ms=ms,
        adc=4095 if digital == "HIGH" else 0,
        digital=digital,
        raw_line=raw_line,
        adc_synthetic=True,
    )


class HardwareStatusTest(unittest.TestCase):
    def test_hardware_status_reports_udp_and_simulation_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)

            status = app.hardware_status()

        self.assertTrue(status["ok"])
        self.assertFalse(status["udp_listening"])
        self.assertFalse(status["real_device_connected"])
        self.assertFalse(status["simulation_enabled"])
        self.assertFalse(status["serial_debug_enabled"])
        self.assertFalse(status["serial_debug_running"])
        self.assertIsNone(status["current_sample"])

    def test_short_pressed_noise_after_release_does_not_trigger_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(
                ConsoleConfig(hook_scheme="scheme1", debounce_ms=120, press_lockout_ms=900),
                Path(directory) / "config.json",
                simulation_enabled=False,
            )
            calls: list[tuple[str, str]] = []
            app.handle_hook_transition = lambda previous, state: calls.append((previous, state))  # type: ignore[method-assign]

            app.handle_sample(sample(1000, "LOW"))
            app.handle_sample(sample(1080, "HIGH"))
            app.handle_sample(sample(1220, "HIGH"))
            app.handle_sample(sample(1300, "LOW"))

        self.assertEqual(app.last_state, "RELEASED")
        self.assertEqual(calls, [])

    def test_pressed_transition_requires_stable_hold_after_release_lockout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(
                ConsoleConfig(hook_scheme="scheme1", debounce_ms=120, press_lockout_ms=900),
                Path(directory) / "config.json",
                simulation_enabled=False,
            )
            calls: list[tuple[str, str]] = []
            app.handle_hook_transition = lambda previous, state: calls.append((previous, state))  # type: ignore[method-assign]

            app.handle_sample(sample(1000, "LOW"))
            app.handle_sample(sample(2000, "HIGH"))
            app.handle_sample(sample(2130, "HIGH"))

        self.assertEqual(app.last_state, "PRESSED")
        self.assertEqual(calls, [("RELEASED", "PRESSED")])

    def test_simulation_heartbeat_does_not_override_recent_real_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(
                ConsoleConfig(hook_scheme="scheme1", debounce_ms=120, press_lockout_ms=900),
                Path(directory) / "config.json",
                simulation_enabled=True,
            )
            calls: list[tuple[str, str]] = []
            app.handle_hook_transition = lambda previous, state: calls.append((previous, state))  # type: ignore[method-assign]

            app.handle_sample(sample(1000, "LOW"))
            app.emit_simulated_sample("PRESSED", "持续心跳", log_raw=False)

        self.assertEqual(app.last_state, "RELEASED")
        self.assertEqual(app.current_sample["sample_source"], "device")
        self.assertEqual(calls, [])

    def test_off_hook_payload_maps_to_lifted_state(self) -> None:
        parsed = parse_serial_line('{"digital":"LOW","hook":"OFF_HOOK","hook_pin":0}')

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.firmware_state, "RELEASED")

    def test_on_hook_payload_maps_to_pressed_state(self) -> None:
        parsed = parse_serial_line('{"digital":"HIGH","hook":"ON_HOOK","hook_pin":0}')

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.firmware_state, "PRESSED")


if __name__ == "__main__":
    unittest.main()
