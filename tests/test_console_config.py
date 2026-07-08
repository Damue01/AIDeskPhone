import unittest

from tools.ai_desk_phone_console import ConsoleConfig


class ConsoleConfigInputProfilesTest(unittest.TestCase):
    def test_legacy_shortcut_fields_seed_default_input_profile(self) -> None:
        config = ConsoleConfig.from_dict(
            {
                "press_action_text": "ctrl+win+shift, 延迟1000毫秒, enter",
                "release_action_text": "ctrl+win+shift",
            }
        )

        profile = config.active_input_profile()

        self.assertEqual(profile["id"], "default")
        self.assertEqual(profile["name"], "默认方案")
        self.assertEqual(profile["press_action_text"], "ctrl+win+shift, 延迟1000毫秒, enter")
        self.assertEqual(profile["release_action_text"], "ctrl+win+shift")
        self.assertEqual(config.action_text_for_state("PRESSED"), "ctrl+win+shift, 延迟1000毫秒, enter")
        self.assertEqual(config.action_text_for_state("RELEASED"), "ctrl+win+shift")

    def test_active_input_profile_controls_press_and_release_actions(self) -> None:
        config = ConsoleConfig.from_dict(
            {
                "active_input_profile_id": "doubao-client",
                "input_action_profiles": [
                    {
                        "id": "codex",
                        "name": "Codex 输入",
                        "press_action_text": "ctrl+enter",
                        "release_action_text": "ctrl+shift",
                    },
                    {
                        "id": "doubao-client",
                        "name": "豆包客户端",
                        "press_action_text": "ctrl+alt+u",
                        "release_action_text": "ctrl+alt+i",
                    },
                ],
            }
        )

        self.assertEqual(config.active_input_profile()["name"], "豆包客户端")
        self.assertEqual(config.action_text_for_state("PRESSED"), "ctrl+alt+u")
        self.assertEqual(config.action_text_for_state("RELEASED"), "ctrl+alt+i")

    def test_unknown_active_input_profile_falls_back_to_first_profile(self) -> None:
        config = ConsoleConfig.from_dict(
            {
                "active_input_profile_id": "missing",
                "input_action_profiles": [
                    {
                        "id": "codex",
                        "name": "Codex 输入",
                        "press_action_text": "ctrl+enter",
                        "release_action_text": "ctrl+shift",
                    }
                ],
            }
        )

        self.assertEqual(config.active_input_profile()["id"], "codex")
        self.assertEqual(config.active_input_profile_id, "codex")

    def test_serial_debug_is_opt_in(self) -> None:
        config = ConsoleConfig.from_dict({})
        enabled = ConsoleConfig.from_dict({"enable_serial_debug": True})

        self.assertFalse(config.enable_serial_debug)
        self.assertTrue(enabled.enable_serial_debug)


if __name__ == "__main__":
    unittest.main()
