import unittest
import base64
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools.volcengine_speech import (
    DEFAULT_OPERATOR_SYSTEM_PROMPT,
    DEFAULT_PHONE_AGENT_SYSTEM_PROMPT,
    SpeechConfig,
    StreamingAsrSession,
    VolcengineSpeech,
    auth_headers,
    build_phone_agent_reply_payload,
    build_operator_report_payload,
    build_asr_init_payload,
    extract_chat_completion_text,
    normalize_speaker_list,
    upsert_dotenv_values,
)


def make_config(**overrides: object) -> SpeechConfig:
    values = {
        "api_key": "",
        "app_key": "",
        "access_key": "",
        "tts_enabled": True,
        "tts_endpoint": "https://example.test/tts",
        "tts_resource_id": "seed-tts-2.0",
        "tts_model": "seed-tts-2.0-standard",
        "tts_speaker": "zh_female_demo",
        "tts_format": "wav",
        "tts_sample_rate": 24000,
        "tts_streaming_playback_enabled": True,
        "tts_explicit_language": "",
        "tts_explicit_dialect": "",
        "tts_disable_markdown_filter": True,
        "tts_disable_emoji_filter": True,
        "asr_enabled": True,
        "asr_endpoint": "wss://example.test/asr",
        "asr_resource_id": "volc.seedasr.sauc.duration",
        "asr_model": "bigmodel",
        "asr_chunk_ms": 100,
        "asr_streaming_enabled": True,
        "asr_boosting_table_id": "",
        "asr_boosting_table_name": "",
        "asr_hotwords": "",
        "operator_api_key": "",
        "operator_endpoint": "https://ark.example.test/api/v3/chat/completions",
        "operator_model": "doubao-seed-character-260628",
        "operator_polish_enabled": True,
        "operator_system_prompt": "你是通讯员。",
        "operator_max_tokens": 900,
    }
    values.update(overrides)
    return SpeechConfig(**values)


class VolcengineSpeechSpeakersTest(unittest.TestCase):
    def test_api_key_auth_header_is_preferred(self) -> None:
        config = make_config(api_key="api-key", app_key="app-key", access_key="access-key")

        self.assertTrue(config.has_credentials())
        self.assertEqual(config.credential_mode(), "api_key")
        self.assertEqual(auth_headers(config), {"X-Api-Key": "api-key"})

    def test_app_access_key_auth_header_remains_supported(self) -> None:
        config = make_config(app_key="app-key", access_key="access-key")

        self.assertTrue(config.has_credentials())
        self.assertEqual(config.credential_mode(), "app_access_key")
        self.assertEqual(
            auth_headers(config),
            {
                "X-Api-App-Key": "app-key",
                "X-Api-App-Id": "app-key",
                "X-Api-Access-Key": "access-key",
            },
        )

    def test_operator_credentials_reuse_main_api_key_by_default(self) -> None:
        env = {
            "VOLCENGINE_API_KEY": "main-key",
            "ARK_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=True):
            config = SpeechConfig.from_env()

        self.assertEqual(config.api_key, "main-key")
        self.assertEqual(config.operator_api_key, "main-key")
        self.assertTrue(VolcengineSpeech(config).is_operator_ready())

    def test_speaker_refresh_without_openapi_keys_keeps_default_path(self) -> None:
        result = VolcengineSpeech(make_config(api_key="api-key")).list_speakers()

        self.assertFalse(result["ok"])
        self.assertIn("已保留当前默认音色", result["error"])

    def test_normalize_speaker_list_accepts_common_response_shapes(self) -> None:
        payload = {
            "Result": {
                "Speakers": [
                    {
                        "SpeakerID": "zh_female_demo",
                        "SpeakerName": "示例女声",
                        "ResourceID": "seed-tts-2.0",
                        "Language": "zh-cn",
                    }
                ],
                "Items": [{"VoiceType": "zh_male_demo", "Name": "示例男声"}],
            }
        }

        speakers = normalize_speaker_list(payload)

        self.assertEqual([speaker["id"] for speaker in speakers], ["zh_female_demo", "zh_male_demo"])
        self.assertEqual(speakers[0]["name"], "示例女声")
        self.assertEqual(speakers[0]["model"], "seed-tts-2.0")

    def test_asr_init_payload_includes_optional_hotword_configuration(self) -> None:
        config = make_config(
            asr_boosting_table_id="boost-id",
            asr_boosting_table_name="desk-phone-terms",
            asr_hotwords="键斗, Codex\nHG113",
        )

        payload = build_asr_init_payload(config, 16000)

        self.assertEqual(payload["audio"]["sample_rate"], 16000)
        self.assertEqual(payload["request"]["boosting_table_id"], "boost-id")
        self.assertEqual(payload["request"]["boosting_table_name"], "desk-phone-terms")
        self.assertEqual(payload["request"]["hotwords"], "键斗,Codex,HG113")

    def test_streaming_session_buffers_audio_into_configured_chunks(self) -> None:
        session = StreamingAsrSession(VolcengineSpeech(make_config()), 16000)

        session.submit_audio(b"\x01" * 1000)
        session.submit_audio(b"\x02" * 2200)
        packet = session.audio_queue.get_nowait()

        self.assertEqual(len(packet), 3200)
        self.assertEqual(session.chunks_submitted, 1)
        self.assertEqual(session.bytes_submitted, 3200)

    def test_tts_payload_can_override_format_for_streaming_pcm(self) -> None:
        speech = VolcengineSpeech(make_config(tts_format="wav", tts_sample_rate=24000))

        payload = speech.build_tts_payload("测试", audio_format="pcm", sample_rate=16000)

        self.assertEqual(payload["req_params"]["audio_params"]["format"], "pcm")
        self.assertEqual(payload["req_params"]["audio_params"]["sample_rate"], 16000)

    def test_tts_sse_audio_chunks_are_yielded_incrementally(self) -> None:
        audio = b"\x01" * 32

        class FakeResponse:
            headers = {"Content-Type": "text/event-stream"}

            def iter_lines(self, decode_unicode: bool = False):
                del decode_unicode
                payload = {"audio": base64.b64encode(audio).decode("ascii")}
                yield "data: " + json.dumps(payload)
                yield "data: [DONE]"

        chunks = list(VolcengineSpeech(make_config()).iter_tts_audio_chunks(FakeResponse()))

        self.assertEqual(chunks, [audio])

    def test_operator_report_payload_targets_character_model(self) -> None:
        config = make_config(operator_model="doubao-seed-character-260628")

        payload = build_operator_report_payload(config, "任务已经完成，改了配置页。", source="codex")

        self.assertEqual(payload["model"], "doubao-seed-character-260628")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn("任务已经完成", payload["messages"][1]["content"])
        self.assertEqual(payload["max_tokens"], 900)

    def test_default_operator_prompt_keeps_in_character_boundary(self) -> None:
        config = make_config(operator_system_prompt="")

        payload = build_operator_report_payload(config, "任务已经完成，改了配置页。", source="codex")

        self.assertEqual(payload["messages"][0]["content"], DEFAULT_OPERATOR_SYSTEM_PROMPT)
        self.assertIn("电话通讯员", payload["messages"][0]["content"])
        self.assertIn("不暴露任何后台身份", payload["messages"][0]["content"])
        self.assertIn("不要固定套用", payload["messages"][0]["content"])
        self.assertNotIn("用户是首长", payload["messages"][0]["content"])
        self.assertNotIn("AI 任务完成结果", payload["messages"][1]["content"])
        self.assertNotIn("外部 AI", payload["messages"][0]["content"])

    def test_phone_agent_reply_payload_uses_style_prompt_not_fixed_reply(self) -> None:
        config = make_config(operator_model="doubao-seed-character-260628")

        payload = build_phone_agent_reply_payload(
            config,
            "定位北京",
            source="voice-asr",
            skill_context="已办好：已定位北京",
            fallback_text="已定位北京。",
        )

        self.assertEqual(payload["model"], "doubao-seed-character-260628")
        self.assertEqual(payload["messages"][0]["content"], DEFAULT_PHONE_AGENT_SYSTEM_PROMPT)
        self.assertIn("小叶", payload["messages"][0]["content"])
        self.assertIn("不要固定套话", payload["messages"][0]["content"])
        self.assertIn("执行类也必须走角色对话", payload["messages"][1]["content"])
        self.assertIn("小叶当前能办理的事", payload["messages"][1]["content"])
        self.assertIn("已办好：已定位北京", payload["messages"][1]["content"])
        self.assertIn("已定位北京。", payload["messages"][1]["content"])
        self.assertIn("定位北京", payload["messages"][1]["content"])
        self.assertNotIn("收到首长", payload["messages"][0]["content"])
        self.assertNotIn("可用技能：command_center.earth", payload["messages"][1]["content"])
        self.assertNotIn("已执行技能结果", payload["messages"][1]["content"])
        self.assertNotIn("兜底参考", payload["messages"][1]["content"])
        self.assertLessEqual(payload["max_tokens"], 220)

    def test_extract_chat_completion_text_accepts_openai_shape(self) -> None:
        payload = {"choices": [{"message": {"content": "首长，任务已经完成。"}}]}

        self.assertEqual(extract_chat_completion_text(payload), "首长，任务已经完成。")

    def test_clearable_speech_env_values_can_be_saved_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env_path = f"{directory}/.env"
            with open(env_path, "w", encoding="utf-8") as file:
                file.write("DOUBAO_ASR_HOTWORDS=键斗,Codex\n")

            upsert_dotenv_values({"DOUBAO_ASR_HOTWORDS": ""}, path=Path(env_path))

            with open(env_path, "r", encoding="utf-8") as file:
                self.assertIn("DOUBAO_ASR_HOTWORDS=", file.read())


if __name__ == "__main__":
    unittest.main()
