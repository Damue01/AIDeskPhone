# AI Desk Phone Interaction Targets

This file is the behavioral contract for the phone console. Keep implementation
and tests aligned with these flows.

## Input Method Mode

Target use: Codex, editors, chat boxes, or any third-party app that already owns
the final AI execution.

1. User lifts the handset.
2. The phone triggers the configured off-hook input action, usually starting
   speech input in the target app.
3. User speaks the command.
4. User hangs up.
5. The phone triggers the configured on-hook input action, usually ending speech
   input, waiting about one second, and pressing Enter.
6. The third-party AI app processes the submitted text.
7. When that app finishes, it calls the local hook endpoint.
8. The console turns the hook payload into a callback report. The report should
   preserve the important content from the AI result, but speak like a concise
   operator reporting back. If the Ark role model is configured, the hook text
   should be polished by that model before it enters the reply queue. If the
   model is unavailable, the original hook text is used.
9. The phone rings or lights up. When the user lifts the handset, the report is
   played. Playback should start from streamed TTS audio when the provider and
   local audio device support it, then fall back to full-file playback.
10. If the user hangs up while the report is playing, playback stops immediately.

## Agent Mode

Target use: the phone itself is the service terminal. No third-party app needs to
be in front.

1. User lifts the handset and speaks to the local Agent.
2. Speech recognition starts immediately and streams microphone audio to ASR
   while the user is still talking.
3. When the user pauses or hangs up after issuing a command, the current turn is
   submitted.
4. The Agent may acknowledge quickly, then continue the task in the background.
5. Hanging up after submitting a command only closes the speaker/listening side;
   it must not cancel the background task.
6. When the task completes, the console calls back using the existing LED and
   buzzer ring cadence.
7. When the user lifts the handset, the operator report plays and the user can
   continue the conversation. The report should use streamed TTS audio where
   possible.
8. If the user hangs up while the Agent is speaking, that speech stops and the
   current spoken report is considered dismissed.
9. If the user says an explicit cancellation phrase such as "撤回", "取消",
   or "不用了" before a task has been committed, the current voice turn should be
   canceled instead of submitted.

## Shortcut Profiles

1. Multiple shortcut profiles can be created.
2. A profile contains the off-hook action and the on-hook action.
3. Profiles can be switched, renamed, and deleted from the config page.
4. The active profile is the only profile sent to firmware or executed locally.

## ASR Vocabulary

Domain-specific words that ordinary ASR may miss, such as product names,
project names, "Codex", or user-defined terms like "键斗", should be handled by
the speech provider's hotword/boosting-table mechanism where available.
