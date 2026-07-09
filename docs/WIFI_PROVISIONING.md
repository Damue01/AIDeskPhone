# AiLandLine Wi-Fi Provisioning

The firmware is not tied to one hard-coded Wi-Fi network. A local
`wifi_credentials.h` file may still be used by a developer, but that file is
ignored by git and is not required for other users.

## First setup

1. Power on the ESP32 board.
2. If it has no saved Wi-Fi credentials, or if joining Wi-Fi keeps failing, it
   starts a setup access point named `AiLandLine-Setup`.
3. Join `AiLandLine-Setup` with password `ailandline`.
4. A captive portal should open automatically. If it does not, open
   `http://192.168.4.1/`.
5. Enter the user's Wi-Fi SSID and password.
6. Leave `Computer command host` blank for `auto`, unless the desktop app runs
   on a known fixed LAN IP.
7. Save. The board stores the settings in NVS and reconnects.

## Host Field

`auto` uses the Wi-Fi gateway as the command host. This works well when the
computer provides a Windows Mobile Hotspot.

On a normal router, the gateway is usually the router, not the computer. That is
fine: the desktop console still discovers the board from its UDP telemetry
broadcast, and hardware commands can use UDP once the device is seen.

For the most reliable persistent TCP command channel on a normal router, enter
the desktop computer's IPv4 address in `Computer command host`.

## Reconfigure Wi-Fi

Send the firmware command `provision`, `wifi_setup`, or `setup_portal` to make
the board leave station mode and start `AiLandLine-Setup` again.

The setup values are saved on the board, so users do not need to rebuild or
edit firmware to change Wi-Fi networks.
