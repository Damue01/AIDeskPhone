import unittest

from tools.agent_runtime import MinimalAgentLoop


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

    def test_unknown_command_returns_capability_hint_without_tool_call(self) -> None:
        result = MinimalAgentLoop().run("帮我整理一下文件")

        self.assertEqual(result.tool_calls, [])
        self.assertIn("最小 Agent", result.final_text)


if __name__ == "__main__":
    unittest.main()
