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

## Minimal Agent Runtime

The first Agent runtime is intentionally small in surface area, but its internal
shape follows a larger PI-style model-driven Agent:

1. A user turn enters through phone ASR or `POST /api/agent/turn`.
2. The turn is appended to an append-only session tree as a `user` message.
3. The prompt builder assembles `system`, `developer`, available tools, available
   skills, loaded skill content, compaction summary, and recent session messages.
4. The Agent loop plans one or more explicit tool calls.
5. Planned calls are stored as an assistant `toolCall` message.
6. Tool calls are executed by registered skills and stored as `toolResult`
   messages.
7. The phone-agent role model turns the user text, tool result summary, and
   recent history into a spoken reply.
8. The spoken reply is stored as an assistant message, then enters the existing
   reply queue and callback flow.
9. When context grows, older session entries are compacted into a structured
   summary while recent messages remain in context.

Current runtime file:

```text
tools/agent_runtime.py
```

Current skills:

```text
command_center.earth   -> command-center map/globe control
web.info               -> weather lookup, browser URL opening, browser search
local.files            -> PI-style read-only file inspection
system.app             -> allowlisted local app launching
system.command         -> explicit, guarded shell command execution
```

They support these tool calls:

```text
set_phase       -> command center status phase
focus_city      -> command center city navigation
fly_to          -> command center lng/lat navigation
show_globe      -> return to globe standby view
lookup_weather  -> query a compact weather summary for spoken reply
search_web      -> open a browser search for an explicit search request
open_url        -> open a URL in the browser
read            -> read a project file with size limits
grep            -> search project text with match limits
find            -> find project paths by name or glob
ls              -> list a project directory
launch_app      -> launch an allowlisted local app
run_command     -> run an explicit shell command with dangerous patterns blocked
```

The runtime also has a PI-inspired skill loader:

```text
~/.pi/agent/skills/
~/.agents/skills/
<project>/.pi/skills/
<project>/.agents/skills/
AI_DESK_PHONE_SKILL_PATHS
```

Skills follow progressive disclosure: the prompt always receives only the skill
name and description; full `SKILL.md` content is loaded only for `/skill:name`
or a matching task. Agent sessions are written as JSONL under:

```text
data/agent_sessions/
```

Backend text entry:

```http
POST http://127.0.0.1:8765/api/agent/turn
Content-Type: application/json

{
  "source": "codex",
  "text": "定位北京",
  "reply_behavior": "direct"
}
```

The command center page receives skill effects through `/events` as
`command_center_command` events. The page then calls its existing
`window.AILandline` bridge, so the Earth renderer remains a UI skill target
rather than backend-rendered state.

### Reference posture

Keep the demo small, but borrow the right shape from existing assistants and
agent systems:

1. Pi-style interaction: the phone Agent should feel calm, patient, and
   conversational. It should not expose internal telemetry, raw JSON, or tool
   chatter to the user unless the user asks for diagnostics.
2. Tool-call loop: a user turn should become explicit tool calls, the app should
   execute those tools, and the final spoken report should summarize the result.
3. Skill ownership: each skill owns one bounded surface. The Earth skill owns
   command-center navigation only; web, local app, and shell skills must not
   reach into renderer internals or mutate unrelated phone state.
4. Traceability: each turn should keep enough metadata to debug later: input
   text, planned tool calls, tool results, final report, and timing.
5. Pi-inspired minimal tooling: Pi defaults to read/bash/edit/write and can
   enable grep/find/ls. The phone Agent adapts that shape more conservatively:
   read-only `read/grep/find/ls`, browser/search, allowlisted app launch, and
   guarded command execution are in the runtime; file mutation and multi-agent
   handoff stay out until they have a permission model that fits voice control.
6. Shell safety: shell execution only runs for explicit command phrases such as
   "执行命令 ..."; dangerous patterns are blocked, execution is time-limited, and
   output is truncated before entering the Agent result.
7. File safety: local file tools only read inside the current project root. They
   skip bulky/generated directories, limit file sizes, and truncate returned
   content before it enters the Agent result.
8. Conversation continuity: the local phone Agent keeps a PI-style append-only
   session tree with `id`/`parentId` links. The console still exposes a compact
   recent-turn view for the phone role model, while the runtime keeps full
   message/tool metadata for debugging and future branching/resume work.
9. Compaction: long sessions should summarize old messages using the structured
   Goal / Constraints / Progress / Key Decisions / Critical Context format and
   keep recent messages verbatim.

## Shortcut Profiles

1. Multiple shortcut profiles can be created.
2. A profile contains the off-hook action and the on-hook action.
3. Profiles can be switched, renamed, and deleted from the config page.
4. The active profile is the only profile sent to firmware or executed locally.

## ASR Vocabulary

Domain-specific words that ordinary ASR may miss, such as product names,
project names, "Codex", or user-defined terms like "键斗", should be handled by
the speech provider's hotword/boosting-table mechanism where available.
