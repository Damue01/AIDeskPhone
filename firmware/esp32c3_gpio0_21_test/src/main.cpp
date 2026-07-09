#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "esp_arduino_version.h"
#include "esp_wifi.h"

#if __has_include("wifi_credentials.h")
#include "wifi_credentials.h"
#endif

#ifndef WIFI_STA_SSID
#define WIFI_STA_SSID ""
#endif

#ifndef WIFI_STA_PASSWORD
#define WIFI_STA_PASSWORD ""
#endif

static constexpr int DEFAULT_HOOK_PIN = 0;
static constexpr int DEFAULT_BUZZER_PIN = 21;
static constexpr int DEFAULT_LED_PIN = 20;

static constexpr uint16_t TELEMETRY_PORT = 8766;
static constexpr uint16_t COMMAND_PORT = 8767;
static constexpr unsigned long DEFAULT_SAMPLE_INTERVAL_MS = 50;
static constexpr unsigned long HEARTBEAT_INTERVAL_MS = 1000;
static constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 30000;
static constexpr unsigned long DEFAULT_DEBOUNCE_MS = 35;
static constexpr unsigned long STARTUP_SETTLE_MS = 300;
static constexpr int WIFI_TX_POWER_QUARTER_DBM = 40;
static constexpr int BUZZER_FREQ_HZ = 3000;
static constexpr int BUZZER_PWM_CHANNEL = 0;
static constexpr int BUZZER_PWM_RESOLUTION_BITS = 8;
static constexpr int BUZZER_PWM_DUTY_50_PERCENT = 128;
static constexpr int LED_ACTIVE_LEVEL = HIGH;

static int hookPin = DEFAULT_HOOK_PIN;
static int buzzerPin = DEFAULT_BUZZER_PIN;
static int ledPin = DEFAULT_LED_PIN;
static int lastRaw = HIGH;
static int stableRaw = HIGH;
static bool buzzerOn = false;
static bool ledOn = false;
static bool udpReady = false;
static int lastStaDisconnectReason = 0;
static int lastTargetChannel = 0;
static uint8_t lastTargetBssid[6] = {0};
static bool hasTargetBssid = false;
static unsigned long lastRawChangeMs = 0;
static unsigned long lastSampleMs = 0;
static unsigned long lastHeartbeatMs = 0;
static unsigned long lastWifiAttemptMs = 0;
static unsigned long beepUntilMs = 0;
static unsigned long sampleIntervalMs = DEFAULT_SAMPLE_INTERVAL_MS;
static unsigned long debounceMs = DEFAULT_DEBOUNCE_MS;
static unsigned long seq = 0;
static String serialBuffer;
static WiFiUDP telemetryUdp;
static WiFiUDP commandUdp;

static bool hasStaCredentials() {
  return strlen(WIFI_STA_SSID) > 0;
}

static const char *levelName(int value) {
  return value == LOW ? "LOW" : "HIGH";
}

static const char *hookStateName(int value) {
  return value == LOW ? "OFF_HOOK" : "ON_HOOK";
}

static String wifiIp() {
  if (WiFi.status() == WL_CONNECTED) {
    return WiFi.localIP().toString();
  }
  return "0.0.0.0";
}

static int wifiRssi() {
  if (WiFi.status() == WL_CONNECTED) {
    return WiFi.RSSI();
  }
  return 0;
}

static void setLed(bool enabled) {
  ledOn = enabled;
  digitalWrite(ledPin, enabled ? LED_ACTIVE_LEVEL : !LED_ACTIVE_LEVEL);
}

static void attachBuzzerPwm() {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcAttachChannel(buzzerPin, BUZZER_FREQ_HZ, BUZZER_PWM_RESOLUTION_BITS, BUZZER_PWM_CHANNEL);
#else
  ledcSetup(BUZZER_PWM_CHANNEL, BUZZER_FREQ_HZ, BUZZER_PWM_RESOLUTION_BITS);
  ledcAttachPin(buzzerPin, BUZZER_PWM_CHANNEL);
#endif
}

static void detachBuzzerPwm(int pin) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcDetach(pin);
#else
  ledcDetachPin(pin);
#endif
}

static void writeBuzzerPwm(int duty) {
#if ESP_ARDUINO_VERSION_MAJOR >= 3
  ledcWrite(buzzerPin, duty);
#else
  ledcWrite(BUZZER_PWM_CHANNEL, duty);
#endif
}

static void setBuzzer(bool enabled) {
  buzzerOn = enabled;
  setLed(enabled);
  if (enabled) {
    writeBuzzerPwm(BUZZER_PWM_DUTY_50_PERCENT);
  } else {
    writeBuzzerPwm(0);
  }
}

static String stateJson(const char *type, unsigned long now) {
  const int raw = digitalRead(hookPin);
  const int syntheticAdc = raw == LOW ? 0 : 4095;
  String json = "{";
  json += "\"type\":\"";
  json += type;
  json += "\"";
  json += ",\"device\":\"ailandline-c3\"";
  json += ",\"seq\":" + String(seq++);
  json += ",\"ms\":" + String(now);
  json += ",\"hook_pin\":" + String(hookPin);
  json += ",\"pin\":" + String(hookPin);
  json += ",\"buzzer_pin\":" + String(buzzerPin);
  json += ",\"led_pin\":" + String(ledPin);
  json += ",\"adc\":" + String(syntheticAdc);
  json += ",\"adc_synthetic\":true";
  json += ",\"digital\":\"" + String(levelName(raw)) + "\"";
  json += ",\"hook\":\"" + String(hookStateName(raw)) + "\"";
  json += ",\"buzzer\":\"" + String(buzzerOn ? "ON" : "OFF") + "\"";
  json += ",\"led\":\"" + String(ledOn ? "ON" : "OFF") + "\"";
  json += ",\"wifi_connected\":" + String(WiFi.status() == WL_CONNECTED ? "true" : "false");
  json += ",\"wifi_ssid\":\"" + String(WIFI_STA_SSID) + "\"";
  json += ",\"wifi_ip\":\"" + wifiIp() + "\"";
  json += ",\"wifi_rssi\":" + String(wifiRssi());
  json += ",\"wifi_status\":" + String(static_cast<int>(WiFi.status()));
  json += ",\"wifi_disconnect_reason\":" + String(lastStaDisconnectReason);
  json += ",\"wifi_tx_power\":" + String(static_cast<int>(WiFi.getTxPower()));
  json += ",\"free_heap\":" + String(ESP.getFreeHeap());
  json += "}";
  return json;
}

static void sendUdp(const String &payload) {
  if (WiFi.status() != WL_CONNECTED || !udpReady) {
    return;
  }

  telemetryUdp.beginPacket(IPAddress(255, 255, 255, 255), TELEMETRY_PORT);
  telemetryUdp.write(reinterpret_cast<const uint8_t *>(payload.c_str()), payload.length());
  telemetryUdp.endPacket();
}

static void publish(const String &payload) {
  Serial.println(payload);
  sendUdp(payload);
}

static void publishState(const char *type, unsigned long now) {
  publish(stateJson(type, now));
}

static void applyWifiCompatibilitySettings() {
  WiFi.setSleep(false);
  WiFi.setTxPower(static_cast<wifi_power_t>(WIFI_TX_POWER_QUARTER_DBM));
  WiFi.setMinSecurity(WIFI_AUTH_WPA2_PSK);

  const esp_err_t protocolErr = esp_wifi_set_protocol(
      WIFI_IF_STA,
      WIFI_PROTOCOL_11B | WIFI_PROTOCOL_11G | WIFI_PROTOCOL_11N);
  const esp_err_t bandwidthErr = esp_wifi_set_bandwidth(WIFI_IF_STA, WIFI_BW_HT20);

  publish(
      String("{\"type\":\"wifi_event\",\"event\":\"compat_settings\",\"protocol_err\":") + String(static_cast<int>(protocolErr)) +
      ",\"bandwidth_err\":" + String(static_cast<int>(bandwidthErr)) +
      ",\"tx_power\":" + String(static_cast<int>(WiFi.getTxPower())) +
      "}");
}

static String macString(const uint8_t *mac) {
  char buffer[18];
  snprintf(
      buffer,
      sizeof(buffer),
      "%02X:%02X:%02X:%02X:%02X:%02X",
      mac[0],
      mac[1],
      mac[2],
      mac[3],
      mac[4],
      mac[5]);
  return String(buffer);
}

static bool scanForConfiguredWifi() {
  hasTargetBssid = false;
  lastTargetChannel = 0;

  publish(String("{\"type\":\"wifi_event\",\"event\":\"scan_start\",\"ssid\":\"") + WIFI_STA_SSID + "\"}");
  const int networkCount = WiFi.scanNetworks(false, true);
  publish(String("{\"type\":\"wifi_event\",\"event\":\"scan_done\",\"count\":") + String(networkCount) + "}");

  int bestIndex = -1;
  int bestRssi = -1000;

  for (int i = 0; i < networkCount; ++i) {
    const String ssid = WiFi.SSID(i);
    if (ssid != String(WIFI_STA_SSID)) {
      continue;
    }

    const String bssid = WiFi.BSSIDstr(i);
    const int channel = WiFi.channel(i);
    const int rssi = WiFi.RSSI(i);
    const int encryption = static_cast<int>(WiFi.encryptionType(i));

    publish(
        String("{\"type\":\"wifi_event\",\"event\":\"scan_match\",\"ssid\":\"") + ssid +
        "\",\"bssid\":\"" + bssid +
        "\",\"channel\":" + String(channel) +
        ",\"rssi\":" + String(rssi) +
        ",\"encryption\":" + String(encryption) +
        "}");

    if (bestIndex < 0 || rssi > bestRssi) {
      bestIndex = i;
      bestRssi = rssi;
    }
  }

  if (bestIndex >= 0) {
    const uint8_t *bssid = WiFi.BSSID(bestIndex);
    memcpy(lastTargetBssid, bssid, sizeof(lastTargetBssid));
    lastTargetChannel = WiFi.channel(bestIndex);
    hasTargetBssid = true;

    publish(
        String("{\"type\":\"wifi_event\",\"event\":\"scan_selected\",\"ssid\":\"") + WIFI_STA_SSID +
        "\",\"bssid\":\"" + macString(lastTargetBssid) +
        "\",\"channel\":" + String(lastTargetChannel) +
        ",\"rssi\":" + String(bestRssi) +
        "}");
  } else {
    publish(String("{\"type\":\"wifi_event\",\"event\":\"scan_no_match\",\"ssid\":\"") + WIFI_STA_SSID + "\"}");
  }

  WiFi.scanDelete();
  return hasTargetBssid;
}

static int intField(const String &command, const char *key, int fallback) {
  const String quotedKey = String("\"") + key + "\"";
  int keyIndex = command.indexOf(quotedKey);
  if (keyIndex < 0) {
    keyIndex = command.indexOf(key);
  }
  if (keyIndex < 0) {
    return fallback;
  }

  const int colonIndex = command.indexOf(':', keyIndex);
  if (colonIndex < 0) {
    return fallback;
  }

  int start = colonIndex + 1;
  while (start < command.length() && (command[start] == ' ' || command[start] == '\"')) {
    ++start;
  }

  int end = start;
  while (end < command.length() && isDigit(command[end])) {
    ++end;
  }

  if (end == start) {
    return fallback;
  }
  return command.substring(start, end).toInt();
}

static void configurePins(int nextHookPin, int nextBuzzerPin, int nextLedPin) {
  setBuzzer(false);
  digitalWrite(ledPin, !LED_ACTIVE_LEVEL);
  if (nextBuzzerPin != buzzerPin) {
    detachBuzzerPwm(buzzerPin);
  }

  hookPin = nextHookPin;
  buzzerPin = nextBuzzerPin;
  ledPin = nextLedPin;
  pinMode(hookPin, INPUT_PULLUP);
  pinMode(buzzerPin, OUTPUT);
  pinMode(ledPin, OUTPUT);
  attachBuzzerPwm();
  setLed(false);

  lastRaw = digitalRead(hookPin);
  stableRaw = lastRaw;
  lastRawChangeMs = millis();
  publishState("pins", millis());
}

static void configureRuntime(const String &command) {
  sampleIntervalMs = constrain(
      intField(command, "sample_interval_ms", static_cast<int>(sampleIntervalMs)),
      20,
      1000);
  debounceMs = constrain(
      intField(command, "debounce_ms", static_cast<int>(debounceMs)),
      5,
      1000);

  publish(
      String("{\"type\":\"config_saved\",\"sample_interval_ms\":") + String(sampleIntervalMs) +
      ",\"debounce_ms\":" + String(debounceMs) +
      "}");
}

static void startBeep(unsigned long durationMs) {
  setBuzzer(true);
  beepUntilMs = millis() + durationMs;
  publishState("buzzer", millis());
}

static bool commandHasType(const String &command, const char *type) {
  const String compact = String("\"type\":\"") + type + "\"";
  const String spaced = String("\"type\": \"") + type + "\"";
  return command == type || command.indexOf(compact) >= 0 || command.indexOf(spaced) >= 0;
}

static void handleCommand(String command) {
  command.trim();
  command.toLowerCase();

  if (command.length() == 0) {
    return;
  }

  if (commandHasType(command, "ping")) {
    publishState("hello", millis());
  } else if (commandHasType(command, "config")) {
    configureRuntime(command);
  } else if (commandHasType(command, "set_pins") || command.startsWith("set_pins")) {
    configurePins(
        intField(command, "hook_pin", hookPin),
        intField(command, "buzzer_pin", buzzerPin),
        intField(command, "led_pin", ledPin));
  } else if (commandHasType(command, "beep") || commandHasType(command, "ring") || commandHasType(command, "ring_once")) {
    startBeep(600);
  } else if (commandHasType(command, "ring_on") || commandHasType(command, "buzzer_on")) {
    beepUntilMs = 0;
    setBuzzer(true);
    publishState("buzzer", millis());
  } else if (commandHasType(command, "ring_off") || commandHasType(command, "buzzer_off")) {
    beepUntilMs = 0;
    setBuzzer(false);
    publishState("buzzer", millis());
  } else if (commandHasType(command, "led_on")) {
    setLed(true);
    publishState("led", millis());
  } else if (commandHasType(command, "led_off")) {
    setLed(false);
    publishState("led", millis());
  } else {
    publish(String("{\"type\":\"error\",\"message\":\"unknown_command\",\"command\":\"") + command + "\"}");
  }
}

static void pollSerial() {
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      handleCommand(serialBuffer);
      serialBuffer = "";
    } else if (c != '\r' && serialBuffer.length() < 400) {
      serialBuffer += c;
    }
  }
}

static void pollUdpCommands() {
  if (!udpReady) {
    return;
  }

  const int packetSize = commandUdp.parsePacket();
  if (packetSize <= 0) {
    return;
  }

  char buffer[401];
  const int bytesRead = commandUdp.read(buffer, sizeof(buffer) - 1);
  if (bytesRead <= 0) {
    return;
  }
  buffer[bytesRead] = '\0';
  publish(
      String("{\"type\":\"command_received\",\"bytes\":") + String(bytesRead) +
      ",\"port\":" + String(COMMAND_PORT) +
      "}");
  handleCommand(String(buffer));
}

static void startUdpCommandListener() {
  commandUdp.stop();
  udpReady = commandUdp.begin(COMMAND_PORT) == 1;
}

static void beginWifiConnect() {
  if (!hasStaCredentials()) {
    publish("{\"type\":\"wifi_event\",\"event\":\"missing_credentials\"}");
    return;
  }

  lastWifiAttemptMs = millis();
  udpReady = false;
  WiFi.mode(WIFI_STA);
  applyWifiCompatibilitySettings();
  WiFi.setAutoReconnect(true);

  scanForConfiguredWifi();
  WiFi.begin(WIFI_STA_SSID, WIFI_STA_PASSWORD);
  publish(String("{\"type\":\"wifi_event\",\"event\":\"connect_start\",\"mode\":\"ssid_only\",\"ssid\":\"") + WIFI_STA_SSID + "\"}");
}

static void handleWifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    lastStaDisconnectReason = 0;
    startUdpCommandListener();
    publishState("wifi_connected", millis());
  } else if (event == ARDUINO_EVENT_WIFI_STA_CONNECTED) {
    publish("{\"type\":\"wifi_event\",\"event\":\"sta_connected\"}");
  } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    lastStaDisconnectReason = info.wifi_sta_disconnected.reason;
    udpReady = false;
    commandUdp.stop();
    publishState("wifi_disconnected", millis());
  }
}

static void maintainWifi(unsigned long now) {
  if (!hasStaCredentials()) {
    return;
  }

  if (WiFi.status() == WL_CONNECTED) {
    if (!udpReady) {
      startUdpCommandListener();
    }
    return;
  }

  if ((now - lastWifiAttemptMs) >= WIFI_RETRY_INTERVAL_MS) {
    WiFi.disconnect(false, false);
    delay(50);
    beginWifiConnect();
  }
}

static void pollHook(unsigned long now) {
  const int raw = digitalRead(hookPin);

  if (raw != lastRaw) {
    lastRaw = raw;
    lastRawChangeMs = now;
  }

  if (raw != stableRaw && (now - lastRawChangeMs) >= debounceMs) {
    stableRaw = raw;
    publishState("hook_change", now);
  }
}

void setup() {
  Serial.begin(115200);
  serialBuffer.reserve(400);

  pinMode(hookPin, INPUT_PULLUP);
  pinMode(buzzerPin, OUTPUT);
  pinMode(ledPin, OUTPUT);
  attachBuzzerPwm();
  writeBuzzerPwm(0);
  setLed(false);

  WiFi.persistent(false);
  WiFi.onEvent(handleWifiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect(false, false);
  delay(200);
  beginWifiConnect();

  delay(STARTUP_SETTLE_MS);
  lastRaw = digitalRead(hookPin);
  stableRaw = lastRaw;
  lastRawChangeMs = millis();

  publishState("hello", millis());
}

void loop() {
  const unsigned long now = millis();

  pollSerial();
  pollUdpCommands();
  maintainWifi(now);
  pollHook(now);

  if (beepUntilMs != 0 && static_cast<long>(now - beepUntilMs) >= 0) {
    beepUntilMs = 0;
    setBuzzer(false);
    publishState("buzzer", now);
  }

  if (WiFi.status() == WL_CONNECTED && (now - lastSampleMs) >= sampleIntervalMs) {
    lastSampleMs = now;
    publishState("sample", now);
  }

  if ((now - lastHeartbeatMs) >= HEARTBEAT_INTERVAL_MS) {
    lastHeartbeatMs = now;
    publishState("heartbeat", now);
  }

  delay(2);
}
