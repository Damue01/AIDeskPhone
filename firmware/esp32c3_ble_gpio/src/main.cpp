#include <Arduino.h>
#include <ArduinoJson.h>
#include <vector>
#include <NimBLEDevice.h>
#include <NimBLEHIDDevice.h>
#include <NimBLEAdvertisementData.h>
#include "nvs.h"
#include "nvs_flash.h"
#include "hid_keyboard_reports.h"

// ESP32-C3 Super Mini: pad marked "1" / "GPIO1" / "A1".
// Hybrid mode: the board owns classification and BLE HID actions. USB serial is
// only a configuration and live-log channel for the local web console.
static constexpr int INPUT_PIN = 1;
static constexpr int ADC_SAMPLE_COUNT = 4;
static constexpr int ADC_STARTUP_DISCARD_COUNT = 6;
static constexpr unsigned long ADC_STARTUP_SETTLE_MS = 800;
static constexpr unsigned long REPORT_STEP_DELAY_MS = 75;
static constexpr unsigned long SENSOR_REPORT_INTERVAL_MS = 50;
static constexpr size_t SERIAL_RX_BUFFER_SIZE = 2048;

static const char *DEVICE_NAME = "AIDeskPhoneKB";
static const char *FIRMWARE_VERSION = "hybrid-ble-config-v1";

static constexpr uint8_t HID_MOD_LEFT_ALT = 0x04;
static constexpr uint8_t HID_KEY_ESCAPE = 0x29;
static constexpr uint8_t HID_KEY_TAB = 0x2B;
static constexpr uint8_t HID_KEY_SPACE = 0x2C;

struct DeviceConfig {
  bool adcLowMeansPressed = true;
  int pressThreshold = 75;
  int releaseThreshold = 92;
  int strongLowPressThreshold = 45;
  int strongHighPressThreshold = 120;
  unsigned long debounceMs = 30;
  unsigned long pressLockoutMs = 350;
  int pressScoreStep = 2;
  int strongPressScoreStep = 3;
  int releaseScoreStep = 1;
  int scoreMax = 8;
  int scoreTrigger = 5;
  unsigned long peakHoldMs = 350;
  unsigned long sampleIntervalMs = SENSOR_REPORT_INTERVAL_MS;
  bool enableActions = true;
  char pressAction[80] = "ctrl+win+shift";
  char releaseAction[80] = "ctrl+win+shift,delay:1000,enter";
};

static DeviceConfig config;

static NimBLEServer *server = nullptr;
static NimBLEHIDDevice *hid = nullptr;
static NimBLECharacteristic *inputReport = nullptr;
static bool connected = false;

static bool stablePressed = false;
static bool rawPressed = false;
static int phonePressScore = 0;
static unsigned long lastChangeMs = 0;
static unsigned long lastPressEvidenceMs = 0;
static unsigned long lastPressTriggerMs = 0;
static unsigned long lastReportMs = 0;
static String serialBuffer;

static uint8_t hidReportMap[] = {
  0x05, 0x01,
  0x09, 0x06,
  0xA1, 0x01,
  0x85, REPORT_ID_KEYBOARD,
  0x05, 0x07,
  0x19, 0xE0,
  0x29, 0xE7,
  0x15, 0x00,
  0x25, 0x01,
  0x75, 0x01,
  0x95, 0x08,
  0x81, 0x02,
  0x95, 0x01,
  0x75, 0x08,
  0x81, 0x01,
  0x95, 0x05,
  0x75, 0x01,
  0x05, 0x08,
  0x19, 0x01,
  0x29, 0x05,
  0x91, 0x02,
  0x95, 0x01,
  0x75, 0x03,
  0x91, 0x01,
  0x95, 0x06,
  0x75, 0x08,
  0x15, 0x00,
  0x25, 0x65,
  0x05, 0x07,
  0x19, 0x00,
  0x29, 0x65,
  0x81, 0x00,
  0xC0
};

class ServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer *pServer, NimBLEConnInfo &connInfo) override {
    connected = true;
    Serial.println("{\"type\":\"ble\",\"state\":\"connected\"}");
  }

  void onDisconnect(NimBLEServer *pServer, NimBLEConnInfo &connInfo, int reason) override {
    connected = false;
    Serial.println("{\"type\":\"ble\",\"state\":\"disconnected\"}");
    NimBLEDevice::startAdvertising();
  }
};

static void ensureNvsReady() {
  esp_err_t err = nvs_flash_init();
  if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
    nvs_flash_erase();
    nvs_flash_init();
  }
}

static int nvsGetI32(nvs_handle_t handle, const char *key, int fallback) {
  int32_t value = fallback;
  nvs_get_i32(handle, key, &value);
  return static_cast<int>(value);
}

static unsigned long nvsGetU32(nvs_handle_t handle, const char *key, unsigned long fallback) {
  uint32_t value = static_cast<uint32_t>(fallback);
  nvs_get_u32(handle, key, &value);
  return static_cast<unsigned long>(value);
}

static bool nvsGetBool(nvs_handle_t handle, const char *key, bool fallback) {
  uint8_t value = fallback ? 1 : 0;
  nvs_get_u8(handle, key, &value);
  return value != 0;
}

static void nvsGetString(nvs_handle_t handle, const char *key, char *target, size_t targetSize, const char *fallback) {
  strlcpy(target, fallback, targetSize);
  size_t requiredSize = targetSize;
  nvs_get_str(handle, key, target, &requiredSize);
}

static void loadConfig() {
  ensureNvsReady();
  nvs_handle_t handle;
  if (nvs_open("aideskphone", NVS_READONLY, &handle) != ESP_OK) {
    return;
  }

  config.adcLowMeansPressed = nvsGetBool(handle, "adcLow", config.adcLowMeansPressed);
  config.pressThreshold = nvsGetI32(handle, "pressTh", config.pressThreshold);
  config.releaseThreshold = nvsGetI32(handle, "releaseTh", config.releaseThreshold);
  config.strongLowPressThreshold = nvsGetI32(handle, "strongLo", config.strongLowPressThreshold);
  config.strongHighPressThreshold = nvsGetI32(handle, "strongHi", config.strongHighPressThreshold);
  config.debounceMs = nvsGetU32(handle, "debounce", config.debounceMs);
  config.pressLockoutMs = nvsGetU32(handle, "lockout", config.pressLockoutMs);
  config.pressScoreStep = nvsGetI32(handle, "pressStep", config.pressScoreStep);
  config.strongPressScoreStep = nvsGetI32(handle, "strongStep", config.strongPressScoreStep);
  config.releaseScoreStep = nvsGetI32(handle, "releaseStep", config.releaseScoreStep);
  config.scoreMax = nvsGetI32(handle, "scoreMax", config.scoreMax);
  config.scoreTrigger = nvsGetI32(handle, "scoreTrig", config.scoreTrigger);
  config.peakHoldMs = nvsGetU32(handle, "peakHold", config.peakHoldMs);
  config.sampleIntervalMs = nvsGetU32(handle, "interval", config.sampleIntervalMs);
  config.enableActions = nvsGetBool(handle, "actions", config.enableActions);
  nvsGetString(handle, "pressAct", config.pressAction, sizeof(config.pressAction), config.pressAction);
  nvsGetString(handle, "relAct", config.releaseAction, sizeof(config.releaseAction), config.releaseAction);
  nvs_close(handle);
}

static void saveConfig() {
  ensureNvsReady();
  nvs_handle_t handle;
  if (nvs_open("aideskphone", NVS_READWRITE, &handle) != ESP_OK) {
    Serial.println("{\"type\":\"error\",\"message\":\"nvs_open_failed\"}");
    return;
  }

  nvs_set_u8(handle, "adcLow", config.adcLowMeansPressed ? 1 : 0);
  nvs_set_i32(handle, "pressTh", config.pressThreshold);
  nvs_set_i32(handle, "releaseTh", config.releaseThreshold);
  nvs_set_i32(handle, "strongLo", config.strongLowPressThreshold);
  nvs_set_i32(handle, "strongHi", config.strongHighPressThreshold);
  nvs_set_u32(handle, "debounce", static_cast<uint32_t>(config.debounceMs));
  nvs_set_u32(handle, "lockout", static_cast<uint32_t>(config.pressLockoutMs));
  nvs_set_i32(handle, "pressStep", config.pressScoreStep);
  nvs_set_i32(handle, "strongStep", config.strongPressScoreStep);
  nvs_set_i32(handle, "releaseStep", config.releaseScoreStep);
  nvs_set_i32(handle, "scoreMax", config.scoreMax);
  nvs_set_i32(handle, "scoreTrig", config.scoreTrigger);
  nvs_set_u32(handle, "peakHold", static_cast<uint32_t>(config.peakHoldMs));
  nvs_set_u32(handle, "interval", static_cast<uint32_t>(config.sampleIntervalMs));
  nvs_set_u8(handle, "actions", config.enableActions ? 1 : 0);
  nvs_set_str(handle, "pressAct", config.pressAction);
  nvs_set_str(handle, "relAct", config.releaseAction);
  nvs_commit(handle);
  nvs_close(handle);
}

static void addConfigToJson(JsonObject target) {
  target["adc_low_means_pressed"] = config.adcLowMeansPressed;
  target["press_threshold"] = config.pressThreshold;
  target["release_threshold"] = config.releaseThreshold;
  target["strong_low_press_threshold"] = config.strongLowPressThreshold;
  target["strong_high_press_threshold"] = config.strongHighPressThreshold;
  target["debounce_ms"] = config.debounceMs;
  target["press_lockout_ms"] = config.pressLockoutMs;
  target["press_score_step"] = config.pressScoreStep;
  target["strong_press_score_step"] = config.strongPressScoreStep;
  target["release_score_step"] = config.releaseScoreStep;
  target["score_max"] = config.scoreMax;
  target["score_trigger"] = config.scoreTrigger;
  target["peak_hold_ms"] = config.peakHoldMs;
  target["sample_interval_ms"] = config.sampleIntervalMs;
  target["enable_actions"] = config.enableActions;
  target["press_action"] = config.pressAction;
  target["release_action"] = config.releaseAction;
}

static void printConfigJson(const char *eventType = "config") {
  JsonDocument doc;
  doc["type"] = eventType;
  doc["version"] = FIRMWARE_VERSION;
  JsonObject cfg = doc["config"].to<JsonObject>();
  addConfigToJson(cfg);
  serializeJson(doc, Serial);
  Serial.println();
}

static void printHelloJson() {
  JsonDocument doc;
  doc["type"] = "hello";
  doc["version"] = FIRMWARE_VERSION;
  doc["pin"] = INPUT_PIN;
  doc["ble_name"] = DEVICE_NAME;
  doc["state"] = stablePressed ? "PRESSED" : "RELEASED";
  serializeJson(doc, Serial);
  Serial.println();
  printConfigJson();
}

static int readInputAdcRaw() {
  uint32_t sum = 0;

  for (int i = 0; i < ADC_SAMPLE_COUNT; ++i) {
    sum += analogRead(INPUT_PIN);
  }

  return static_cast<int>(sum / ADC_SAMPLE_COUNT);
}

static int readInputDigitalState() {
  return digitalRead(INPUT_PIN);
}

static void discardStartupAdcSamples() {
  for (int i = 0; i < ADC_STARTUP_DISCARD_COUNT; ++i) {
    (void)readInputAdcRaw();
    delay(20);
  }
}

static bool isPressEvidenceAdc(int analogRaw) {
  return config.adcLowMeansPressed ? analogRaw <= config.pressThreshold : analogRaw >= config.pressThreshold;
}

static bool isStrongPressEvidenceAdc(int analogRaw) {
  return config.adcLowMeansPressed ? analogRaw <= config.strongLowPressThreshold
                                   : analogRaw >= config.strongHighPressThreshold;
}

static bool isReleaseEvidenceAdc(int analogRaw) {
  return config.adcLowMeansPressed ? analogRaw >= config.releaseThreshold : analogRaw <= config.releaseThreshold;
}

static bool hasRecentPressPeak(unsigned long now) {
  return lastPressEvidenceMs != 0 && (now - lastPressEvidenceMs) <= config.peakHoldMs;
}

static void updatePhonePressScore(int analogRaw, unsigned long now) {
  if (isStrongPressEvidenceAdc(analogRaw)) {
    lastPressEvidenceMs = now;
    phonePressScore = min(config.scoreMax, phonePressScore + config.strongPressScoreStep);
    return;
  }

  if (isPressEvidenceAdc(analogRaw)) {
    lastPressEvidenceMs = now;
    phonePressScore = min(config.scoreMax, phonePressScore + config.pressScoreStep);
    return;
  }

  if (isReleaseEvidenceAdc(analogRaw)) {
    if (hasRecentPressPeak(now)) {
      return;
    }
    phonePressScore = max(0, phonePressScore - config.releaseScoreStep);
    return;
  }

  if (!hasRecentPressPeak(now) && phonePressScore > 0) {
    phonePressScore = max(0, phonePressScore - 1);
  }
}

static bool stateFromPressScore(bool fallbackPressed) {
  if (phonePressScore >= config.scoreTrigger) {
    return true;
  }
  if (phonePressScore <= 0) {
    return false;
  }
  return fallbackPressed;
}

static void resetClassifier(int analogRaw) {
  if (isPressEvidenceAdc(analogRaw)) {
    phonePressScore = config.scoreMax;
    stablePressed = true;
    rawPressed = true;
    lastPressEvidenceMs = millis();
  } else {
    phonePressScore = 0;
    stablePressed = false;
    rawPressed = false;
    lastPressEvidenceMs = 0;
  }
  lastChangeMs = millis();
}

static void sendKeyboardReport(const KeyboardReport &report) {
  if (!connected || inputReport == nullptr) {
    Serial.println("{\"type\":\"action\",\"result\":\"skipped\",\"reason\":\"ble_not_connected\"}");
    return;
  }

  inputReport->setValue(reinterpret_cast<const uint8_t *>(&report), sizeof(report));
  inputReport->notify();
  delay(REPORT_STEP_DELAY_MS);
}

static void tapKeyboardReport(const KeyboardReport &report) {
  sendKeyboardReport(report);
  sendKeyboardReport(REPORT_EMPTY);
}

static char *trimToken(char *value) {
  while (*value == ' ' || *value == '\t') {
    ++value;
  }

  char *end = value + strlen(value);
  while (end > value && (end[-1] == ' ' || end[-1] == '\t' || end[-1] == '\r' || end[-1] == '\n')) {
    *--end = '\0';
  }

  return value;
}

static bool equalsIgnoreCase(const char *left, const char *right) {
  while (*left != '\0' && *right != '\0') {
    if (tolower(*left) != tolower(*right)) {
      return false;
    }
    ++left;
    ++right;
  }
  return *left == '\0' && *right == '\0';
}

static uint8_t keyCodeForToken(const char *token) {
  if (equalsIgnoreCase(token, "enter")) {
    return HID_KEY_ENTER;
  }
  if (equalsIgnoreCase(token, "esc") || equalsIgnoreCase(token, "escape")) {
    return HID_KEY_ESCAPE;
  }
  if (equalsIgnoreCase(token, "tab")) {
    return HID_KEY_TAB;
  }
  if (equalsIgnoreCase(token, "space")) {
    return HID_KEY_SPACE;
  }
  if (strlen(token) == 1) {
    const char c = tolower(token[0]);
    if (c >= 'a' && c <= 'z') {
      return 0x04 + static_cast<uint8_t>(c - 'a');
    }
    if (c >= '1' && c <= '9') {
      return 0x1E + static_cast<uint8_t>(c - '1');
    }
    if (c == '0') {
      return 0x27;
    }
  }
  return 0;
}

static void tapActionSegment(char *segment) {
  KeyboardReport report = REPORT_EMPTY;
  int keySlot = 0;

  char *tokenContext = nullptr;
  char *token = strtok_r(segment, "+", &tokenContext);
  while (token != nullptr) {
    token = trimToken(token);

    if (equalsIgnoreCase(token, "ctrl") || equalsIgnoreCase(token, "control")) {
      report.modifiers |= HID_MOD_LEFT_CTRL;
    } else if (equalsIgnoreCase(token, "shift")) {
      report.modifiers |= HID_MOD_LEFT_SHIFT;
    } else if (equalsIgnoreCase(token, "win") || equalsIgnoreCase(token, "gui") || equalsIgnoreCase(token, "meta")) {
      report.modifiers |= HID_MOD_LEFT_GUI;
    } else if (equalsIgnoreCase(token, "alt")) {
      report.modifiers |= HID_MOD_LEFT_ALT;
    } else {
      const uint8_t keyCode = keyCodeForToken(token);
      if (keyCode != 0 && keySlot < 6) {
        report.keys[keySlot++] = keyCode;
      }
    }

    token = strtok_r(nullptr, "+", &tokenContext);
  }

  tapKeyboardReport(report);
}

static void runActionString(const char *action) {
  if (!config.enableActions) {
    Serial.println("{\"type\":\"action\",\"result\":\"skipped\",\"reason\":\"actions_disabled\"}");
    return;
  }

  char buffer[160];
  strncpy(buffer, action, sizeof(buffer) - 1);
  buffer[sizeof(buffer) - 1] = '\0';

  char *segmentContext = nullptr;
  char *segment = strtok_r(buffer, ",", &segmentContext);
  while (segment != nullptr) {
    segment = trimToken(segment);

    if (strncmp(segment, "delay:", 6) == 0) {
      delay(static_cast<unsigned long>(atoi(segment + 6)));
    } else if (*segment != '\0') {
      tapActionSegment(segment);
    }

    segment = strtok_r(nullptr, ",", &segmentContext);
  }
}

static void tapVoiceHotkeyThenEnter() {
  runActionString("ctrl+win+shift,delay:1000,enter");
}

static void printEventJson(const char *event, bool pressed, int analogRaw, const char *action) {
  JsonDocument doc;
  doc["type"] = "event";
  doc["event"] = event;
  doc["state"] = pressed ? "PRESSED" : "RELEASED";
  doc["adc"] = analogRaw;
  doc["score"] = phonePressScore;
  doc["action"] = action;
  doc["ble_connected"] = connected;
  serializeJson(doc, Serial);
  Serial.println();
}

static void handlePhoneStateEvent(bool pressed, int analogRaw) {
  const char *action = pressed ? config.pressAction : config.releaseAction;
  printEventJson("state_change", pressed, analogRaw, action);
  runActionString(action);
}

static void printSensorJson(int analogRaw, int digitalRaw) {
  JsonDocument doc;
  doc["type"] = "sample";
  doc["version"] = FIRMWARE_VERSION;
  doc["ms"] = millis();
  doc["pin"] = INPUT_PIN;
  doc["adc"] = analogRaw;
  doc["digital"] = digitalRaw == LOW ? "LOW" : "HIGH";
  doc["state"] = stablePressed ? "PRESSED" : "RELEASED";
  doc["score"] = phonePressScore;
  doc["ble_connected"] = connected;
  serializeJson(doc, Serial);
  Serial.println();
}

static void applyJsonConfig(JsonObject incoming) {
  if (incoming["adc_low_means_pressed"].is<bool>()) {
    config.adcLowMeansPressed = incoming["adc_low_means_pressed"];
  }
  if (incoming["press_threshold"].is<int>()) {
    config.pressThreshold = incoming["press_threshold"];
  }
  if (incoming["release_threshold"].is<int>()) {
    config.releaseThreshold = incoming["release_threshold"];
  }
  if (incoming["strong_low_press_threshold"].is<int>()) {
    config.strongLowPressThreshold = incoming["strong_low_press_threshold"];
  }
  if (incoming["strong_high_press_threshold"].is<int>()) {
    config.strongHighPressThreshold = incoming["strong_high_press_threshold"];
  }
  if (incoming["debounce_ms"].is<unsigned long>()) {
    config.debounceMs = incoming["debounce_ms"];
  }
  if (incoming["press_lockout_ms"].is<unsigned long>()) {
    config.pressLockoutMs = incoming["press_lockout_ms"];
  }
  if (incoming["press_score_step"].is<int>()) {
    config.pressScoreStep = incoming["press_score_step"];
  }
  if (incoming["strong_press_score_step"].is<int>()) {
    config.strongPressScoreStep = incoming["strong_press_score_step"];
  }
  if (incoming["release_score_step"].is<int>()) {
    config.releaseScoreStep = incoming["release_score_step"];
  }
  if (incoming["score_max"].is<int>()) {
    config.scoreMax = incoming["score_max"];
  }
  if (incoming["score_trigger"].is<int>()) {
    config.scoreTrigger = incoming["score_trigger"];
  }
  if (incoming["peak_hold_ms"].is<unsigned long>()) {
    config.peakHoldMs = incoming["peak_hold_ms"];
  }
  if (incoming["sample_interval_ms"].is<unsigned long>()) {
    config.sampleIntervalMs = incoming["sample_interval_ms"];
  }
  if (incoming["enable_actions"].is<bool>()) {
    config.enableActions = incoming["enable_actions"];
  }
  if (incoming["press_action"].is<const char *>()) {
    strlcpy(config.pressAction, incoming["press_action"], sizeof(config.pressAction));
  }
  if (incoming["release_action"].is<const char *>()) {
    strlcpy(config.releaseAction, incoming["release_action"], sizeof(config.releaseAction));
  }
}

static void handleSerialJsonCommand(const String &line) {
  JsonDocument doc;
  DeserializationError error = deserializeJson(doc, line);
  if (error) {
    Serial.println("{\"type\":\"error\",\"message\":\"invalid_json\"}");
    return;
  }

  const char *type = doc["type"] | "";
  if (strcmp(type, "config") == 0) {
    JsonObject incoming = doc["config"].as<JsonObject>();
    applyJsonConfig(incoming);
    saveConfig();
    resetClassifier(readInputAdcRaw());
    printConfigJson("config_saved");
  } else if (strcmp(type, "get_config") == 0) {
    printConfigJson();
  } else if (strcmp(type, "simulate_press") == 0) {
    handlePhoneStateEvent(true, readInputAdcRaw());
  } else if (strcmp(type, "simulate_release") == 0) {
    handlePhoneStateEvent(false, readInputAdcRaw());
  } else if (strcmp(type, "ping") == 0) {
    printHelloJson();
  } else {
    Serial.println("{\"type\":\"error\",\"message\":\"unknown_command\"}");
  }
}

static void pollSerialCommands() {
  while (Serial.available() > 0) {
    const char c = static_cast<char>(Serial.read());
    if (c == '\n') {
      String line = serialBuffer;
      serialBuffer = "";
      line.trim();
      if (line.length() > 0) {
        handleSerialJsonCommand(line);
      }
    } else if (c != '\r' && serialBuffer.length() < 1200) {
      serialBuffer += c;
    }
  }
}

static void setupBleKeyboard() {
  NimBLEDevice::init(DEVICE_NAME);
  NimBLEDevice::setSecurityAuth(true, false, false);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);

  server = NimBLEDevice::createServer();
  server->setCallbacks(new ServerCallbacks());

  hid = new NimBLEHIDDevice(server);
  inputReport = hid->getInputReport(REPORT_ID_KEYBOARD);
  hid->getOutputReport(REPORT_ID_KEYBOARD);
  hid->setManufacturer("AIDeskPhone");
  hid->setPnp(0x02, 0x303A, 0x1001, 0x0100);
  hid->setHidInfo(0x00, 0x01);
  hid->setReportMap(hidReportMap, sizeof(hidReportMap));
  hid->setBatteryLevel(100);
  inputReport->setValue(reinterpret_cast<const uint8_t *>(&REPORT_EMPTY), sizeof(REPORT_EMPTY));
  server->start();

  NimBLEAdvertising *advertising = NimBLEDevice::getAdvertising();
  NimBLEAdvertisementData advertisementData;
  std::vector<NimBLEUUID> hidServices = {NimBLEUUID(static_cast<uint16_t>(0x1812))};
  advertisementData.setFlags(0x06);
  advertisementData.setName(DEVICE_NAME);
  advertisementData.setAppearance(HID_KEYBOARD);
  advertisementData.setCompleteServices16(hidServices);
  advertising->setAdvertisementData(advertisementData);
  advertising->enableScanResponse(false);
  advertising->start();
}

void setup() {
  Serial.setRxBufferSize(SERIAL_RX_BUFFER_SIZE);
  Serial.begin(115200);
  serialBuffer.reserve(SERIAL_RX_BUFFER_SIZE);
  delay(200);

  pinMode(INPUT_PIN, INPUT_PULLUP);
  analogReadResolution(12);
  delay(ADC_STARTUP_SETTLE_MS);
  discardStartupAdcSamples();

  loadConfig();
  resetClassifier(readInputAdcRaw());
  setupBleKeyboard();
  printHelloJson();
  lastReportMs = millis();
}

void loop() {
  pollSerialCommands();

  const unsigned long now = millis();
  const int analogRaw = readInputAdcRaw();
  const int digitalRaw = readInputDigitalState();

  updatePhonePressScore(analogRaw, now);
  const bool nextRawPressed = stateFromPressScore(rawPressed);

  if (nextRawPressed != rawPressed) {
    rawPressed = nextRawPressed;
    lastChangeMs = now;
  }

  if ((now - lastChangeMs) >= config.debounceMs && rawPressed != stablePressed) {
    bool shouldHandleEvent = true;
    stablePressed = rawPressed;

    if (stablePressed) {
      if (lastPressTriggerMs != 0 && (now - lastPressTriggerMs) < config.pressLockoutMs) {
        shouldHandleEvent = false;
        printEventJson("press_lockout", stablePressed, analogRaw, "");
      } else {
        lastPressTriggerMs = now;
      }
    }

    if (shouldHandleEvent) {
      handlePhoneStateEvent(stablePressed, analogRaw);
    }
  }

  if ((now - lastReportMs) >= config.sampleIntervalMs) {
    lastReportMs = now;
    printSensorJson(analogRaw, digitalRaw);
  }

  delay(5);
}
