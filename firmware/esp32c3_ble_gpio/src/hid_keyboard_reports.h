#pragma once

#include <stddef.h>
#include <stdint.h>

static constexpr uint8_t REPORT_ID_KEYBOARD = 1;
static constexpr size_t KEYBOARD_REPORT_SIZE = 8;

static constexpr uint8_t HID_MOD_LEFT_CTRL = 0x01;
static constexpr uint8_t HID_MOD_LEFT_SHIFT = 0x02;
static constexpr uint8_t HID_MOD_LEFT_GUI = 0x08;
static constexpr uint8_t HID_MOD_RIGHT_ALT = 0x40;
static constexpr uint8_t HID_KEY_ENTER = 0x28;
static constexpr uint8_t HID_MOD_VOICE_HOTKEY = HID_MOD_LEFT_CTRL | HID_MOD_LEFT_SHIFT | HID_MOD_LEFT_GUI;

struct KeyboardReport {
  uint8_t modifiers;
  uint8_t reserved;
  uint8_t keys[6];
};

static_assert(sizeof(KeyboardReport) == KEYBOARD_REPORT_SIZE, "Keyboard reports must be 8 bytes");

static constexpr KeyboardReport REPORT_EMPTY = {0x00, 0x00, {0, 0, 0, 0, 0, 0}};
static constexpr KeyboardReport REPORT_RIGHT_ALT = {HID_MOD_RIGHT_ALT, 0x00, {0, 0, 0, 0, 0, 0}};
static constexpr KeyboardReport REPORT_VOICE_HOTKEY = {HID_MOD_VOICE_HOTKEY, 0x00, {0, 0, 0, 0, 0, 0}};
static constexpr KeyboardReport REPORT_ENTER = {0x00, 0x00, {HID_KEY_ENTER, 0, 0, 0, 0, 0}};
