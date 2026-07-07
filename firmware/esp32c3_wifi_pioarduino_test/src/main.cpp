#include <Arduino.h>
#include <WiFi.h>

#if __has_include("wifi_credentials.h")
#include "wifi_credentials.h"
#endif

#ifndef WIFI_STA_SSID
#define WIFI_STA_SSID ""
#endif

#ifndef WIFI_STA_PASSWORD
#define WIFI_STA_PASSWORD ""
#endif

static int lastDisconnectReason = 0;
static unsigned long lastPrintMs = 0;

static const char *statusName(wl_status_t status) {
  switch (status) {
    case WL_IDLE_STATUS:
      return "IDLE";
    case WL_NO_SSID_AVAIL:
      return "NO_SSID";
    case WL_SCAN_COMPLETED:
      return "SCAN_COMPLETED";
    case WL_CONNECTED:
      return "CONNECTED";
    case WL_CONNECT_FAILED:
      return "CONNECT_FAILED";
    case WL_CONNECTION_LOST:
      return "CONNECTION_LOST";
    case WL_DISCONNECTED:
      return "DISCONNECTED";
    default:
      return "UNKNOWN";
  }
}

static void printState(const char *event) {
  Serial.print("{\"event\":\"");
  Serial.print(event);
  Serial.print("\",\"ssid\":\"");
  Serial.print(WIFI_STA_SSID);
  Serial.print("\",\"status\":");
  Serial.print(static_cast<int>(WiFi.status()));
  Serial.print(",\"status_name\":\"");
  Serial.print(statusName(WiFi.status()));
  Serial.print("\",\"ip\":\"");
  Serial.print(WiFi.localIP());
  Serial.print("\",\"rssi\":");
  Serial.print(WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0);
  Serial.print(",\"disconnect_reason\":");
  Serial.print(lastDisconnectReason);
  Serial.println("}");
}

static void onWifiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_CONNECTED) {
    printState("sta_connected");
  } else if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    lastDisconnectReason = 0;
    printState("got_ip");
  } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    lastDisconnectReason = info.wifi_sta_disconnected.reason;
    printState("disconnected");
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  Serial.println("{\"event\":\"boot\",\"test\":\"wifi_pioarduino\"}");
  WiFi.persistent(false);
  WiFi.onEvent(onWifiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.disconnect(false, false);
  delay(300);

  printState("connect_start");
  WiFi.begin(WIFI_STA_SSID, WIFI_STA_PASSWORD);
}

void loop() {
  const unsigned long now = millis();
  if (now - lastPrintMs >= 1000) {
    lastPrintMs = now;
    printState("tick");
  }
  delay(20);
}
