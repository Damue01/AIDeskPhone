#include <Arduino.h>
#include <DNSServer.h>
#include <Preferences.h>
#include <WebServer.h>
#include <WiFi.h>
#include <WiFiClient.h>
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

#ifndef AILANDLINE_DEFAULT_HOOK_PIN
#define AILANDLINE_DEFAULT_HOOK_PIN 0
#endif

#ifndef AILANDLINE_DEFAULT_BUZZER_PIN
#define AILANDLINE_DEFAULT_BUZZER_PIN 21
#endif

#ifndef AILANDLINE_DEFAULT_LED_PIN
#define AILANDLINE_DEFAULT_LED_PIN 20
#endif

static constexpr int DEFAULT_HOOK_PIN = AILANDLINE_DEFAULT_HOOK_PIN;
static constexpr int DEFAULT_BUZZER_PIN = AILANDLINE_DEFAULT_BUZZER_PIN;
static constexpr int DEFAULT_LED_PIN = AILANDLINE_DEFAULT_LED_PIN;

static constexpr uint16_t TELEMETRY_PORT = 8766;
static constexpr uint16_t COMMAND_PORT = 8767;
static constexpr uint16_t COMMAND_TCP_PORT = 8768;
static constexpr unsigned long DEFAULT_SAMPLE_INTERVAL_MS = 250;
static constexpr unsigned long HEARTBEAT_INTERVAL_MS = 1000;
static constexpr unsigned long COMMAND_TCP_RECONNECT_INTERVAL_MS = 3000;
static constexpr unsigned long WIFI_RETRY_INTERVAL_MS = 30000;
static constexpr unsigned long WIFI_CONNECT_TIMEOUT_MS = 15000;
static constexpr unsigned long DEFAULT_DEBOUNCE_MS = 35;
static constexpr unsigned long STARTUP_SETTLE_MS = 300;
static constexpr int WIFI_TX_POWER_QUARTER_DBM = 78;
static constexpr bool WIFI_USE_ALTERNATE_STA_MAC = false;
static constexpr bool WIFI_RELAX_PMF = true;
static constexpr int BUZZER_FREQ_HZ = 3000;
static constexpr int BUZZER_PWM_CHANNEL = 0;
static constexpr int BUZZER_PWM_RESOLUTION_BITS = 8;
static constexpr int BUZZER_PWM_DUTY_50_PERCENT = 128;
static constexpr int LED_ACTIVE_LEVEL = HIGH;

#ifndef COMMAND_SERVER_HOST_OCTETS
#define COMMAND_SERVER_HOST_OCTETS 0, 0, 0, 0
#endif

#ifndef COMMAND_SERVER_HOST_TEXT
#define COMMAND_SERVER_HOST_TEXT ""
#endif

static constexpr const char *PROVISIONING_AP_SSID = "AiLandLine-Setup";
static constexpr const char *PROVISIONING_AP_PASSWORD = "ailandline";
static constexpr int PROVISIONING_AP_CHANNEL = 6;
static constexpr unsigned long PROVISIONING_RECONNECT_DELAY_MS = 1000;
static constexpr unsigned int WIFI_FAILURES_BEFORE_PROVISIONING = 3;

static int hookPin = DEFAULT_HOOK_PIN;
static int buzzerPin = DEFAULT_BUZZER_PIN;
static int ledPin = DEFAULT_LED_PIN;
static int lastRaw = HIGH;
static int stableRaw = HIGH;
static bool buzzerOn = false;
static bool ledOn = false;
static bool udpReady = false;
static bool wifiConnectInProgress = false;
static bool preferencesReady = false;
static bool provisioningActive = false;
static bool provisioningStartPending = false;
static bool provisioningStartInProgress = false;
static bool provisioningReconnectPending = false;
static int lastStaDisconnectReason = 0;
static unsigned int wifiFailureCount = 0;
static int lastTargetChannel = 0;
static uint8_t lastTargetBssid[6] = {0};
static bool hasTargetBssid = false;
static unsigned long lastRawChangeMs = 0;
static unsigned long lastSampleMs = 0;
static unsigned long lastHeartbeatMs = 0;
static unsigned long lastWifiAttemptMs = 0;
static unsigned long lastCommandTcpConnectMs = 0;
static unsigned long provisioningReconnectAtMs = 0;
static unsigned long beepUntilMs = 0;
static unsigned long sampleIntervalMs = DEFAULT_SAMPLE_INTERVAL_MS;
static unsigned long debounceMs = DEFAULT_DEBOUNCE_MS;
static unsigned long seq = 0;
static String serialBuffer;
static String tcpCommandBuffer;
static String runtimeWifiSsid;
static String runtimeWifiPassword;
static String commandServerHostText = COMMAND_SERVER_HOST_TEXT;
static const char *provisioningStartReason = "requested";
static WiFiUDP telemetryUdp;
static WiFiUDP commandUdp;
static WiFiClient commandClient;
static DNSServer provisioningDns;
static Preferences preferences;
static WebServer provisioningServer(80);
static IPAddress commandServerHost;

static String macString(const uint8_t *mac);
static void beginWifiConnect();

static bool hasStaCredentials() {
  return runtimeWifiSsid.length() > 0;
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

static String wifiMac() {
  return WiFi.macAddress();
}

static const char *activeWifiSsid() {
  return runtimeWifiSsid.c_str();
}

static const char *activeWifiPassword() {
  return runtimeWifiPassword.c_str();
}

static String htmlEscape(const String &value) {
  String escaped = value;
  escaped.replace("&", "&amp;");
  escaped.replace("\"", "&quot;");
  escaped.replace("<", "&lt;");
  escaped.replace(">", "&gt;");
  return escaped;
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
  json += ",\"wifi_ssid\":\"" + runtimeWifiSsid + "\"";
  json += ",\"wifi_mac\":\"" + wifiMac() + "\"";
  json += ",\"wifi_ip\":\"" + wifiIp() + "\"";
  json += ",\"wifi_rssi\":" + String(wifiRssi());
  json += ",\"wifi_status\":" + String(static_cast<int>(WiFi.status()));
  json += ",\"wifi_disconnect_reason\":" + String(lastStaDisconnectReason);
  json += ",\"wifi_tx_power\":" + String(static_cast<int>(WiFi.getTxPower()));
  json += ",\"command_host\":\"" + commandServerHostText + "\"";
  json += ",\"provisioning_active\":" + String(provisioningActive ? "true" : "false");
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
  if (Serial && payload.indexOf("\"type\":\"sample\"") < 0) {
    Serial.println(payload);
  }
  sendUdp(payload);
}

static void publishState(const char *type, unsigned long now) {
  publish(stateJson(type, now));
}

static bool resolveCommandServerHost() {
  String host = commandServerHostText;
  host.trim();
  if (host.length() == 0 || host.equalsIgnoreCase("auto")) {
    if (WiFi.status() != WL_CONNECTED) {
      commandServerHost = IPAddress(0, 0, 0, 0);
      return false;
    }
    const IPAddress gateway = WiFi.gatewayIP();
    if (gateway.toString() == "0.0.0.0") {
      commandServerHost = IPAddress(0, 0, 0, 0);
      return false;
    }
    commandServerHost = gateway;
    return true;
  }
  if (commandServerHost.fromString(host)) {
    return true;
  }
  commandServerHost = IPAddress(COMMAND_SERVER_HOST_OCTETS);
  return commandServerHost.toString() != "0.0.0.0";
}

static void loadWifiSettings() {
  if (!preferencesReady) {
    preferencesReady = preferences.begin("ailandline", false);
  }
  if (preferencesReady) {
    runtimeWifiSsid = preferences.getString("wifi_ssid", WIFI_STA_SSID);
    runtimeWifiPassword = preferences.getString("wifi_pass", WIFI_STA_PASSWORD);
    commandServerHostText = preferences.getString("cmd_host", COMMAND_SERVER_HOST_TEXT);
  } else {
    runtimeWifiSsid = WIFI_STA_SSID;
    runtimeWifiPassword = WIFI_STA_PASSWORD;
    commandServerHostText = COMMAND_SERVER_HOST_TEXT;
  }
  commandServerHostText.trim();
  publish(
      String("{\"type\":\"wifi_event\",\"event\":\"settings_loaded\",\"ssid\":\"") + runtimeWifiSsid +
      "\",\"command_host\":\"" + commandServerHostText +
      "\",\"preferences\":" + String(preferencesReady ? "true" : "false") +
      "}");
}

static bool saveWifiSettings(const String &ssid, const String &password, const String &host) {
  if (!preferencesReady) {
    preferencesReady = preferences.begin("ailandline", false);
  }
  if (!preferencesReady || ssid.length() == 0) {
    return false;
  }
  const String nextHost = host.length() > 0 ? host : "auto";
  preferences.putString("wifi_ssid", ssid);
  preferences.putString("wifi_pass", password);
  preferences.putString("cmd_host", nextHost);
  runtimeWifiSsid = ssid;
  runtimeWifiPassword = password;
  commandServerHostText = nextHost;
  publish(
      String("{\"type\":\"wifi_event\",\"event\":\"settings_saved\",\"ssid\":\"") + runtimeWifiSsid +
      "\",\"command_host\":\"" + commandServerHostText +
      "\"}");
  return true;
}

static void handleProvisioningRoot() {
  String page;
  page.reserve(2400);
  page += F("<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">");
  page += F("<title>AiLandLine Wi-Fi Setup</title><style>body{font-family:system-ui,sans-serif;margin:24px;max-width:520px}label{display:block;margin:14px 0 6px}input{box-sizing:border-box;width:100%;padding:10px;font-size:16px}button{margin-top:18px;padding:10px 14px;font-size:16px}</style></head><body>");
  page += F("<h1>AiLandLine Wi-Fi Setup</h1><form method=\"post\" action=\"/save\">");
  page += F("<label>Wi-Fi SSID</label><input name=\"ssid\" value=\"");
  page += htmlEscape(runtimeWifiSsid);
  page += F("\" required>");
  page += F("<label>Wi-Fi Password</label><input name=\"password\" type=\"password\" value=\"");
  page += htmlEscape(runtimeWifiPassword);
  page += F("\">");
  page += F("<label>Computer command host (optional)</label><input name=\"host\" value=\"");
  page += htmlEscape(commandServerHostText);
  page += F("\" placeholder=\"auto\">");
  page += F("<button type=\"submit\">Save and reconnect</button></form>");
  page += F("<p>Leave host blank for auto. Auto uses the Wi-Fi gateway, which works for Windows Mobile Hotspot. On a normal router, the desktop app can still discover this device through UDP broadcast.</p>");
  page += F("<p>If this page does not open automatically, visit http://192.168.4.1/ after joining the setup Wi-Fi.</p>");
  page += F("</body></html>");
  provisioningServer.send(200, "text/html; charset=utf-8", page);
}

static void handleProvisioningSave() {
  String ssid = provisioningServer.arg("ssid");
  String password = provisioningServer.arg("password");
  String host = provisioningServer.arg("host");
  ssid.trim();
  host.trim();
  if (!saveWifiSettings(ssid, password, host)) {
    provisioningServer.send(400, "text/plain; charset=utf-8", "Missing SSID or NVS unavailable.");
    return;
  }
  provisioningReconnectPending = true;
  provisioningReconnectAtMs = millis() + PROVISIONING_RECONNECT_DELAY_MS;
  provisioningServer.send(200, "text/html; charset=utf-8", "<!doctype html><meta charset=\"utf-8\"><p>Saved. ESP32 is reconnecting.</p>");
}

static void startProvisioningPortal(const char *reason) {
  if (provisioningActive) {
    return;
  }
  provisioningStartPending = false;
  WiFi.setAutoReconnect(false);
  WiFi.mode(WIFI_AP);
  delay(100);
  const bool apStarted = WiFi.softAP(PROVISIONING_AP_SSID, PROVISIONING_AP_PASSWORD, PROVISIONING_AP_CHANNEL, false, 4);
  provisioningDns.start(53, "*", WiFi.softAPIP());
  provisioningServer.on("/", HTTP_GET, handleProvisioningRoot);
  provisioningServer.on("/save", HTTP_POST, handleProvisioningSave);
  provisioningServer.onNotFound(handleProvisioningRoot);
  provisioningServer.begin();
  provisioningActive = true;
  publish(
      String("{\"type\":\"provisioning\",\"event\":\"started\",\"reason\":\"") + reason +
      "\",\"ok\":" + String(apStarted ? "true" : "false") +
      ",\"ap_ssid\":\"" + PROVISIONING_AP_SSID +
      "\",\"ap_channel\":" + String(PROVISIONING_AP_CHANNEL) +
      ",\"ap_ip\":\"" + WiFi.softAPIP().toString() +
      "\"}");
}

static void requestProvisioningPortal(const char *reason) {
  if (provisioningActive || provisioningStartPending || provisioningStartInProgress) {
    return;
  }
  provisioningStartReason = reason;
  provisioningStartPending = true;
  publish(String("{\"type\":\"provisioning\",\"event\":\"requested\",\"reason\":\"") + reason + "\"}");
}

static void stopProvisioningPortal(const char *reason) {
  if (!provisioningActive) {
    return;
  }
  provisioningServer.stop();
  provisioningDns.stop();
  WiFi.softAPdisconnect(true);
  provisioningActive = false;
  provisioningStartPending = false;
  provisioningStartInProgress = false;
  provisioningReconnectPending = false;
  publish(String("{\"type\":\"provisioning\",\"event\":\"stopped\",\"reason\":\"") + reason + "\"}");
}

static void pollProvisioningPortal() {
  if (provisioningStartPending && !provisioningActive) {
    const char *reason = provisioningStartReason;
    provisioningStartPending = false;
    provisioningStartInProgress = true;
    WiFi.disconnect(false, false);
    delay(50);
    startProvisioningPortal(reason);
    provisioningStartInProgress = false;
  }
  if (provisioningActive) {
    provisioningDns.processNextRequest();
    provisioningServer.handleClient();
  }
  if (provisioningReconnectPending && static_cast<long>(millis() - provisioningReconnectAtMs) >= 0) {
    stopProvisioningPortal("saved");
    wifiFailureCount = 0;
    WiFi.disconnect(false, false);
    delay(100);
    beginWifiConnect();
  }
}

static void applyWifiCompatibilitySettings() {
  WiFi.setSleep(false);
  WiFi.setTxPower(static_cast<wifi_power_t>(WIFI_TX_POWER_QUARTER_DBM));

  publish(
      String("{\"type\":\"wifi_event\",\"event\":\"compat_settings\",\"mode\":\"minimal\"") +
      ",\"tx_power\":" + String(static_cast<int>(WiFi.getTxPower())) +
      "}");
}

static void applyWifiStationIdentity() {
  uint8_t mac[6] = {0};
  const esp_err_t readErr = esp_wifi_get_mac(WIFI_IF_STA, mac);
  if (readErr != ESP_OK) {
    publish(String("{\"type\":\"wifi_event\",\"event\":\"sta_mac_read_failed\",\"err\":") + String(static_cast<int>(readErr)) + "}");
    return;
  }

  const String factoryMac = macString(mac);
  esp_err_t setErr = ESP_OK;
  if (WIFI_USE_ALTERNATE_STA_MAC) {
    mac[0] = (mac[0] | 0x02) & 0xFE;
    mac[5] ^= 0x5A;
    setErr = esp_wifi_set_mac(WIFI_IF_STA, mac);
  }

  publish(
      String("{\"type\":\"wifi_event\",\"event\":\"sta_mac\",\"alternate\":") +
      String(WIFI_USE_ALTERNATE_STA_MAC ? "true" : "false") +
      ",\"factory\":\"" + factoryMac +
      "\",\"active\":\"" + macString(mac) +
      "\",\"err\":" + String(static_cast<int>(setErr)) +
      "}");
}

static void connectWifiWithRelaxedPmf() {
  const wl_status_t beginStatus = WiFi.begin(activeWifiSsid(), activeWifiPassword(), 0, nullptr, false);
  wifi_config_t config;
  memset(&config, 0, sizeof(config));
  esp_err_t getErr = esp_wifi_get_config(WIFI_IF_STA, &config);
  esp_err_t setErr = ESP_OK;
  if (getErr == ESP_OK && WIFI_RELAX_PMF) {
    config.sta.pmf_cfg.capable = false;
    config.sta.pmf_cfg.required = false;
    setErr = esp_wifi_set_config(WIFI_IF_STA, &config);
  }
  const esp_err_t connectErr = esp_wifi_connect();
  publish(
      String("{\"type\":\"wifi_event\",\"event\":\"connect_config\",\"begin_status\":") + String(static_cast<int>(beginStatus)) +
      ",\"relax_pmf\":" + String(WIFI_RELAX_PMF ? "true" : "false") +
      ",\"get_err\":" + String(static_cast<int>(getErr)) +
      ",\"set_err\":" + String(static_cast<int>(setErr)) +
      ",\"connect_err\":" + String(static_cast<int>(connectErr)) +
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

  publish(String("{\"type\":\"wifi_event\",\"event\":\"scan_start\",\"ssid\":\"") + runtimeWifiSsid + "\"}");
  const int networkCount = WiFi.scanNetworks(false, true);
  publish(String("{\"type\":\"wifi_event\",\"event\":\"scan_done\",\"count\":") + String(networkCount) + "}");

  int bestIndex = -1;
  int bestRssi = -1000;

  for (int i = 0; i < networkCount; ++i) {
    const String ssid = WiFi.SSID(i);
    if (ssid != runtimeWifiSsid) {
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
        String("{\"type\":\"wifi_event\",\"event\":\"scan_selected\",\"ssid\":\"") + runtimeWifiSsid +
        "\",\"bssid\":\"" + macString(lastTargetBssid) +
        "\",\"channel\":" + String(lastTargetChannel) +
        ",\"rssi\":" + String(bestRssi) +
        "}");
  } else {
    publish(String("{\"type\":\"wifi_event\",\"event\":\"scan_no_match\",\"ssid\":\"") + runtimeWifiSsid + "\"}");
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
  configurePins(intField(command, "hook_pin", hookPin),
                intField(command, "buzzer_pin", buzzerPin),
                intField(command, "led_pin", ledPin));
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
  } else if (commandHasType(command, "provision") || commandHasType(command, "wifi_setup") || commandHasType(command, "setup_portal")) {
    setBuzzer(false);
    requestProvisioningPortal("command");
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

static void maintainTcpCommandClient(unsigned long now) {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  if (!resolveCommandServerHost()) {
    return;
  }

  if (!commandClient.connected()) {
    if ((now - lastCommandTcpConnectMs) < COMMAND_TCP_RECONNECT_INTERVAL_MS) {
      return;
    }
    lastCommandTcpConnectMs = now;
    tcpCommandBuffer = "";
    if (commandClient.connect(commandServerHost, COMMAND_TCP_PORT, 250)) {
      commandClient.setNoDelay(true);
      commandClient.print(String("{\"type\":\"hello\",\"device\":\"ailandline-c3\",\"ms\":") + String(now) + "}\n");
      publish(String("{\"type\":\"tcp_command\",\"event\":\"connected\",\"host\":\"") + commandServerHostText + "\",\"port\":" + String(COMMAND_TCP_PORT) + "}");
    }
    return;
  }

  while (commandClient.available() > 0) {
    const char c = static_cast<char>(commandClient.read());
    if (c == '\n') {
      publish(
          String("{\"type\":\"command_received\",\"transport\":\"tcp\",\"bytes\":") + String(tcpCommandBuffer.length()) +
          ",\"port\":" + String(COMMAND_TCP_PORT) +
          "}");
      handleCommand(tcpCommandBuffer);
      tcpCommandBuffer = "";
    } else if (c != '\r' && tcpCommandBuffer.length() < 400) {
      tcpCommandBuffer += c;
    }
  }
}

static void startUdpCommandListener() {
  commandUdp.stop();
  udpReady = commandUdp.begin(COMMAND_PORT) == 1;
}

static void beginWifiConnect() {
  if (!hasStaCredentials()) {
    publish("{\"type\":\"wifi_event\",\"event\":\"missing_credentials\"}");
    requestProvisioningPortal("missing_credentials");
    return;
  }

  const unsigned long now = millis();
  if (wifiConnectInProgress && (now - lastWifiAttemptMs) < WIFI_CONNECT_TIMEOUT_MS) {
    return;
  }

  lastWifiAttemptMs = now;
  udpReady = false;
  WiFi.mode(WIFI_STA);
  applyWifiStationIdentity();
  applyWifiCompatibilitySettings();
  WiFi.setAutoReconnect(true);

  scanForConfiguredWifi();
  connectWifiWithRelaxedPmf();
  wifiConnectInProgress = true;
  publish(String("{\"type\":\"wifi_event\",\"event\":\"connect_start\",\"mode\":\"ssid_only\",\"ssid\":\"") + runtimeWifiSsid + "\"}");
}

static void handleWifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    lastStaDisconnectReason = 0;
    wifiFailureCount = 0;
    wifiConnectInProgress = false;
    stopProvisioningPortal("wifi_connected");
    startUdpCommandListener();
    publishState("wifi_connected", millis());
  } else if (event == ARDUINO_EVENT_WIFI_STA_CONNECTED) {
    publish("{\"type\":\"wifi_event\",\"event\":\"sta_connected\"}");
  } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    lastStaDisconnectReason = info.wifi_sta_disconnected.reason;
    wifiConnectInProgress = false;
    udpReady = false;
    commandUdp.stop();
    publishState("wifi_disconnected", millis());
    if (provisioningActive || provisioningStartInProgress) {
      return;
    }
    ++wifiFailureCount;
    if (wifiFailureCount >= WIFI_FAILURES_BEFORE_PROVISIONING) {
      requestProvisioningPortal("auth_failures");
    }
  }
}

static void maintainWifi(unsigned long now) {
  if (!hasStaCredentials()) {
    requestProvisioningPortal("missing_credentials");
    return;
  }

  if (provisioningActive || provisioningStartPending) {
    return;
  }

  if (WiFi.status() == WL_CONNECTED) {
    if (!udpReady) {
      startUdpCommandListener();
    }
    return;
  }

  if (wifiConnectInProgress && (now - lastWifiAttemptMs) < WIFI_CONNECT_TIMEOUT_MS) {
    return;
  }

  if ((now - lastWifiAttemptMs) >= WIFI_RETRY_INTERVAL_MS) {
    wifiConnectInProgress = false;
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
  loadWifiSettings();

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
  pollProvisioningPortal();
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

  maintainTcpCommandClient(now);

  delay(2);
}
