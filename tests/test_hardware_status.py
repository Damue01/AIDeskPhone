import tempfile
import threading
import unittest
import urllib.request
import json
from pathlib import Path

from tools.ai_desk_phone_console import AppState, ConsoleConfig, SensorSample, make_server, parse_serial_line


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
        self.closed = False

    def sendto(self, payload: bytes, target: tuple[str, int]) -> int:
        self.sent.append((payload, target))
        return len(payload)

    def setsockopt(self, *_args: object) -> None:
        return None

    def close(self) -> None:
        self.closed = True


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

    def test_disabling_simulation_clears_stale_simulation_sample(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=True)
            app.emit_simulated_sample("PRESSED", "test")

            app.set_simulation_enabled(False)
            status = app.hardware_status()

        self.assertFalse(status["simulation_enabled"])
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

    def test_update_config_keeps_state_machine_on_device_sample_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.handle_sample(sample(1000, "HIGH"))

            app.update_config(ConsoleConfig())
            status = app.hardware_status()

        self.assertEqual(app.machine.last_stable_change_ms, 1000)
        self.assertEqual(app.last_state, "PRESSED")
        self.assertEqual(status["current_sample"]["python_state"], "PRESSED")

    def test_state_machine_recovers_when_device_clock_is_behind_host_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.machine.reset_to_state("RELEASED", 999999)

            app.handle_sample(sample(1000, "HIGH"))

        self.assertEqual(app.last_state, "PRESSED")
        self.assertEqual(app.current_sample["python_state"], "PRESSED")

    def test_wifi_tx_power_is_parsed_from_firmware_payload(self) -> None:
        parsed = parse_serial_line('{"digital":"HIGH","hook":"ON_HOOK","hook_pin":0,"wifi_tx_power":78}')

        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.wifi_tx_power, 78)

    def test_update_config_sends_runtime_config_over_udp_when_wifi_device_is_connected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.attach_udp_socket(socket)  # type: ignore[arg-type]
            app.create_udp_command_socket = lambda: socket  # type: ignore[method-assign]
            app.update_udp_device("192.0.2.113", 8767)

            app.update_config(ConsoleConfig(debounce_ms=55, sample_interval_ms=80))

        payload, target = socket.sent[0]
        self.assertEqual(target, ("192.0.2.113", 8767))
        self.assertIn(("255.255.255.255", 8767), [sent_target for _, sent_target in socket.sent])
        command = json.loads(payload.decode("utf-8").strip())
        self.assertEqual(command["type"], "config")
        self.assertEqual(command["config"]["debounce_ms"], 55)
        self.assertEqual(command["config"]["sample_interval_ms"], 80)

    def test_update_config_sends_persisted_hardware_pins_over_udp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.attach_udp_socket(socket)  # type: ignore[arg-type]
            app.create_udp_command_socket = lambda: socket  # type: ignore[method-assign]
            app.update_udp_device("192.0.2.113", 8767)

            app.update_config(ConsoleConfig(hook_pin=0, buzzer_pin=21, led_pin=8))

        payload, _ = socket.sent[0]
        command = json.loads(payload.decode("utf-8").strip())
        self.assertEqual(command["config"]["hook_pin"], 0)
        self.assertEqual(command["config"]["buzzer_pin"], 21)
        self.assertEqual(command["config"]["led_pin"], 8)

    def test_set_test_pins_persists_hardware_pins(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), config_path, simulation_enabled=False)
            app.attach_udp_socket(socket)  # type: ignore[arg-type]
            app.send_tcp_command = lambda _command: False  # type: ignore[method-assign]
            app.create_udp_command_socket = lambda: socket  # type: ignore[method-assign]
            app.update_udp_device("192.0.2.113", 8767)

            ok = app.set_test_pins(0, 21, 8)

            saved = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertTrue(ok)
        self.assertEqual(saved["hook_pin"], 0)
        self.assertEqual(saved["buzzer_pin"], 21)
        self.assertEqual(saved["led_pin"], 8)

    def test_udp_commands_use_transient_command_socket(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            listener_socket = FakeUdpSocket()
            command_socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.attach_udp_socket(listener_socket)  # type: ignore[arg-type]
            app.send_tcp_command = lambda _command: False  # type: ignore[method-assign]
            app.create_udp_command_socket = lambda: command_socket  # type: ignore[method-assign]
            app.update_udp_device("192.0.2.113", 8767)

            ok = app.send_device_command("led_on")

        self.assertTrue(ok)
        self.assertEqual(listener_socket.sent, [])
        self.assertEqual(len(command_socket.sent), 9)
        payload, target = command_socket.sent[0]
        self.assertEqual(target, ("192.0.2.113", 8767))
        self.assertEqual(json.loads(payload.decode("utf-8").strip()), {"type": "led_on"})
        self.assertEqual(command_socket.sent[1][1], ("192.0.2.255", 8767))
        self.assertEqual(json.loads(command_socket.sent[1][0].decode("utf-8").strip()), {"type": "led_on"})
        self.assertEqual(
            {target for _, target in command_socket.sent},
            {("192.0.2.113", 8767), ("192.0.2.255", 8767), ("255.255.255.255", 8767)},
        )
        self.assertTrue(command_socket.closed)

    def test_hardware_command_fails_without_device_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command_socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.create_udp_command_socket = lambda: command_socket  # type: ignore[method-assign]
            app.update_udp_device("192.0.2.113", 8767)
            app.wait_for_hardware_confirmation = lambda _expected, _since: False  # type: ignore[method-assign]

            ok = app.run_hardware_command("led_on")
            status = app.hardware_status()

        self.assertFalse(ok)
        self.assertEqual(status["last_hardware_command"]["error"], "not_confirmed")
        self.assertFalse(status["last_hardware_command"]["confirmed"])
        self.assertEqual(json.loads(command_socket.sent[0][0].decode("utf-8").strip()), {"type": "led_on"})

    def test_hardware_command_succeeds_with_device_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command_socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.create_udp_command_socket = lambda: command_socket  # type: ignore[method-assign]
            app.update_udp_device("192.0.2.113", 8767)
            captured: list[dict[str, str]] = []

            def confirm(expected: dict[str, str], _since: float) -> bool:
                captured.append(expected)
                return True

            app.wait_for_hardware_confirmation = confirm  # type: ignore[method-assign]

            ok = app.run_hardware_command("ring_off")
            status = app.hardware_status()

        self.assertTrue(ok)
        self.assertEqual(captured, [{"buzzer": "OFF"}])
        self.assertTrue(status["last_hardware_command"]["ok"])
        self.assertTrue(status["last_hardware_command"]["confirmed"])

    def test_hardware_command_is_queued_for_device_polling(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command_socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.create_udp_command_socket = lambda: command_socket  # type: ignore[method-assign]
            app.update_udp_device("192.0.2.113", 8767)
            app.wait_for_hardware_confirmation = lambda _expected, _since: False  # type: ignore[method-assign]

            app.run_hardware_command("led_on")
            queued = app.pop_next_device_command()

        self.assertIsNotNone(queued)
        assert queued is not None
        self.assertEqual(queued["command"], "led_on")

    def test_device_poll_response_pops_next_queued_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.queue_device_command("led_on")

            response = app.next_device_poll_response()
            empty_response = app.next_device_poll_response()

        self.assertEqual(response["type"], "led_on")
        self.assertEqual(response["id"], 1)
        self.assertIsNone(empty_response)

    def test_device_poll_response_preserves_json_command_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.queue_device_command('{"type":"config","config":{"led_pin":20}}')

            response = app.next_device_poll_response()

        self.assertEqual(response["type"], "config")
        self.assertEqual(response["config"]["led_pin"], 20)

    def test_tcp_command_client_counts_as_real_hardware_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.update_tcp_command_client(("192.0.2.113", 49152))

            status = app.hardware_status()

        self.assertTrue(status["real_device_connected"])
        self.assertTrue(status["tcp_command_connected"])
        self.assertEqual(status["tcp_command_client"], "192.0.2.113:49152")

    def test_send_device_command_queues_for_tcp_client_without_udp_or_serial(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.update_tcp_command_client(("192.0.2.113", 49152))

            ok = app.send_device_command("led_on")
            response = app.next_device_poll_response()

        self.assertTrue(ok)
        self.assertEqual(response["type"], "led_on")

    def test_device_command_queue_keeps_latest_command_per_hardware_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.queue_device_command("led_off")
            app.queue_device_command("ring_on")
            app.queue_device_command("led_on")

            first = app.next_device_poll_response()
            second = app.next_device_poll_response()
            empty = app.next_device_poll_response()

        self.assertEqual(first["type"], "ring_on")
        self.assertEqual(second["type"], "led_on")
        self.assertIsNone(empty)

    def test_device_next_command_endpoint_returns_command_string(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.queue_device_command("led_on")
            server = make_server("127.0.0.1", 0, app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                host, port = server.server_address[:2]
                with urllib.request.urlopen(f"http://{host}:{port}/api/device/next-command", timeout=2) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertTrue(payload["ok"])
        self.assertEqual(payload["command"], "led_on")
        self.assertEqual(payload["id"], 1)

    def test_hardware_command_accepts_already_confirmed_fresh_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command_socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.create_udp_command_socket = lambda: command_socket  # type: ignore[method-assign]
            app.update_udp_device("192.0.2.113", 8767)
            current = sample(1000, "HIGH")
            current.led = "OFF"
            app.handle_sample(current)
            app.wait_for_hardware_confirmation = lambda _expected, _since: False  # type: ignore[method-assign]

            ok = app.run_hardware_command("led_off")
            status = app.hardware_status()

        self.assertTrue(ok)
        self.assertTrue(status["last_hardware_command"]["ok"])
        self.assertTrue(status["last_hardware_command"]["confirmed"])

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

    def test_hardware_command_still_uses_stale_udp_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            listener_socket = FakeUdpSocket()
            command_socket = FakeUdpSocket()
            app = AppState(ConsoleConfig(), Path(directory) / "config.json", simulation_enabled=False)
            app.attach_udp_socket(listener_socket)  # type: ignore[arg-type]
            app.create_udp_command_socket = lambda: command_socket  # type: ignore[method-assign]
            app.update_udp_device("192.0.2.113", 8767)
            assert app.udp_last_seen is not None
            app.udp_last_seen -= 60

            ok = app.send_device_command("led_on")

        self.assertTrue(ok)
        self.assertFalse(app.has_hardware_link())
        self.assertEqual(json.loads(command_socket.sent[0][0].decode("utf-8").strip()), {"type": "led_on"})
        self.assertEqual(command_socket.sent[0][1], ("192.0.2.113", 8767))

    def test_hardware_command_uses_persisted_udp_target_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            command_socket = FakeUdpSocket()
            config = ConsoleConfig(udp_device_host="192.0.2.113", udp_command_port=8767)
            app = AppState(config, Path(directory) / "config.json", simulation_enabled=False)
            app.create_udp_command_socket = lambda: command_socket  # type: ignore[method-assign]

            ok = app.send_device_command("ring_on")
            status = app.hardware_status()

        self.assertTrue(ok)
        self.assertEqual(status["udp_device"], "192.0.2.113:8767")
        self.assertFalse(status["real_device_connected"])
        self.assertEqual(json.loads(command_socket.sent[0][0].decode("utf-8").strip()), {"type": "ring_on"})

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
        self.assertIn("static constexpr unsigned long DEFAULT_SAMPLE_INTERVAL_MS = 250;", source)
        self.assertIn("udpReady = commandUdp.begin(COMMAND_PORT) == 1;", source)
        self.assertIn("const int packetSize = commandUdp.parsePacket();", source)
        self.assertIn("telemetryUdp.beginPacket(IPAddress(255, 255, 255, 255), TELEMETRY_PORT);", source)
        self.assertIn('payload.indexOf("\\"type\\":\\"sample\\"") < 0', source)
        self.assertNotIn("static WiFiUDP udp;", source)

    def test_firmware_does_not_digital_write_pwm_buzzer_pin(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("digitalWrite(buzzerPin, LOW);", source)

    def test_firmware_runtime_config_applies_persisted_pins(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn('configurePins(intField(command, "hook_pin", hookPin)', source)
        self.assertIn('intField(command, "buzzer_pin", buzzerPin)', source)
        self.assertIn('intField(command, "led_pin", ledPin)', source)

    def test_firmware_upload_forces_utf8_output(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "tools" / "ai_desk_phone_console.py").read_text(encoding="utf-8")

        self.assertIn('"PYTHONIOENCODING": "utf-8"', source)
        self.assertIn('"PYTHONUTF8": "1"', source)
        self.assertIn('"PLATFORMIO_SETTING_ENABLE_COLOR": "0"', source)
        self.assertIn("env=upload_env", source)

    def test_firmware_uses_stable_wifi_connection_settings(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("scanForConfiguredWifi", source)
        self.assertIn("scan_selected", source)
        self.assertIn("static constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 30000;", source)
        self.assertIn("static constexpr unsigned long WIFI_CONNECT_TIMEOUT_MS = 15000;", source)
        self.assertIn("static bool wifiConnectInProgress = false;", source)
        self.assertIn("static constexpr bool WIFI_USE_ALTERNATE_STA_MAC = false;", source)
        self.assertIn("static constexpr bool WIFI_RELAX_PMF = true;", source)
        self.assertIn("applyWifiStationIdentity();", source)
        self.assertIn("esp_wifi_set_mac(WIFI_IF_STA, mac);", source)
        self.assertIn("connectWifiWithRelaxedPmf();", source)
        self.assertIn("WiFi.begin(activeWifiSsid(), activeWifiPassword(), 0, nullptr, false);", source)
        self.assertIn("config.sta.pmf_cfg.capable = false;", source)
        self.assertIn("config.sta.pmf_cfg.required = false;", source)
        self.assertIn("esp_wifi_connect();", source)
        self.assertIn('\\"wifi_mac\\":\\"', source)
        self.assertIn("WiFi.setAutoReconnect(true);", source)
        self.assertIn("scanForConfiguredWifi();", source)
        self.assertIn('\\"event\\":\\"connect_start\\",\\"mode\\":\\"ssid_only\\"', source)
        self.assertIn("wifiConnectInProgress && (now - lastWifiAttemptMs) < WIFI_CONNECT_TIMEOUT_MS", source)
        self.assertNotIn("WiFi.begin(WIFI_STA_SSID, WIFI_STA_PASSWORD, lastTargetChannel, lastTargetBssid, true);", source)

    def test_firmware_uses_stable_wifi_tx_power_compat_settings(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("static constexpr int WIFI_TX_POWER_QUARTER_DBM = 78;", source)
        self.assertIn("static constexpr bool WIFI_USE_ALTERNATE_STA_MAC = false;", source)
        self.assertIn("WiFi.setSleep(false);", source)
        self.assertIn("WiFi.setTxPower(static_cast<wifi_power_t>(WIFI_TX_POWER_QUARTER_DBM));", source)
        self.assertNotIn("WiFi.setMinSecurity", source)
        self.assertNotIn("esp_wifi_set_protocol", source)
        self.assertNotIn("esp_wifi_set_bandwidth", source)
        self.assertIn("wifi_tx_power", source)

    def test_firmware_uses_persistent_tcp_command_channel_without_http_poll(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("#include <WiFiClient.h>", source)
        self.assertIn("static constexpr uint16_t COMMAND_TCP_PORT = 8768;", source)
        self.assertIn("commandServerHost.fromString", source)
        self.assertIn("commandClient.connect(commandServerHost, COMMAND_TCP_PORT, 250)", source)
        self.assertIn("handleCommand(tcpCommandBuffer);", source)
        self.assertNotIn("GET /api/device/next-command HTTP/1.1", source)
        self.assertNotIn("pollHttpCommands(now);", source)
        self.assertNotIn("commandClient.stop()", source)

    def test_firmware_has_softap_wifi_provisioning_portal(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("#include <Preferences.h>", source)
        self.assertIn("#include <WebServer.h>", source)
        self.assertIn('PROVISIONING_AP_SSID = "AiLandLine-Setup"', source)
        self.assertIn("PROVISIONING_AP_CHANNEL = 6", source)
        self.assertIn("Preferences preferences;", source)
        self.assertIn("WebServer provisioningServer(80);", source)
        self.assertIn('preferences.getString("wifi_ssid"', source)
        self.assertIn('preferences.putString("wifi_ssid"', source)
        self.assertIn('preferences.putString("cmd_host"', source)
        self.assertIn("WiFi.mode(WIFI_AP);", source)
        self.assertIn("WiFi.softAP(PROVISIONING_AP_SSID, PROVISIONING_AP_PASSWORD, PROVISIONING_AP_CHANNEL, false, 4)", source)
        self.assertIn("provisioningServer.on(\"/save\", HTTP_POST", source)
        self.assertIn("requestProvisioningPortal(\"auth_failures\")", source)
        self.assertIn("WiFi.setAutoReconnect(false);", source)
        self.assertIn("if (provisioningActive || provisioningStartPending)", source)
        self.assertIn("provisioningStartInProgress", source)
        self.assertIn("pollProvisioningPortal();", source)

    def test_firmware_provisioning_is_not_bound_to_one_user_network(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn("#include <DNSServer.h>", source)
        self.assertIn("DNSServer provisioningDns;", source)
        self.assertIn('provisioningDns.start(53, "*", WiFi.softAPIP())', source)
        self.assertIn("provisioningDns.processNextRequest();", source)
        self.assertIn("#define COMMAND_SERVER_HOST_OCTETS 0, 0, 0, 0", source)
        self.assertIn("#define COMMAND_SERVER_HOST_TEXT \"\"", source)
        self.assertIn("static bool resolveCommandServerHost()", source)
        self.assertIn("WiFi.gatewayIP()", source)
        self.assertIn("maintainTcpCommandClient", source)
        self.assertIn("if (!resolveCommandServerHost())", source)
        self.assertIn("host.length() > 0 ? host : \"auto\"", source)
        self.assertIn("commandHasType(command, \"provision\")", source)

    def test_firmware_does_not_publish_high_rate_samples_while_wifi_is_disconnected(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "firmware" / "esp32c3_gpio0_21_test" / "src" / "main.cpp").read_text(
            encoding="utf-8"
        )

        self.assertIn('if (Serial && payload.indexOf("\\"type\\":\\"sample\\"") < 0)', source)
        self.assertIn("WiFi.status() == WL_CONNECTED && (now - lastSampleMs) >= sampleIntervalMs", source)


if __name__ == "__main__":
    unittest.main()
