# AI Desk Phone

AI Desk Phone turns an HG113 desk phone shell into a local AI desk phone. The
computer runs the console, voice pipeline, Agent runtime, Codex completion
hooks, and command-center page. The ESP32 board only handles the physical phone
state, Wi-Fi telemetry, LED, and buzzer.

This project does not connect to a real telephone line. Keep the original phone
line disconnected.

## Current Hardware

The current verified board is ESP32-S3. Some firmware folders still contain
`esp32c3` in their names because the project started on ESP32-C3; treat those
as historical path names.

Current ESP32-S3 wiring:

| Function | ESP32-S3 pin |
| --- | --- |
| Hook switch input | GPIO4 |
| LED output | GPIO1 |
| Buzzer output | GPIO2 |
| Common ground | GND |

The hook input uses `INPUT_PULLUP`.

```text
HIGH = on hook / pressed / handset down
LOW  = off hook / released / handset lifted
```

## What Runs Where

```text
HG113 hook switch
  -> ESP32-S3 GPIO4
  -> Wi-Fi telemetry
  -> local console at http://127.0.0.1:8765/

Local console
  -> hardware commands over TCP/UDP
  -> ESP32-S3 GPIO1 LED and GPIO2 buzzer

Handset audio
  -> Windows audio device
  -> ASR / TTS / Agent runtime on the computer
```

The ESP32 does not process microphone or speaker audio.

## Quick Start

Install Python dependencies and start the local console:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\Start_AI_Desk_Phone.bat
```

Open:

```text
http://127.0.0.1:8765/
http://127.0.0.1:8765/command-center/
http://127.0.0.1:8765/simulator
```

Use the simulator first if no real hardware is connected.

## Secrets

Copy `.env.example` to `.env` and fill only local secrets:

```text
VOLCENGINE_API_KEY=
ARK_API_KEY=
```

Do not commit `.env`.

## ESP32-S3 Firmware

The current main firmware is:

```text
firmware/esp32c3_gpio0_21_test/
```

Build for ESP32-S3:

```powershell
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_gpio0_21_test -e esp32s3_gpio0_21_test
```

Upload, replacing `COM7` with the actual port:

```powershell
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_gpio0_21_test -e esp32s3_gpio0_21_test -t upload --upload-port COM7
```

Find the current port:

```powershell
.\.venv\Scripts\platformio.exe device list
```

The ESP32-S3 port may change after reset or replugging. In our verified setup it
has appeared as both `COM6` and `COM7`.

## Wi-Fi Provisioning

The firmware is not tied to one fixed Wi-Fi network.

On first setup, or after repeated Wi-Fi failures, the board starts a setup
hotspot:

```text
SSID: AiLandLine-Setup
Password: ailandline
Setup page: http://192.168.4.1/
```

Steps:

1. Power on the board.
2. Join `AiLandLine-Setup`.
3. Open `http://192.168.4.1/` if the setup page does not appear automatically.
4. Enter the user's Wi-Fi SSID and password.
5. Leave `Computer command host` blank for `auto`, unless the desktop computer
   has a known fixed LAN IP.
6. Save and wait for the board to reconnect.

Saved Wi-Fi settings live in ESP32 NVS, so users do not need to rebuild the
firmware to change networks.

To enter setup mode again, send one of these firmware commands:

```text
provision
wifi_setup
setup_portal
```

More detail: [docs/WIFI_PROVISIONING.md](docs/WIFI_PROVISIONING.md).

## Optional Local Wi-Fi Credentials

Developers can still create a local ignored file:

```text
firmware/esp32c3_gpio0_21_test/include/wifi_credentials.h
```

Example:

```cpp
#pragma once

#define WIFI_STA_SSID "your-wifi"
#define WIFI_STA_PASSWORD "your-password"
#define COMMAND_SERVER_HOST_TEXT "192.168.1.23"
#define COMMAND_SERVER_HOST_OCTETS 192, 168, 1, 23
```

This file is ignored by git. It is only for local development, not for users.

## Hardware Verification

After the console and board are running, check:

1. The console shows `real_device_connected = true`.
2. The board has a Wi-Fi IP.
3. The board reports `pin = 4`, `led_pin = 1`, and `buzzer_pin = 2`.
4. `LED on` lights GPIO1.
5. `beep` or `ring_on` drives GPIO2.
6. `ring_off` and `led_off` return both outputs to `OFF`.

Useful API checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/hardware/status
```

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8765/api/hardware/beep `
  -Method Post `
  -ContentType "application/json" `
  -Body "{}"
```

## Codex Completion Callback

The project can receive Codex completion hooks and turn them into phone reports.
The expected path is:

```text
Codex finishes a task
  -> local hook sends the final message to the console
  -> operator model summarizes the result
  -> phone callback queue
  -> LED/buzzer alert when callback is enabled
```

If the handset is already lifted, the console may play or queue the report
without ringing. To test the full callback ring flow, keep the handset down
until the alert starts.

Detailed hook notes: [docs/CODEX_OPERATOR_HOOK.md](docs/CODEX_OPERATOR_HOOK.md).

## Common Pitfalls

### LED works but buzzer is silent

For the current ESP32-S3 wiring:

```text
GPIO1 = LED
GPIO2 = buzzer
```

If GPIO1 lights but no sound is heard, do not assume the command failed. Check
whether the buzzer is actually wired to GPIO2, whether it needs a driver, and
whether it is active or passive.

### Board is connected but upload fails

The COM port can change. Run:

```powershell
.\.venv\Scripts\platformio.exe device list
```

Then upload with `--upload-port COMx`.

### Wi-Fi connects but backend does not react

Check that the board and computer are on the same reachable network. Windows
Mobile Hotspot usually works well with `Computer command host = auto`. On a
normal router, UDP discovery can still find the board, but a fixed desktop IP is
more reliable for the persistent TCP command channel.

### Callback does not ring

Check:

1. `enable_callback` is enabled in the console.
2. The phone is on hook before the callback arrives.
3. Hardware status shows `buzzer = OFF` and `led = OFF` before testing.
4. The completion hook sends real task content, not only a generic template.

### Text looks garbled in logs

Run commands with UTF-8 enabled:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:PYTHONUTF8='1'
```

The startup scripts already set UTF-8 for the common paths.

## Project Map

| Path | Purpose |
| --- | --- |
| `Start_AI_Desk_Phone.bat` | Starts the local console and related services. |
| `tools/ai_desk_phone_console.py` | Main backend and web console, default port `8765`. |
| `tools/agent_runtime.py` | Local PI-style Agent runtime. |
| `tools/volcengine_speech.py` | ASR, TTS, and operator polish calls. |
| `tools/codex_operator_hook.py` | Codex completion hook client. |
| `firmware/esp32c3_gpio0_21_test/` | Main ESP32 firmware, including ESP32-S3 env. |
| `firmware/esp32c3_wifi_pioarduino_test/` | Minimal Wi-Fi diagnostic firmware. |
| `web/variant-earth-command-center/` | Command-center globe/map page. |
| `docs/` | Hardware, Wi-Fi, hook, and build notes. |
| `tests/` | Unit tests for runtime, hooks, hardware status, and firmware expectations. |

## Tests

Run Python tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover tests -v
```

Build the ESP32-S3 firmware:

```powershell
.\.venv\Scripts\platformio.exe run -d firmware\esp32c3_gpio0_21_test -e esp32s3_gpio0_21_test
```

## License

No open-source license has been selected yet. Add a `LICENSE` file before
public reuse.
