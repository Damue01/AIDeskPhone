import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.audio_recorder import RecordingResult
from tools.ai_desk_phone_console import AppState, ConsoleConfig, ReplyTask
import tools.ai_desk_phone_console as console


class FakeRecorder:
    def __init__(self, recording: bool = False) -> None:
        self.recording = recording
        self.cancelled = False
        self.stopped_paths: list[Path] = []
        self.started_at = time.monotonic()

    def dependencies_available(self) -> bool:
        return True

    def start(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        device: str | int | None = None,
        on_audio_chunk=None,
    ) -> None:
        del sample_rate, channels, device
        self.recording = True
        self.started_at = time.monotonic()
        if on_audio_chunk is not None:
            on_audio_chunk(b"\x01" * 3200)

    def is_recording(self) -> bool:
        return self.recording

    def stop_to_wav(self, output_path: Path) -> RecordingResult:
        self.recording = False
        self.stopped_paths.append(output_path)
        return RecordingResult(
            path=output_path,
            sample_rate=16000,
            channels=1,
            bytes_written=3200,
            duration_seconds=1.5,
        )

    def cancel(self) -> None:
        self.recording = False
        self.cancelled = True

    def current_duration_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at) if self.recording else 0.0

    def voice_activity(self) -> dict[str, float]:
        return {
            "duration_seconds": self.current_duration_seconds(),
            "silence_seconds": 0.0,
            "last_level": 0.0,
            "peak_level": 0.0,
        }


class FailingRecorder(FakeRecorder):
    def start(
        self,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        device: str | int | None = None,
        on_audio_chunk=None,
    ) -> None:
        del sample_rate, channels, device, on_audio_chunk
        raise RuntimeError("portaudio unavailable")


class FakeStreamingSession:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result
        self.cancelled = False
        self.submitted: list[bytes] = []

    def submit_audio(self, audio: bytes) -> None:
        self.submitted.append(audio)

    def finish(self, timeout: float = 25.0) -> dict[str, object]:
        del timeout
        return self.result

    def cancel(self) -> None:
        self.cancelled = True


class FakeSpeech:
    def __init__(self, session: FakeStreamingSession) -> None:
        self.session = session
        self.config = type(
            "Config",
            (),
            {"asr_streaming_enabled": True, "asr_chunk_ms": 100},
        )()

    def is_asr_ready(self) -> bool:
        return True

    def start_streaming_asr(self, sample_rate: int, *, on_result=None) -> FakeStreamingSession:
        del sample_rate
        if on_result is not None:
            on_result({"success": True, "text": "实时部分", "partial": True, "streaming": True})
        return self.session


class FakeOperatorSpeech:
    def is_operator_ready(self) -> bool:
        return True

    def format_operator_report(self, text: str, *, source: str = "codex") -> dict[str, object]:
        return {
            "success": True,
            "text": f"首长，{source} 已完成：{text}",
            "model": "doubao-seed-character-260628",
            "inference_latency": 0.1,
        }


def make_app(test_case: unittest.TestCase, config: ConsoleConfig | None = None) -> AppState:
    directory = tempfile.TemporaryDirectory()
    test_case.addCleanup(directory.cleanup)
    app = AppState(config or ConsoleConfig(), Path(directory.name) / "config.json", simulation_enabled=False)
    app.speech = None  # type: ignore[assignment]
    return app


class AgentHangupFlowTest(unittest.TestCase):
    def test_input_mode_lift_and_hangup_execute_active_profile_actions(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="codex"))
        calls: list[str] = []
        app.run_configured_shortcut_for_state = lambda state: calls.append(state) or True  # type: ignore[method-assign]

        app.handle_hook_transition("PRESSED", "RELEASED")
        app.handle_hook_transition("RELEASED", "PRESSED")

        self.assertEqual(calls, ["RELEASED", "PRESSED"])

    def test_ai_hook_while_on_hook_queues_reply_and_starts_callback(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="codex", enable_callback=True))
        app.last_state = "PRESSED"
        alerts: list[str] = []
        app.start_operator_alert = lambda source="ai": alerts.append(source) or True  # type: ignore[method-assign]

        ok = app.run_ai_hook_signal("codex", "任务完成")

        self.assertTrue(ok)
        self.assertEqual(app.reply_status()["queue_size"], 1)
        self.assertEqual(app.reply_queue[0].text, "任务完成")
        self.assertEqual(alerts, ["codex"])

    def test_ai_hook_uses_operator_role_model_when_configured(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="codex", enable_callback=False))
        app.speech = FakeOperatorSpeech()  # type: ignore[assignment]
        app.last_state = "PRESSED"

        ok = app.run_ai_hook_signal("codex", "改好了 config 页面")

        self.assertTrue(ok)
        self.assertEqual(app.reply_queue[0].text, "首长，codex 已完成：改好了 config 页面")

    def test_direct_reply_on_hook_is_queued_for_callback(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao", enable_callback=True, voice_reply_policy="direct"))
        app.last_state = "PRESSED"
        app.voice_session_id = 7
        alerts: list[str] = []
        app.start_operator_alert = lambda source="ai": alerts.append(source) or True  # type: ignore[method-assign]

        app.handle_voice_reply_text("打开灯", "direct", 7)

        self.assertEqual(app.reply_status()["queue_size"], 1)
        self.assertEqual(app.reply_queue[0].source, "agent")
        self.assertEqual(alerts, ["agent"])

    def test_unanswered_callback_expires_reply_queue(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao", enable_callback=True, voice_reply_policy="direct"))
        app.last_state = "PRESSED"
        app.simulation_enabled = True
        app.run_hardware_command = lambda command, log=True: True  # type: ignore[method-assign]
        app.enqueue_reply("agent", "首长，任务已经完成。", title="通讯员回报")

        with (
            patch.object(console, "OPERATOR_RING_TIMEOUT_SECONDS", 0.02),
            patch.object(console, "OPERATOR_CALLBACK_EXPIRE_SECONDS", 0.06),
            patch.object(console, "OPERATOR_RING_ON_SECONDS", 0.01),
            patch.object(console, "OPERATOR_RING_OFF_SECONDS", 0.01),
            patch.object(console, "OPERATOR_BUSY_ON_SECONDS", 0.01),
            patch.object(console, "OPERATOR_BUSY_OFF_SECONDS", 0.01),
        ):
            self.assertTrue(app.start_operator_alert("agent"))
            assert app.alert_thread is not None
            app.alert_thread.join(timeout=1)

        self.assertEqual(app.reply_status()["queue_size"], 0)
        self.assertFalse(app.alerting)
        self.assertIsNone(app.pending_report_text)
        self.assertEqual(app.completed_replies[-1].status, "expired")
        self.assertEqual(app.completed_replies[-1].text, "首长，任务已经完成。")
        logs = "\n".join(app.action_logs)
        self.assertIn("回拨无人接听，已取消 1 条回话", logs)

        app.start_reply_playback = lambda reason="": self.fail("expired reply should not play")  # type: ignore[method-assign]
        calls: list[str] = []
        app.start_agent_voice_session = lambda reason="", allow_on_hook=False: calls.append(reason) or {"ok": True}  # type: ignore[method-assign]
        app.handle_hook_transition("PRESSED", "RELEASED")

        self.assertEqual(calls, ["电话抬起"])

    def test_hangup_while_processing_keeps_current_voice_session(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao"))
        app.last_state = "PRESSED"
        app.voice_session_id = 3
        app.voice_processing = True
        app.cancel_agent_voice_session = lambda reason="": self.fail("processing session should not be cancelled")  # type: ignore[method-assign]

        app.handle_hook_transition("RELEASED", "PRESSED")

        self.assertEqual(app.voice_session_id, 3)
        self.assertTrue(app.voice_processing)

    def test_hangup_while_recording_submits_turn_and_callbacks(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao", enable_callback=True, voice_reply_policy="direct"))
        recorder = FakeRecorder(recording=True)
        app.recorder = recorder  # type: ignore[assignment]
        app.last_state = "PRESSED"
        app.voice_recording = True
        app.voice_session_id = 9
        app.voice_recording_path = str(Path(tempfile.gettempdir()) / "agent-hangup-test.wav")
        app.transcribe_audio_file = lambda path: {"success": True, "text": "查一下状态"}  # type: ignore[method-assign]
        alerts: list[str] = []
        app.start_operator_alert = lambda source="ai": alerts.append(source) or True  # type: ignore[method-assign]

        submitted = app.submit_agent_voice_turn_after_hangup("电话挂机")
        self.assertTrue(submitted)
        assert app.voice_monitor_thread is not None
        app.voice_monitor_thread.join(timeout=2)

        self.assertFalse(recorder.is_recording())
        self.assertEqual(app.reply_status()["queue_size"], 1)
        self.assertEqual(alerts, ["agent"])
        self.assertFalse(app.voice_processing)
        self.assertFalse(app.voice_recording)
        logs = "\n".join(app.action_logs)
        self.assertIn("电话已挂机：停止录音并提交已收到的语音", logs)
        self.assertIn("收到命令：查一下状态", logs)
        self.assertIn("回话内容：首长，我听到了：查一下状态。这句先按通话处理，不启动额外调度。您继续说。", logs)

    def test_agent_voice_turn_publishes_command_center_skill_event(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao", enable_callback=False, voice_reply_policy="direct"))
        app.last_state = "PRESSED"
        app.voice_session_id = 4
        subscriber = app.subscribe()
        self.addCleanup(lambda: app.unsubscribe(subscriber))

        app.handle_voice_reply_text("定位北京", "direct", 4)

        events: list[dict[str, object]] = []
        while not subscriber.empty():
            events.append(subscriber.get_nowait())

        command_events = [event for event in events if event.get("type") == "command_center_command"]
        self.assertEqual(len(command_events), 1)
        self.assertEqual(command_events[0]["command"]["action"], "focusCity")
        self.assertEqual(command_events[0]["command"]["payload"], "北京")
        self.assertEqual(app.reply_queue[0].source, "agent")
        self.assertIn("北京", app.reply_queue[0].text)
        logs = "\n".join(app.action_logs)
        self.assertIn("收到命令：定位北京", logs)
        self.assertIn("回话内容：首长，已定位北京。", logs)

    def test_voice_stop_uses_streaming_asr_result_before_file_fallback(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao", enable_callback=False, voice_reply_policy="direct"))
        recorder = FakeRecorder(recording=False)
        stream = FakeStreamingSession({"success": True, "text": "打开会议纪要", "streaming": True})
        app.recorder = recorder  # type: ignore[assignment]
        app.speech = FakeSpeech(stream)  # type: ignore[assignment]
        app.last_state = "PRESSED"
        app.transcribe_audio_file = lambda path: self.fail("streaming ASR should avoid file fallback")  # type: ignore[method-assign]

        start = app.start_agent_voice_session("调试页测试", allow_on_hook=True)
        self.assertTrue(start["ok"])
        result = app.stop_voice_recording("测试停止", reply_behavior="direct")

        self.assertTrue(result["ok"])
        self.assertEqual(result["transcript"]["text"], "打开会议纪要")
        self.assertEqual(app.reply_status()["queue_size"], 1)
        self.assertEqual(app.voice_status()["partial_text"], "打开会议纪要")
        self.assertTrue(stream.submitted)

    def test_voice_cancel_phrase_does_not_enqueue_reply(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao", enable_callback=True, voice_reply_policy="direct"))
        recorder = FakeRecorder(recording=False)
        stream = FakeStreamingSession({"success": True, "text": "撤回不用了", "streaming": True})
        app.recorder = recorder  # type: ignore[assignment]
        app.speech = FakeSpeech(stream)  # type: ignore[assignment]
        app.last_state = "PRESSED"

        start = app.start_agent_voice_session("调试页测试", allow_on_hook=True)
        self.assertTrue(start["ok"])
        app.stop_voice_recording("测试停止", reply_behavior="direct")

        self.assertEqual(app.reply_status()["queue_size"], 0)
        self.assertIn("语音取消", app.voice_status()["cancel_reason"])

    def test_agent_debug_start_can_record_while_on_hook(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao"))
        recorder = FakeRecorder(recording=False)
        app.recorder = recorder  # type: ignore[assignment]
        app.last_state = "PRESSED"

        result = app.start_agent_voice_session("调试页测试", allow_on_hook=True)

        self.assertTrue(result["ok"])
        self.assertTrue(recorder.is_recording())
        app.cancel_agent_voice_session("test cleanup")

    def test_agent_debug_start_returns_json_error_when_audio_device_fails(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao"))
        app.recorder = FailingRecorder(recording=False)  # type: ignore[assignment]
        app.last_state = "PRESSED"

        result = app.start_agent_voice_session("调试页测试", allow_on_hook=True)

        self.assertFalse(result["ok"])
        self.assertIn("audio input failed", result["error"])
        self.assertFalse(app.voice_status()["recording"])

    def test_hangup_during_agent_playback_stops_current_report_only(self) -> None:
        app = make_app(self, ConsoleConfig(business_mode="doubao"))
        app.last_state = "PRESSED"
        app.active_reply = ReplyTask(id="reply-test", source="voice-asr", title="语音识别回报", text="完成")
        app.callback_session_active = True
        calls: list[str] = []
        app.stop_reply_playback = lambda reason="", wait_seconds=0.0: calls.append("stop") or True  # type: ignore[method-assign]
        app.clear_voice_replies = lambda reason="": calls.append("clear_voice")  # type: ignore[method-assign]
        app.clear_ai_alert = lambda reason="": calls.append("clear_alert") or True  # type: ignore[method-assign]
        app.submit_agent_voice_turn_after_hangup = lambda reason="": calls.append("submit") or False  # type: ignore[method-assign]

        app.handle_hook_transition("RELEASED", "PRESSED")

        self.assertEqual(calls, ["stop", "clear_voice", "submit", "clear_alert"])


if __name__ == "__main__":
    unittest.main()
