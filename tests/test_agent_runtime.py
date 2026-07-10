import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.agent_runtime import (
    AgentContext,
    CompactionSettings,
    CommandCenterEarthSkill,
    LocalFileSkill,
    LocalAppSkill,
    MinimalAgentLoop,
    ShellCommandSkill,
    SkillRegistry,
    WebInformationSkill,
    parse_search_summary_html,
)


ROLE_LEAK_TERMS = ("Agent", "AI", "人工智能", "模型", "技能", "tool", "JSON")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class MinimalAgentRuntimeTest(unittest.TestCase):
    def test_city_navigation_uses_command_center_earth_skill(self) -> None:
        result = MinimalAgentLoop().run("定位北京")

        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].skill, "command_center.earth")
        self.assertEqual(result.tool_calls[0].name, "focus_city")
        self.assertEqual(result.tool_results[0].event["command"]["action"], "focusCity")
        self.assertEqual(result.tool_results[0].event["command"]["payload"], "北京")
        self.assertIn("北京", result.final_text)

    def test_adjust_to_city_uses_command_center_earth_skill(self) -> None:
        result = MinimalAgentLoop().run("调整到纽约")

        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].skill, "command_center.earth")
        self.assertEqual(result.tool_calls[0].name, "focus_city")
        self.assertEqual(result.tool_results[0].event["command"]["payload"], "纽约")
        self.assertIn("纽约", result.final_text)

    def test_switch_back_to_earth_page_uses_show_globe(self) -> None:
        result = MinimalAgentLoop().run("切换回地球页面")

        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].skill, "command_center.earth")
        self.assertEqual(result.tool_calls[0].name, "show_globe")
        self.assertEqual(result.tool_results[0].event["command"]["action"], "showGlobe")
        self.assertIn("地球屏保", result.final_text)

    def test_return_to_default_home_uses_show_globe(self) -> None:
        result = MinimalAgentLoop().run("回到默认首页")

        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].name, "show_globe")

    def test_coordinate_navigation_uses_fly_to(self) -> None:
        result = MinimalAgentLoop().run("跳到经度 116.4074 纬度 39.9042")

        self.assertEqual(result.tool_calls[0].name, "fly_to")
        payload = result.tool_results[0].event["command"]["payload"]
        self.assertEqual(payload["lng"], 116.4074)
        self.assertEqual(payload["lat"], 39.9042)

    def test_non_action_turn_does_not_synthesize_local_reply(self) -> None:
        result = MinimalAgentLoop().run("帮我整理一下文件")

        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.final_text, "")
        self.assertNotIn("帮我整理一下文件", result.final_text)
        for term in ROLE_LEAK_TERMS:
            self.assertNotIn(term, result.final_text)

    def test_question_without_tool_intent_does_not_synthesize_local_reply(self) -> None:
        result = MinimalAgentLoop().run("今天我有点累，是不是先休息一下")

        self.assertEqual(result.tool_calls, [])
        self.assertEqual(result.final_text, "")
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

    def test_weather_query_uses_web_information_skill(self) -> None:
        skill = WebInformationSkill(weather_lookup=lambda city: f"{city}现在多云，气温26度")
        result = MinimalAgentLoop(skills=[skill]).run("查询一下上海的天气")

        self.assertEqual(result.tool_calls[0].skill, "web.info")
        self.assertEqual(result.tool_calls[0].name, "lookup_weather")
        self.assertEqual(result.tool_calls[0].arguments["city"], "上海")
        self.assertIn("上海现在多云", result.final_text)

    def test_browser_search_opens_search_url(self) -> None:
        opened: list[str] = []
        skill = WebInformationSkill(
            open_url=lambda url: opened.append(url) or True,
            search_lookup=lambda query: f"{query} 的搜索摘要",
        )

        result = MinimalAgentLoop(skills=[skill]).run("搜索 PI coding agent")

        self.assertEqual(result.tool_calls[0].name, "search_web")
        self.assertEqual(result.tool_calls[0].arguments["query"], "PI coding agent")
        self.assertEqual(len(opened), 1)
        self.assertIn("PI+coding+agent", opened[0])
        self.assertIn("已打开浏览器搜索", result.final_text)
        self.assertIn("PI coding agent 的搜索摘要", result.final_text)
        self.assertEqual(result.tool_results[0].event["result"]["summary"], "PI coding agent 的搜索摘要")

    def test_search_summary_parser_extracts_bing_result_blocks(self) -> None:
        summary = parse_search_summary_html(
            """
            <html><body>
              <li class="b_algo"><h2><a>上海台风消息</a></h2><div><p>台风预计影响华东沿海。</p></div></li>
              <li class="b_algo"><h2><a>中央气象台</a></h2><p>今年第 5 号台风正在移动。</p></li>
            </body></html>
            """
        )

        self.assertIn("上海台风消息：台风预计影响华东沿海", summary)
        self.assertIn("中央气象台：今年第 5 号台风正在移动", summary)

    def test_local_file_ls_uses_pi_style_tool(self) -> None:
        result = MinimalAgentLoop(skills=[LocalFileSkill()]).run(
            "ls tools",
            AgentContext(cwd=str(PROJECT_ROOT)),
        )

        self.assertEqual(result.tool_calls[0].skill, "local.files")
        self.assertEqual(result.tool_calls[0].name, "ls")
        self.assertEqual(result.tool_calls[0].arguments["path"], "tools")
        self.assertTrue(result.tool_results[0].ok)
        self.assertIn("已列出", result.final_text)

    def test_local_file_read_is_limited_to_project_root(self) -> None:
        result = MinimalAgentLoop(skills=[LocalFileSkill()]).run(
            "读取文件 ..\\outside.txt",
            AgentContext(cwd=str(PROJECT_ROOT)),
        )

        self.assertEqual(result.tool_calls[0].name, "read")
        self.assertFalse(result.tool_results[0].ok)
        self.assertIn("路径不在当前项目内", result.final_text)

    def test_local_file_grep_requires_file_context(self) -> None:
        result = MinimalAgentLoop(skills=[LocalFileSkill()]).run(
            "搜索 PI coding agent",
            AgentContext(cwd=str(PROJECT_ROOT)),
        )

        self.assertEqual(result.tool_calls, [])

    def test_local_file_grep_searches_project_text(self) -> None:
        result = MinimalAgentLoop(skills=[LocalFileSkill()]).run(
            "在代码里搜索 AgentContext",
            AgentContext(cwd=str(PROJECT_ROOT)),
        )

        self.assertEqual(result.tool_calls[0].name, "grep")
        self.assertEqual(result.tool_calls[0].arguments["pattern"], "AgentContext")
        self.assertTrue(result.tool_results[0].ok)
        self.assertIn("找到", result.final_text)

    def test_local_file_find_searches_project_paths(self) -> None:
        result = MinimalAgentLoop(skills=[LocalFileSkill()]).run(
            "查找文件 agent_runtime.py",
            AgentContext(cwd=str(PROJECT_ROOT)),
        )

        self.assertEqual(result.tool_calls[0].name, "find")
        self.assertTrue(result.tool_results[0].ok)
        self.assertIn("agent_runtime.py", "\n".join(result.tool_results[0].event["result"]["paths"]))

    def test_app_launch_uses_allowlisted_target(self) -> None:
        launched: list[tuple[str, ...]] = []
        skill = LocalAppSkill(launcher=lambda command: launched.append(command) or True)

        result = MinimalAgentLoop(skills=[skill]).run("打开计算器")

        self.assertEqual(result.tool_calls[0].skill, "system.app")
        self.assertEqual(result.tool_calls[0].name, "launch_app")
        self.assertEqual(result.tool_calls[0].arguments["app"], "计算器")
        self.assertEqual(launched, [("calc.exe",)])
        self.assertIn("已打开计算器", result.final_text)

    def test_shell_command_runs_explicit_safe_command(self) -> None:
        calls: list[tuple[str, str | None, float]] = []

        def fake_runner(command: str, cwd: str | None, timeout: float) -> tuple[int, str, str]:
            calls.append((command, cwd, timeout))
            return 0, "hello\n", ""

        skill = ShellCommandSkill(runner=fake_runner, timeout_seconds=3)
        result = MinimalAgentLoop(skills=[skill]).run(
            "执行命令 echo hello",
            AgentContext(permission_profile="commander", cwd=str(PROJECT_ROOT)),
        )

        self.assertEqual(result.tool_calls[0].skill, "system.command")
        self.assertEqual(result.tool_calls[0].arguments["command"], "echo hello")
        self.assertEqual(calls, [("echo hello", str(PROJECT_ROOT), 3)])
        self.assertIn("命令已执行", result.final_text)
        self.assertIn("hello", result.final_text)

    def test_shell_command_is_denied_by_default_profile(self) -> None:
        skill = ShellCommandSkill(runner=lambda *_: self.fail("default profile must not run shell"))

        result = MinimalAgentLoop(skills=[skill]).run("执行命令 echo hello")

        self.assertFalse(result.tool_results[0].ok)
        self.assertIn("当前权限不允许执行本机命令", result.final_text)

    def test_shell_command_intent_does_not_switch_earth_phase(self) -> None:
        skill = ShellCommandSkill(runner=lambda *_: (0, "hello\n", ""))
        result = MinimalAgentLoop(skills=[CommandCenterEarthSkill(), skill]).run(
            "执行命令 echo hello",
            AgentContext(permission_profile="commander", cwd=str(PROJECT_ROOT)),
        )

        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(result.tool_calls[0].skill, "system.command")

    def test_shell_command_blocks_dangerous_command(self) -> None:
        skill = ShellCommandSkill(runner=lambda *_: self.fail("dangerous command should not run"))

        result = MinimalAgentLoop(skills=[skill]).run(
            "执行命令 git reset --hard",
            AgentContext(permission_profile="commander", cwd=str(PROJECT_ROOT)),
        )

        self.assertEqual(result.tool_calls[0].name, "run_command")
        self.assertFalse(result.tool_results[0].ok)
        self.assertIn("危险命令已拦截", result.final_text)

    def test_pi_style_session_records_user_tool_call_and_tool_result(self) -> None:
        loop = MinimalAgentLoop()

        result = loop.run("定位北京")

        self.assertIsNotNone(result.prompt)
        assert result.prompt is not None
        self.assertEqual(result.prompt.messages[0]["role"], "system")
        self.assertEqual(result.prompt.messages[1]["role"], "developer")
        self.assertIn("command_center.earth/focus_city", result.prompt.messages[1]["content"])

        status = loop.status()
        self.assertIn("你是 AI Desk Phone", status["prompt_template"]["system"])
        self.assertIn("PI Coding Agent", status["prompt_template"]["developer"])
        self.assertIn("commander", status["permission_profiles"])
        entries = status["session"]["recent_entries"]
        message_entries = [entry for entry in entries if entry["type"] == "message"]
        roles = [entry["message"]["role"] for entry in message_entries]
        self.assertEqual(roles, ["user", "assistant", "toolResult"])
        self.assertIsNone(message_entries[0]["parentId"])
        self.assertEqual(message_entries[1]["parentId"], message_entries[0]["id"])
        self.assertEqual(message_entries[2]["parentId"], message_entries[1]["id"])
        self.assertEqual(message_entries[1]["message"]["metadata"]["stopReason"], "toolUse")
        self.assertEqual(message_entries[2]["message"]["tool_name"], "command_center.earth/focus_city")

    def test_phone_reply_is_recorded_as_assistant_message(self) -> None:
        loop = MinimalAgentLoop()
        result = loop.run("定位北京")

        message_id = loop.record_assistant_reply(result.id, "首长，好了，已经定位到北京。", skill_context="已办好：已定位北京")

        self.assertIsNotNone(message_id)
        last_entry = loop.status()["session"]["recent_entries"][-1]
        self.assertEqual(last_entry["message"]["role"], "assistant")
        self.assertEqual(last_entry["message"]["metadata"]["turn_id"], result.id)
        self.assertTrue(last_entry["message"]["metadata"]["phone_reply"])
        self.assertIn("已经定位到北京", last_entry["message"]["content"][0]["text"])

    def test_skill_registry_loads_skill_markdown_on_explicit_invocation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / ".pi" / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: demo-skill\n"
                "description: Demo skill for phone agent tests.\n"
                "---\n\n"
                "# Demo Skill\n\n"
                "Use this when the user explicitly asks for the demo skill.\n",
                encoding="utf-8",
            )
            loop = MinimalAgentLoop(skills=[], cwd=str(root))

            result = loop.run("/skill:demo-skill 看一下")

            self.assertEqual(result.tool_calls, [])
            assert result.prompt is not None
            self.assertIn("demo-skill", result.prompt.loaded_skills)
            self.assertIn("<loaded_skill name=\"demo-skill\"", result.prompt.messages[1]["content"])
            self.assertEqual(loop.status()["skills"]["loaded"], ["demo-skill"])

    def test_map_skill_markdown_loads_for_map_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill_dir = root / ".pi" / "skills" / "command-center-earth"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                "---\n"
                "name: command-center-earth\n"
                "description: Map and globe operations for the command center.\n"
                "---\n\n"
                "# Command Center Earth\n\n"
                "Use focusCity for city navigation.\n",
                encoding="utf-8",
            )
            loop = MinimalAgentLoop(skills=[], cwd=str(root))

            result = loop.run("帮我把地图切换到上海")

            assert result.prompt is not None
            self.assertIn("command-center-earth", result.prompt.loaded_skills)
            self.assertIn("<loaded_skill name=\"command-center-earth\"", result.prompt.messages[1]["content"])

    def test_skill_registry_loads_only_project_pi_skills(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_home = root / "home"
            global_skill = fake_home / ".pi" / "agent" / "skills" / "global-demo"
            project_agents_skill = root / "project" / ".agents" / "skills" / "agents-demo"
            project_skill = root / "project" / ".pi" / "skills" / "project-demo"
            global_skill.mkdir(parents=True)
            project_agents_skill.mkdir(parents=True)
            project_skill.mkdir(parents=True)
            (root / "project" / ".git").mkdir()
            (global_skill / "SKILL.md").write_text(
                "---\nname: global-demo\ndescription: Global demo skill.\n---\n",
                encoding="utf-8",
            )
            (project_agents_skill / "SKILL.md").write_text(
                "---\nname: agents-demo\ndescription: Project agents skill.\n---\n",
                encoding="utf-8",
            )
            (project_skill / "SKILL.md").write_text(
                "---\nname: project-demo\ndescription: Project demo skill.\n---\n",
                encoding="utf-8",
            )

            with patch("tools.agent_runtime.Path.home", return_value=fake_home):
                registry = SkillRegistry(cwd=str(root / "project"))

            self.assertIn("project-demo", registry.skills)
            self.assertNotIn("global-demo", registry.skills)
            self.assertNotIn("agents-demo", registry.skills)

    def test_skill_registry_ignores_external_skill_paths_and_env_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            explicit_skill = root / "external" / "explicit-demo"
            env_skill = root / "env" / "env-demo"
            project_skill = root / "project" / ".pi" / "skills" / "project-demo"
            explicit_skill.mkdir(parents=True)
            env_skill.mkdir(parents=True)
            project_skill.mkdir(parents=True)
            (root / "project" / ".git").mkdir(parents=True)
            (explicit_skill / "SKILL.md").write_text(
                "---\nname: explicit-demo\ndescription: Explicit external skill.\n---\n",
                encoding="utf-8",
            )
            (env_skill / "SKILL.md").write_text(
                "---\nname: env-demo\ndescription: Env external skill.\n---\n",
                encoding="utf-8",
            )
            (project_skill / "SKILL.md").write_text(
                "---\nname: project-demo\ndescription: Project demo skill.\n---\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"AI_DESK_PHONE_SKILL_PATHS": str(env_skill.parent)}, clear=False):
                registry = SkillRegistry(cwd=str(root / "project"), skill_paths=[explicit_skill.parent])

            self.assertIn("project-demo", registry.skills)
            self.assertNotIn("explicit-demo", registry.skills)
            self.assertNotIn("env-demo", registry.skills)

    def test_compaction_adds_summary_and_keeps_recent_context(self) -> None:
        loop = MinimalAgentLoop(
            skills=[],
            compaction_settings=CompactionSettings(
                enabled=True,
                context_window_tokens=90,
                reserve_tokens=10,
                keep_recent_tokens=25,
            ),
        )

        for index in range(12):
            loop.run(f"这是第 {index} 轮很长的电话调试内容，需要被压缩保存上下文。")

        status = loop.status()
        latest_compaction = status["session"]["latest_compaction"]
        self.assertIsNotNone(latest_compaction)
        self.assertIn("## Goal", latest_compaction["summary"])
        context_roles = [message.role for message in loop.session.build_session_context()]
        self.assertIn("system", context_roles)
        self.assertGreater(status["session"]["entry_count"], status["session"]["message_count"])


if __name__ == "__main__":
    unittest.main()
