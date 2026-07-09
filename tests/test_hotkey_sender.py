import unittest
from unittest.mock import patch

from tools.ai_desk_phone_console import WindowsHotkeySender


class FakeUser32:
    def __init__(self) -> None:
        self.events: list[tuple[int, int]] = []

    def keybd_event(self, vk: int, scan: int, flags: int, extra_info: int) -> None:
        del scan, extra_info
        self.events.append((vk, flags))


class WindowsHotkeySenderTest(unittest.TestCase):
    def test_sends_letter_hotkeys_used_by_input_profiles(self) -> None:
        sender = WindowsHotkeySender()
        fake_user32 = FakeUser32()
        sender.user32 = fake_user32

        with patch("tools.ai_desk_phone_console.time.sleep"):
            sender.send_hotkey(["ctrl", "alt", "i"])
            sender.send_hotkey(["ctrl", "alt", "u"])

        pressed_codes = [vk for vk, flags in fake_user32.events if flags == 0]
        self.assertIn(ord("I"), pressed_codes)
        self.assertIn(ord("U"), pressed_codes)


if __name__ == "__main__":
    unittest.main()
