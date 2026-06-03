# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bat
install.bat          # First-time setup: pip install, npm install, .env scaffold, Whisper pre-download
start.bat            # Launch Jarvis (Electron spawns the Python backend as a child process)
```

Dev mode (auto-reload on file save):
```bat
dev.bat              # backend: uvicorn --reload; frontend: fs.watch reload; DevTools open
```

Live log monitoring:
```powershell
Get-Content .\jarvis.log -Wait -Tail 40
```

## Architecture

Jarvis is a **local voice assistant** split into two processes managed as parent/child:

```
start.bat
  └─ Electron (frontend/main.js)          ← parent process
       ├─ spawns: python backend/main.py  ← child, killed on window close
       └─ loads:  frontend/index.html     ← UI
```

**Communication:** The backend runs a FastAPI WebSocket server on `ws://127.0.0.1:8765`. The Electron renderer connects automatically and reconnects every 3 s on drop.

### Audio pipeline (backend/main.py — Pipecat)

```
LocalAudioTransport (mic, device [27])
  → WhisperSTTService (faster-whisper small, CPU int8)
    → LLMContextAggregatorPair / user_agg (SileroVAD turn detection)
      → JarvisBroadcaster (WebSocket state emitter, memory saver)
        → AnthropicLLMService (Claude, streaming, tool calling)
          → PiperTTSService (en_US-lessac-medium, local, ~150ms)
            → LocalAudioTransport (speakers)
              → assistant_agg (context tracking)
```

**Turn detection**: `SileroVADAnalyzer(stop_secs=1.2, start_secs=0.2)` — the user must be silent for 1.2 s before the turn ends. This is the key parameter that makes conversation feel natural (no cut-off on thinking pauses).

**Barge-in**: Built into Pipecat — SileroVAD detects speech during TTS playback and the framework propagates `InterruptionFrame` to cancel output mid-stream.

**Always listening**: No wake word. Jarvis responds to any speech. Wake word can be added back by inserting an `OpenWakeWordService` processor before `stt` in the pipeline.

### State machine

States: `idle` → `listening` → `thinking` → `speaking` → `idle`

State changes are broadcast over WebSocket as `{"type": "state", "value": "<state>"}`. The frontend applies a `state-<state>` class to `<body>`, which drives all CSS animations and the Three.js shader uniform `stateF`.

`JarvisBroadcaster` (a Pipecat `FrameProcessor`) handles this mapping:
| Pipecat Frame | WebSocket state |
|---|---|
| `UserStartedSpeakingFrame` | listening |
| `UserStoppedSpeakingFrame` | thinking |
| `BotStartedSpeakingFrame` | speaking |
| `BotStoppedSpeakingFrame` | idle |

### WebSocket message protocol

| Direction | Type | Key fields |
|---|---|---|
| backend → frontend | `state` | `value` |
| backend → frontend | `transcript` | `text` |
| backend → frontend | `response` | `text` |
| backend → frontend | `amplitude` | `value` (0–1) |
| backend → frontend | `ready`, `log`, `error`, `tool_use`, `tool_result` | `text` / `name` |
| frontend → backend | `trigger` | *(manual mic button)* |
| frontend → backend | `clear_history` | *(clear conversation)* |

### Tool system (backend/tools.py — unchanged from pre-Pipecat)

Adding a new OS skill requires three edits in `tools.py`:
1. Write the Python function
2. Register it in `execute_tool()` → `registry` dict
3. Add its JSON schema to `TOOLS_DEFINITION` list

Claude receives all schemas on every call. `main.py._build_tool_schemas()` converts `TOOLS_DEFINITION` → Pipecat `FunctionSchema` objects automatically. `_register_tools(llm)` registers a universal async handler for each tool via `llm.register_function(name, handler)`. Tool results flow back via `params.result_callback(result)` — Anthropic API accepts both `str` and `list[ContentBlock]` (for analyze_screen vision).

Current tools: `open_application`, `open_url`, `search_web`, `get_time`, `get_system_info`, `take_screenshot`, `list_directory`, `create_file`, `read_file`, `type_text`, `press_keys`, `set_volume`, `get_clipboard`, `set_clipboard`, `run_command`, `run_python`, window tools (`list_open_windows`, `get_active_window`, `focus/maximize/minimize/restore/resize/move/snap/center_window`), mouse tools (`click_at`, `double_click_at`, `right_click_at`, `move_mouse_to`, `scroll_at`, `drag_mouse`, `get_mouse_position`), **`analyze_screen`** (takes screenshot, returns base64 image to Claude for visual analysis).

**Vision workflow:** `analyze_screen` returns a `list` of content blocks (`text` + `image`) instead of a string. `main.py._ask_claude` detects `isinstance(result, list)` and passes it directly as `tool_result.content` — the Anthropic API accepts both `str` and `list[ContentBlock]` here.

### Mic device selection

- Saved to `jarvis_mic.json` at repo root (auto-created; not committed)
- On startup the saved device is re-tested with `test_device_rms()`; if it returns `0.0` (broken/overflow, i.e. rms > 2.0) the backend scans all devices and picks the best valid one
- Device [27] "Microphone Array 2 (Intel SST)" is the correct mic on this machine; device [28] returns hardware overflow (rms ~667k) and is treated as broken

### Key tuning constants (backend/main.py top-of-file)

| Constant | Purpose |
|---|---|
| `VAD_STOP_SECS = 1.2` | Silence duration before Pipecat considers the user's turn ended |
| `VAD_START_SECS = 0.2` | Speech duration required to open a turn |
| `MODEL` | Claude model string |
| `WS_PORT = 8765` | FastAPI WebSocket port |

Adjust `VAD_STOP_SECS` to taste: lower (0.8) = snappier but cuts off thinking pauses; higher (2.0) = more patient but slower response start.

### Frontend (frontend/)

The UI is a **full-screen transparent HUD overlay** (Iron Man aesthetic). The window is frameless, transparent, and always-on-top. Most of the screen is click-through to the desktop; only the side panels and buttons capture clicks.

- **main.js** — Electron entry; spawns Python backend; uses `screen.getPrimaryDisplay().bounds` for fullscreen; sets `setIgnoreMouseEvents(true, {forward:true})` by default so the desktop is usable. Registers `Ctrl+Shift+J` global hotkey.
- **preload.js** — Exposes `window.electronAPI` (close, minimize, pin, onGlobalTrigger, **setIgnoreMouseEvents**) via `contextBridge`
- **renderer.js** — WebSocket client + Three.js scene + clock/uptime updater + comm-log panel + mouse-through tracking (`mousemove` → IPC `set-ignore-mouse-events`)
- **styles.css** — Transparent body; panels have `rgba(3,8,22,0.91)` backgrounds; state-driven colors on pulse-dot and status label
- Three.js loaded from CDN (`three@0.160.0`), no bundler

**Click-through mechanism:** renderer.js tracks `mousemove`, calls `window.electronAPI.setIgnoreMouseEvents(false)` when cursor enters `#titlebar`, `#panel-left`, `#panel-right`, or `.mic-btn`; reverts to `true` (click-through) otherwise.

### Configuration (.env)

```
ANTHROPIC_API_KEY=sk-ant-api03-...   # required
```

Optional overrides (not yet wired; modify constants in main.py directly):
```
JARVIS_MODEL, JARVIS_VOICE, JARVIS_WS_PORT
```

### Persistent files (gitignore candidates)

| File | Purpose |
|---|---|
| `.env` | API key |
| `jarvis_mic.json` | Saved mic device index + name |
| `jarvis.log` | Appended log from current/last run |
| `jarvis_memory.json` | Rolling last-20 conversation snippets, injected into system prompt as context |
