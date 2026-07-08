import unittest

from tools.volcengine_speech import SpeechConfig, VolcengineSpeech, auth_headers, normalize_speaker_list


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
        "tts_explicit_language": "",
        "tts_explicit_dialect": "",
        "tts_disable_markdown_filter": True,
        "tts_disable_emoji_filter": True,
        "asr_enabled": True,
        "asr_endpoint": "wss://example.test/asr",
        "asr_resource_id": "volc.seedasr.sauc.duration",
        "asr_model": "bigmodel",
        "asr_chunk_ms": 100,
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


if __name__ == "__main__":
    unittest.main()
