import tempfile
import unittest
import json
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


class FakeUdpSocket:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, payload: bytes, target: tuple[str, int]) -> int:
        self.sent.append((payload, target))
        return len(payload)


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

    def test_wifi_tx_power_is_parsed_from_firmware_payload(self) -> None:
        parsed = parse_serial_line('{"digital":"HIGH","hook":"ON_HOOK","hook_pin":0,"wifi_tx_power":40}')

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.wifi_tx_power, 40)

    def test_update_config_sends_runtime_config_over_udp_when_wifi_device_is_connected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.attach_udp_socket(socket)  # type: ignore[arg-type]
            app.update_udp_device("192.0.2.113", 8767)

            app.update_config(ConsoleConfig(debounce_ms=55, sample_interval_ms=80))

        self.assertEqual(len(socket.sent), 1)
        payload, target = socket.sent[0]
        self.assertEqual(target, ("192.0.2.113", 8767))
        command = json.loads(payload.decode("utf-8").strip())
        self.assertEqual(command["type"], "config")
        self.assertEqual(command["config"]["debounce_ms"], 55)
        self.assertEqual(command["config"]["sample_interval_ms"], 80)

    def test_stale_udp_device_is_not_reported_as_real_hardware_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.attach_udp_socket(socket)  # type: ignore[arg-type]
            app.update_udp_device("192.0.2.113", 8767)
            assert app.udp_last_seen is not None
            app.udp_last_seen -= 60

            status = app.hardware_status()

        self.assertFalse(status["real_device_connected"])
        self.assertFalse(app.has_hardware_link())

    def test_stale_real_sample_is_not_reported_as_real_hardware_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.handle_sample(sample(1000, "HIGH"))
            assert app.real_sample_last_seen is not None
            app.real_sample_last_seen -= 60

            status = app.hardware_status()

        self.assertFalse(status["real_device_connected"])
        self.assertGreater(status["real_sample_last_seen_seconds"], 8)
        self.assertEqual(status["current_sample"]["sample_source"], "device")

    def test_firmware_uses_separate_udp_sockets_for_telemetry_and_commands(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("static WiFiUDP telemetryUdp;", source)
        self.assertIn("static WiFiUDP commandUdp;", source)
        self.assertIn("commandUdp.begin(COMMAND_PORT)", source)
        self.assertIn("const int packetSize = commandUdp.parsePacket();", source)
        self.assertIn("telemetryUdp.beginPacket(IPAddress(255, 255, 255, 255), TELEMETRY_PORT);", source)

    def test_firmware_does_not_digital_write_pwm_buzzer_pin(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("digitalWrite(buzzerPin, LOW);", source)

    def test_firmware_scans_wifi_but_connects_by_ssid(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("scanForConfiguredWifi", source)
        self.assertIn("scan_selected", source)
        self.assertIn("WiFi.setAutoReconnect(true);", source)
        self.assertIn("WiFi.begin(WIFI_STA_SSID, WIFI_STA_PASSWORD);", source)
        self.assertNotIn("WiFi.begin(WIFI_STA_SSID, WIFI_STA_PASSWORD, lastTargetChannel, lastTargetBssid, true);", source)

    def test_firmware_uses_reduced_wifi_tx_power_compat_settings(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("static constexpr int WIFI_TX_POWER_QUARTER_DBM = 40;", source)
        self.assertIn("WiFi.setSleep(false);", source)
        self.assertIn("WiFi.setTxPower(static_cast<wifi_power_t>(WIFI_TX_POWER_QUARTER_DBM));", source)
        self.assertIn("esp_wifi_set_protocol", source)
        self.assertIn("esp_wifi_set_bandwidth", source)
        self.assertIn("WIFI_BW_HT20", source)
        self.assertIn("wifi_tx_power", source)

    def test_firmware_does_not_publish_high_rate_samples_while_wifi_is_disconnected(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("WiFi.status() == WL_CONNECTED && (now - lastSampleMs) >= sampleIntervalMs", source)


if __name__ == "__main__":
    unittest.main()
