import unittest

from tools.agent_runtime import MinimalAgentLoop


ROLE_LEAK_TERMS = ("Agent", "AI", "人工智能", "模型", "技能", "tool", "JSON")


class MinimalAgentRuntimeTest(unittest.TestCase):
    def test_city_navigation_uses_command_center_earth_skill(self) -> None:
        result = MinimalAgentLoop().run("定位北京")

        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].skill, "command_center.earth")
        self.assertEqual(result.tool_calls[0].name, "focus_city")
        self.assertEqual(result.tool_results[0].event["command"]["action"], "focusCity")
        self.assertEqual(result.tool_results[0].event["command"]["payload"], "北京")
        self.assertIn("北京", result.final_text)

    def test_coordinate_navigation_uses_fly_to(self) -> None:
        result = MinimalAgentLoop().run("跳到经度 116.4074 纬度 39.9042")

        self.assertEqual(result.tool_calls[0].name, "fly_to")
        payload = result.tool_results[0].event["command"]["payload"]
        self.assertEqual(payload["lng"], 116.4074)
        self.assertEqual(payload["lat"], 39.9042)

    def test_non_action_turn_returns_conversation_without_tool_call(self) -> None:
        result = MinimalAgentLoop().run("帮我整理一下文件")

        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.final_text, "我在，您说。")
        self.assertNotIn("帮我整理一下文件", result.final_text)
        for term in ROLE_LEAK_TERMS:
            self.assertNotIn(term, result.final_text)

    def test_question_without_tool_intent_stays_in_chat_mode(self) -> None:
        result = MinimalAgentLoop().run("今天我有点累，是不是先休息一下")

        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.final_text, "我在，您说。")
        self.assertNotIn("今天我有点累", result.final_text)
        for term in ROLE_LEAK_TERMS:
            self.assertNotIn(term, result.final_text)

    def test_spoken_report_does_not_expose_internal_identity(self) -> None:
        result = MinimalAgentLoop().run("切到执行命令")

        self.assertEqual(result.tool_calls[0].name, "set_phase")
        self.assertIn("执行状态", result.final_text)
        self.assertNotIn("executing", result.final_text)
        for term in ROLE_LEAK_TERMS:
            self.assertNotIn(term, result.final_text)


if __name__ == "__main__":
    unittest.main()
