"""
Jarvis AI Assistant — Pipecat voice pipeline
ChatGPT-style: always listening, natural turn detection, barge-in, fast local TTS.
Just speak — no wake word needed.
"""
import asyncio
import json
import os
import sys
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)  # silence FastAPI/uvicorn noise

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

import anthropic as _anthropic

# ── Pipecat ───────────────────────────────────────────────────────────────────
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InputAudioRawFrame,
    LLMContextFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.transports.local.audio import LocalAudioTransport, LocalAudioTransportParams
from pipecat.turns.user_turn_strategies import UserTurnStrategies
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.workers.runner import WorkerRunner

_ENV_PATH = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, encoding="utf-8", override=True)

# ─── Config ───────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL             = "claude-sonnet-4-6"
WS_PORT           = 8765
WHISPER_MODEL     = "base"  # "tiny"=fast/inaccurate | "base"=balanced | "small"=accurate/slow
VAD_STOP_SECS     = 0.8     # silence before a speech SEGMENT closes (→ Whisper transcribes it).
                            # Was 0.2 — too aggressive: chopped sentences mid-phrase, dropping words.
                            # 0.8 keeps natural micro-pauses inside one clean chunk for Whisper.
VAD_START_SECS    = 0.3     # sustained speech needed to OPEN a turn
VAD_MIN_VOLUME    = 0.6     # volume floor — raised from 0.1 so speaker echo doesn't fake a barge-in
TURN_PAUSE_SECS   = 2.0     # total silence after which the turn ends and Claude is called
SPEAKER_LOCK      = True    # only obey the enrolled owner's voice (if jarvis_voiceprint.npy exists)

# ─── First-run greeting ───────────────────────────────────────────────────────
_GREETED_PATH = Path(__file__).parent.parent / "jarvis_greeted.flag"

def _is_first_run() -> bool:
    return not _GREETED_PATH.exists()

def _mark_greeted() -> None:
    _GREETED_PATH.touch()

async def _piper_greeting(broadcaster_proc) -> None:
    """
    Inject a first-run greeting through the Piper TTS voice.
    Waits for Whisper + Piper to initialize before speaking.
    Uses TTSSpeakFrame which flows broadcaster → LLM (pass-through) → TTS → speakers.
    """
    from pipecat.frames.frames import TTSSpeakFrame
    await asyncio.sleep(4.5)   # wait for models to load
    try:
        await broadcaster_proc.push_frame(TTSSpeakFrame("Hi Kalo! Jarvis online and ready."))
        _mark_greeted()
        ilog("━━━ First-run greeting sent via Piper ━━━")
    except Exception as e:
        ilog(f"Greeting error: {e}")

# ─── Logging ──────────────────────────────────────────────────────────────────
_LOG_PATH = Path(__file__).parent.parent / "jarvis.log"
logger.remove()
logger.add(sys.stderr, level="INFO",  format="[{time:HH:mm:ss}] {message}")
logger.add(str(_LOG_PATH), level="DEBUG", rotation="10 MB", encoding="utf-8",
           format="[{time:HH:mm:ss.SSS}] {message}")

def dlog(msg: str) -> None: logger.debug(msg)
def ilog(msg: str) -> None: logger.info(msg)   # also prints to terminal

# ─── Memory ───────────────────────────────────────────────────────────────────
_MEMORY_PATH = Path(__file__).parent.parent / "jarvis_memory.json"

def load_memory() -> Dict:
    try:
        if _MEMORY_PATH.exists():
            return json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"recent": []}

def save_memory(mem: Dict) -> None:
    try:
        _MEMORY_PATH.write_text(
            json.dumps(mem, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        dlog(f"Memory save error: {e}")

_JARVIS_ROOT = Path(__file__).parent.parent   # C:\Users\Amit\jarvis\

_BASE_SYSTEM = (
    "You are Jarvis, a local AI assistant on Kalo's Windows computer. "
    "Kalo is the user — use their name naturally in conversation. "
    "You have full OS control: click, type, open apps, move windows, run commands, see the screen.\n\n"

    "VOICE RULES (critical — responses are spoken aloud):\n"
    "- Reply in 1 sentence maximum. 2 sentences only if truly necessary.\n"
    "- Never use bullet points, lists, markdown, asterisks, or numbered steps.\n"
    "- After completing a tool action, confirm in one short sentence only.\n"
    "- Never apologise. Be direct and confident.\n\n"

    "WINDOW AWARENESS:\n"
    "- The Jarvis UI window title is exactly 'J.A.R.V.I.S'.\n"
    "- When Kalo says 'your window', 'the UI', 'make it bigger', 'your interface' "
    "→ target the 'J.A.R.V.I.S' window.\n"
    "- The backend terminal window is NOT the UI — never resize/maximize it for UI requests.\n\n"

    "TOOL ROUTING:\n"
    "- Single actions → direct tools (click_at, type_text, open_application, run_command…).\n"
    "- PowerShell needed (registry, services, WMI, network) → run_powershell.\n"
    "- File ops: create_file, read_file, edit_file, delete_file, move_file, copy_file.\n"
    "- Web tasks (any website interaction) → browser_* tools. See below.\n"
    "- Complex non-browser multi-step UI task → run_os_task. "
    "Say 'On it' first, speak the summary when done.\n\n"

    "BROWSER CAPABILITIES (Playwright Chrome):\n"
    "- You have a dedicated Chrome window (browser_* tools) with a PERSISTENT profile.\n"
    "- The browser remembers all of Kalo's logins — Google, GitHub, cloud consoles, "
    "social media, everything signed in on that profile stays signed in.\n"
    "- FIRST-TIME: if a site isn't signed in yet, navigate there and ask Kalo to log in "
    "once — after that it's remembered forever.\n"
    "- Workflow for any web task: browser_navigate → browser_snapshot (read the page) → "
    "browser_click / browser_type → browser_snapshot again (confirm) → browser_screenshot "
    "(if you need to visually verify or read a key/token) → browser_close when done.\n"
    "- To get an API key: navigate directly to the settings/keys page, click 'Create', "
    "read the key with browser_screenshot, copy it to clipboard with set_clipboard, then tell Kalo.\n"
    "- Always browser_close after finishing a web task to free the window.\n\n"

    "SELF-IMPROVEMENT (you can edit your own code):\n"
    f"- Root: {_JARVIS_ROOT}\n"
    "- Frontend (live-reload): frontend/styles.css, index.html, renderer.js\n"
    "  → read_file → edit_file or create_file → reload_ui()\n"
    "- Backend (auto-restart): backend/tools.py, main.py, operator_agent.py\n"
    "  → read_file → edit_file or create_file → restart_backend()\n"
    "- Always read_file first. Confirm change in one sentence.\n\n"

    "MEMORY (you remember things across sessions):\n"
    "- Use `remember` PROACTIVELY whenever Kalo shares a durable fact about himself, "
    "his preferences, people/pets, projects, or plans — don't wait to be asked.\n"
    "- Facts you already know are injected each turn under 'WHAT YOU KNOW ABOUT KALO' — "
    "treat them as established and never re-ask what you already know.\n"
    "- Use `forget` when Kalo corrects you or asks you to drop something.\n"
    "- The 'RECENT ACTIONS this session' block lists results of tools you just ran "
    "(e.g. a screenshot's file path) — reuse those exact values instead of guessing."
)

def build_system_prompt(mem: Dict) -> str:
    prompt = _BASE_SYSTEM

    # Tell Claude whether voice-lock is active so it can answer "do you know my voice?"
    voiceprint = Path(__file__).parent.parent / "jarvis_voiceprint.npy"
    if SPEAKER_LOCK and voiceprint.exists():
        prompt += (
            "\n\nVOICE LOCK: Speaker verification is ACTIVE. Every word you receive has "
            "already been confirmed as Kalo's voice — anyone else is silently filtered out "
            "before reaching you. So yes, you DO recognise Kalo's voice; if asked, confirm it.\n"
        )
    else:
        prompt += (
            "\n\nVOICE LOCK: Speaker verification is OFF (no voiceprint enrolled yet). "
            "You currently respond to anyone. If Kalo asks, tell them to run the enrollment "
            "(it happens automatically on the next start.bat launch).\n"
        )

    recent = mem.get("recent", [])
    if recent:
        lines = ["\nRecent memory (context about Kalo's past requests):"]
        for e in recent[-5:]:
            lines.append(f'  [{e["time"]}] "{e["q"]}" -> "{e["r"]}"')
        prompt += "\n".join(lines)
    return prompt

# ─── Mic config ───────────────────────────────────────────────────────────────
_MIC_CONFIG_PATH = Path(__file__).parent.parent / "jarvis_mic.json"

def load_mic_config() -> Optional[Dict]:
    try:
        if _MIC_CONFIG_PATH.exists():
            return json.loads(_MIC_CONFIG_PATH.read_text())
    except Exception:
        pass
    return None

def _test_device(index: int) -> bool:
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        s = p.open(format=pyaudio.paInt16, channels=1, rate=16000,
                   input=True, input_device_index=index, frames_per_buffer=512)
        s.close(); p.terminate()
        return True
    except Exception:
        try: p.terminate()
        except: pass
        return False

def _device_is_wasapi(index: int) -> bool:
    """Return True if device uses Windows WASAPI (has hardware AEC)."""
    try:
        import pyaudio
        p = pyaudio.PyAudio()
        d  = p.get_device_info_by_index(index)
        ha = p.get_host_api_info_by_index(d["hostApi"])["name"]
        p.terminate()
        return "WASAPI" in ha
    except Exception:
        return False

def _find_working_mic() -> Optional[int]:
    """
    Scan for the best available input device.
    Priority: WASAPI Intel SST (hardware AEC) > WDM-KS Intel SST "Array 2" > other Intel > rest.
    WASAPI devices have hardware echo cancellation — use them whenever possible.
    """
    import pyaudio
    p = pyaudio.PyAudio()
    devices = []
    for i in range(p.get_device_count()):
        d  = p.get_device_info_by_index(i)
        ha = p.get_host_api_info_by_index(d["hostApi"])["name"]
        if d["maxInputChannels"] > 0:
            devices.append((i, d["name"], ha))
    p.terminate()

    def _priority(entry):
        _, name, ha = entry
        is_intel  = "Intel" in name or "SST" in name
        is_wasapi = "WASAPI" in ha
        is_arr2   = "Array 2" in name and "WDM-KS" in ha
        if is_intel and is_wasapi: return 0   # best: WASAPI with hardware AEC
        if is_intel and is_arr2:   return 1   # good: WDM-KS AEC-processed array
        if is_intel:               return 2   # ok: other Intel mic
        return 3                              # fallback: anything else

    for idx, name, ha in sorted(devices, key=_priority):
        if _test_device(idx):
            ilog(f"Using mic [{idx}]: {name} [{ha}]")
            return idx
    return None

# ─── FastAPI + WebSocket ──────────────────────────────────────────────────────
_clients:       Set[WebSocket]       = set()
_current_state: str                  = "idle"
_llm_context:   Optional[LLMContext] = None

async def broadcast(msg: Dict) -> None:
    dead = set()
    for ws in _clients:
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            dead.add(ws)
    _clients.difference_update(dead)

# Lifespan replaces the deprecated @app.on_event pattern
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(_run_pipeline())
    ilog("Jarvis backend starting...")
    yield
    ilog("Jarvis shutting down")

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"])

@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    _clients.add(websocket)
    dlog(f"Frontend connected (total={len(_clients)})")
    await websocket.send_text(json.dumps({"type": "state", "value": _current_state}))
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
            except Exception:
                continue
            if msg.get("type") == "clear_history" and _llm_context is not None:
                kept = [m for m in (_llm_context.messages or [])
                        if m.get("role") in ("system", "developer")]
                _llm_context.messages[:] = kept
                ilog("Conversation history cleared")
                await websocket.send_text(json.dumps({"type": "log", "text": "Conversation cleared."}))
    except WebSocketDisconnect:
        _clients.discard(websocket)
        dlog("Frontend disconnected")


# ─── Shared state (broadcaster ↔ response capture) ───────────────────────────
_last_transcription: str = ""   # set by JarvisBroadcaster, read by ResponseCapture


# ─── Frame processor: state → WebSocket + terminal logs ──────────────────────

class JarvisBroadcaster(FrameProcessor):
    """
    Sits between user_agg and llm (upstream side).
    Sees: UserStarted/StoppedSpeaking, TranscriptionFrame, BotStarted/StoppedSpeaking.
    Does NOT see TextFrames from Claude — those go downstream past the LLM.
    Response text and comm-log broadcast are handled by ResponseCapture.
    """

    def __init__(self) -> None:
        super().__init__()

    async def process_frame(self, frame: Frame, direction) -> None:
        global _current_state, _last_transcription
        await super().process_frame(frame, direction)

        if isinstance(frame, UserStartedSpeakingFrame):
            _current_state = "listening"
            await broadcast({"type": "state", "value": "listening"})

        elif isinstance(frame, UserStoppedSpeakingFrame):
            _current_state = "thinking"
            await broadcast({"type": "state", "value": "thinking"})

        elif isinstance(frame, TranscriptionFrame):
            _last_transcription = frame.text
            ilog(f"\n{'='*55}\n>> YOU:    {frame.text}")
            await broadcast({"type": "transcript", "text": frame.text})

        elif isinstance(frame, BotStartedSpeakingFrame):
            _current_state = "speaking"
            await broadcast({"type": "state", "value": "speaking"})

        elif isinstance(frame, BotStoppedSpeakingFrame):
            _current_state = "idle"
            await broadcast({"type": "state", "value": "idle"})

        await self.push_frame(frame, direction)




# ─── Software echo gate (no hardware AEC on device 8) ────────────────────────

class EchoGate(FrameProcessor):
    """Mutes mic input while Jarvis is speaking to prevent speaker→mic echo."""

    def __init__(self) -> None:
        super().__init__()
        self._bot_speaking = False

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, BotStartedSpeakingFrame):
            self._bot_speaking = True
        elif isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False

        # Drop mic audio while bot is talking
        if isinstance(frame, InputAudioRawFrame) and self._bot_speaking:
            return

        await self.push_frame(frame, direction)


# ─── Speaker verification gate ───────────────────────────────────────────────

ENROLL_TARGET_SECS = 6.0   # voiced seconds of the owner needed to build a voiceprint


class SpeakerGate(FrameProcessor):
    """
    Locks Jarvis to the owner's voice, with VOICE-DRIVEN enrollment.

    Sits right after Whisper. Two modes:

    ENROLL  (no voiceprint yet): Jarvis greets the user and asks them to speak.
            The gate accumulates their audio across utterances; once it has
            ~ENROLL_TARGET_SECS of voiced speech it builds the voiceprint and
            switches to VERIFY. Enrollment chatter is NOT sent to Claude.

    VERIFY  (voiceprint exists): every transcription is checked against the
            voiceprint; non-matching speech is DROPPED before it can trigger a
            turn, so Jarvis stays silent for anyone who isn't the owner.

    If SPEAKER_LOCK is False, the gate is a transparent pass-through.
    """

    def __init__(self) -> None:
        super().__init__()
        self._buf = bytearray()
        self._sr  = 16000
        self._max_bytes = 16000 * 2 * 12      # cap rolling buffer at ~12s int16 PCM
        from speaker_id import VoiceID
        self._vid = VoiceID()

        if not SPEAKER_LOCK:
            self._mode = "off"
            ilog("SpeakerGate: disabled by config (SPEAKER_LOCK=False).")
        elif self._vid.is_enrolled():
            self._mode = "verify"
            ilog(f"SpeakerGate: VERIFY — locked to the enrolled voice "
                 f"(threshold {self._vid.threshold}).")
        else:
            self._mode = "enroll"
            self._enroll_pcm = bytearray()   # accumulates owner audio across utterances
            ilog("SpeakerGate: ENROLL — will learn the owner's voice on first speech.")

    # ── helpers ──────────────────────────────────────────────────────────────
    async def _speak(self, text: str) -> None:
        """Make Jarvis say something by injecting a TTSSpeakFrame downstream."""
        await self.push_frame(TTSSpeakFrame(text), FrameDirection.DOWNSTREAM)

    async def announce_enrollment(self) -> None:
        """Called once after the pipeline is ready, if we're in enroll mode."""
        if self._mode == "enroll":
            await self._speak(
                "Hello! I don't know your voice yet. Please say hello and tell me "
                "a little about yourself for a few seconds, so I can learn it."
            )
            await broadcast({"type": "enroll_status", "text": "Learning your voice — keep talking…"})

    def _pcm_from(self, buf: bytearray):
        import numpy as np
        return np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0

    # ── main frame handler ───────────────────────────────────────────────────
    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)

        if self._mode == "off":
            await self.push_frame(frame, direction)
            return

        # Accumulate mic audio into the rolling buffer
        if isinstance(frame, InputAudioRawFrame):
            self._sr = getattr(frame, "sample_rate", self._sr) or self._sr
            self._buf.extend(frame.audio)
            if len(self._buf) > self._max_bytes:
                del self._buf[: len(self._buf) - self._max_bytes]
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, TranscriptionFrame):
            if self._mode == "enroll":
                await self._handle_enroll(frame)
                return    # never forward enrollment speech to Claude
            elif self._mode == "verify":
                if not await self._handle_verify(frame):
                    return    # dropped — unknown speaker

        await self.push_frame(frame, direction)

    # ── enroll mode ──────────────────────────────────────────────────────────
    async def _handle_enroll(self, frame: TranscriptionFrame) -> None:
        # Append this utterance's audio to the enrollment accumulator
        self._enroll_pcm.extend(self._buf)
        self._buf.clear()

        pcm = self._pcm_from(self._enroll_pcm)
        voiced = self._vid.voiced_seconds(pcm, self._sr)
        ilog(f"SpeakerGate[enroll]: '{frame.text}' — {voiced:.1f}s voiced so far")
        await broadcast({"type": "enroll_status",
                         "text": f"Learning your voice… {voiced:.0f}/{int(ENROLL_TARGET_SECS)}s"})

        if voiced >= ENROLL_TARGET_SECS:
            try:
                self._vid.enroll([pcm], self._sr)
                self._mode = "verify"
                self._enroll_pcm = bytearray()
                ilog("SpeakerGate: voiceprint created — now locked to the owner.")
                await broadcast({"type": "enroll_status", "text": "Voice learned — locked to you."})
                await broadcast({"type": "enrolled"})
                await self._speak("Got it — I've learned your voice. I'll only respond to you now. How can I help?")
            except Exception as exc:
                ilog(f"SpeakerGate: enroll failed ({exc}) — asking for more speech.")
                await self._speak("Sorry, I didn't catch enough. Please keep talking for a few more seconds.")
        else:
            await self._speak("Great, keep going — tell me a bit more.")

    # ── verify mode ──────────────────────────────────────────────────────────
    async def _handle_verify(self, frame: TranscriptionFrame) -> bool:
        ok, score = True, 1.0
        if len(self._buf) >= self._sr * 2:        # need ≥1s of audio to judge
            ok, score = self._vid.verify(self._pcm_from(self._buf), self._sr)
        self._buf.clear()

        verdict = "ACCEPT" if ok else "IGNORE"
        ilog(f"SpeakerGate[verify]: {verdict} score={score:.2f} thr={self._vid.threshold} — '{frame.text}'")

        if not ok:
            await broadcast({"type": "ignored_speaker", "text": frame.text, "score": round(score, 2)})
            return False
        return True


# ─── Parallel LLM engine ─────────────────────────────────────────────────────
# Each user turn fires an asyncio.create_task() and returns immediately.
# The pipeline is NEVER blocked waiting for Claude.
# Multiple turns can process concurrently; responses are delivered in FIFO order.
#
# Latency optimisation: _bg_turn STREAMS the Claude response.
# As soon as a sentence boundary is detected in the token stream, that sentence
# is pushed to _delivery_queue immediately — TTS starts on sentence 1 while
# Claude is still generating sentence 2+.  Tool-use turns fall back to blocking.

import re as _re

_delivery_queue: asyncio.Queue = asyncio.Queue()

# ── Working memory: what Jarvis has just DONE this session ────────────────────
# Captures tool calls + their results (e.g. "take_screenshot -> C:\...\shot.png")
# so the next turn knows things like where a file was just saved. Injected into
# the system prompt each turn. This is short-term/session memory.
_session_actions: list[str] = []
_SESSION_ACTIONS_MAX = 15

def _record_action(tool: str, inputs, result) -> None:
    inp = json.dumps(inputs)[:60] if inputs else ""
    res = ("[image]" if isinstance(result, list) else str(result))[:140]
    _session_actions.append(f"{tool}({inp}) -> {res}")
    del _session_actions[:-_SESSION_ACTIONS_MAX]

def _working_memory_block() -> str:
    if not _session_actions:
        return ""
    lines = "\n".join(f"  - {a}" for a in _session_actions[-8:])
    return ("\n\nRECENT ACTIONS this session (use these exact results — e.g. file "
            f"paths you just created/saved):\n{lines}\n")


def _recall_block(query: str) -> str:
    """
    Inject Jarvis's long-term knowledge about Kalo (the persistent fact store)
    into the system prompt. Pure JSON read + keyword ranking — no ML model, so
    it adds essentially zero runtime cost.
    """
    try:
        facts = load_memory().get("facts", [])
    except Exception:
        facts = []
    if not facts:
        return ""

    # Few facts → include them all. Many → keyword-rank against the query.
    if len(facts) <= 30:
        chosen = facts
    else:
        qwords = set(_re.findall(r"\w+", (query or "").lower()))
        def _score(f):
            fwords = set(_re.findall(r"\w+", f.get("text", "").lower()))
            return len(qwords & fwords)
        chosen = sorted(facts, key=_score, reverse=True)[:18]

    lines = "\n".join(f"  - {f.get('text', '')}" for f in chosen)
    return ("\n\nWHAT YOU KNOW ABOUT KALO (long-term memory — treat as established "
            f"fact, don't re-ask):\n{lines}\n")

# Sentence boundary: end-of-sentence punctuation followed by whitespace or EOL
_SENT_END_RE = _re.compile(r'(?<=[.!?…])\s+|(?<=[.!?…])$')


def _pop_sentences(buf: str) -> tuple[list[str], str]:
    """
    Extract all complete sentences from buf.
    Returns (complete_list, leftover).
    """
    parts = _SENT_END_RE.split(buf)
    if len(parts) <= 1:
        return [], buf          # no sentence boundary found yet
    # Everything except the last fragment is a complete sentence
    complete = [s.strip() for s in parts[:-1] if s.strip()]
    return complete, parts[-1]  # last part may be an incomplete sentence


async def _enqueue_sentence(
    sentence: str, tid: int, query: str, is_first: bool, memory: Dict | None
) -> None:
    """Broadcast UI update and push one sentence to the delivery queue."""
    if is_first:
        await broadcast({"type": "responding_to", "text": query[:70]})
    await broadcast({"type": "response", "text": sentence})
    await _delivery_queue.put({
        "text":   sentence,
        "query":  query  if is_first else "",
        "tid":    tid,
        "memory": memory if is_first else None,
    })


async def _run_tool_loop(
    client: "_anthropic.AsyncAnthropic",
    msgs: list, system: str, tools_def: list
) -> tuple[str, list]:
    """
    Run the blocking (non-streaming) tool-use loop.
    Returns (final_text_response, updated_msgs).
    """
    from tools import execute_tool
    response_text = ""
    while True:
        resp = await client.messages.create(
            model=MODEL, max_tokens=1024,
            system=system, messages=msgs, tools=tools_def,
        )
        if resp.stop_reason == "tool_use":
            tool_results = []
            for block in resp.content:
                if block.type == "tool_use":
                    ilog(f"## TOOL:  {block.name}({block.input})")
                    await broadcast({"type": "tool_use", "name": block.name})
                    try:
                        result = await asyncio.get_event_loop().run_in_executor(
                            None, execute_tool, block.name, block.input
                        )
                    except Exception as exc:
                        result = f"Tool error: {exc}"
                    await broadcast({
                        "type": "tool_result", "name": block.name,
                        "result": "[screen]" if isinstance(result, list) else str(result)[:200],
                    })
                    _record_action(block.name, block.input, result)   # remember what we did
                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     result if isinstance(result, list) else str(result),
                    })
            msgs = msgs + [
                {"role": "assistant", "content": list(resp.content)},
                {"role": "user",      "content": tool_results},
            ]
        else:
            for block in resp.content:
                if hasattr(block, "text"):
                    response_text += block.text
            break
    return response_text, msgs


async def _bg_turn(tid: int, messages: list, query: str, memory: Dict) -> None:
    """
    Background task: stream Claude API, deliver sentence-by-sentence to TTS.
    Tool-use turns fall back to the blocking loop then deliver the full reply.
    Runs completely outside the pipeline — InterruptionFrame cannot cancel it.
    """
    from tools import TOOLS_DEFINITION

    tools_def = [
        {"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
        for t in TOOLS_DEFINITION
    ]

    system = ""
    msgs: list = []
    for m in messages:
        if m.get("role") == "system":
            system = m.get("content", "")
        else:
            msgs.append(m)

    # Augment the system prompt with what Jarvis has just done + relevant memories
    system += _working_memory_block()
    system += _recall_block(query)

    await broadcast({"type": "bg_thinking", "tid": tid, "query": query[:60]})
    ilog(f"[Turn {tid}] → '{query[:50]}'")

    client = _anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    try:
        full_response = ""
        sent_count    = 0
        is_tool_turn  = False

        # ── Streaming pass (pure-text turns) ────────────────────────────────
        pending = ""
        async with client.messages.stream(
            model=MODEL, max_tokens=1024,
            system=system, messages=msgs, tools=tools_def,
        ) as stream:
            async for event in stream:
                etype = getattr(event, "type", None)

                if etype == "content_block_start":
                    if getattr(event.content_block, "type", None) == "tool_use":
                        is_tool_turn = True
                        break   # abort streaming — handle via blocking loop

                elif etype == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta and getattr(delta, "type", None) == "text_delta":
                        chunk = delta.text
                        full_response += chunk
                        pending       += chunk

                        complete, pending = _pop_sentences(pending)
                        for sentence in complete:
                            await _enqueue_sentence(
                                sentence, tid, query,
                                is_first=(sent_count == 0), memory=memory
                            )
                            sent_count += 1

            # Flush any remaining text (no trailing punctuation)
            if not is_tool_turn and pending.strip():
                await _enqueue_sentence(
                    pending.strip(), tid, query,
                    is_first=(sent_count == 0), memory=memory
                )
                sent_count += 1

        # ── Tool-use fallback (blocking) ─────────────────────────────────────
        if is_tool_turn:
            full_response, _ = await _run_tool_loop(client, msgs, system, tools_def)
            if full_response:
                await _enqueue_sentence(
                    full_response.strip(), tid, query,
                    is_first=True, memory=memory
                )
                sent_count += 1

        ilog(f"<< JARVIS (→ '{query[:40]}'): {full_response[:120]}\n{'='*55}")

        # ── Persist context + memory ─────────────────────────────────────────
        if _llm_context and full_response:
            _llm_context.add_message({"role": "assistant", "content": full_response})

        if query and full_response and memory is not None:
            memory.setdefault("recent", []).append({
                "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "q": query[:120],
                "r": full_response[:160],
            })
            memory["recent"] = memory["recent"][-20:]
            save_memory(memory)

    except Exception as exc:
        ilog(f"[Turn {tid}] error: {exc}")
        await broadcast({"type": "error", "text": str(exc)[:120]})


class TurnDispatcher(FrameProcessor):
    """
    Replaces AnthropicLLMService in the pipeline.
    Receives LLMContextFrame → fires _bg_turn asyncio task → returns IMMEDIATELY.
    The pipeline is always free to keep listening.
    Runs a delivery loop that injects TTSSpeakFrame when a response is ready.
    """

    def __init__(self, memory: Dict) -> None:
        super().__init__()
        self._memory = memory
        self._tid    = 0

    def start_delivery_loop(self) -> None:
        """Call after the pipeline is running so push_frame has a valid context."""
        asyncio.create_task(self._deliver())

    async def _deliver(self) -> None:
        """
        Dequeue sentences from _delivery_queue and speak them via Piper TTS.
        Broadcasting and logging are handled upstream in _bg_turn / _enqueue_sentence.
        Sentences queue up while TTS is busy — each starts as soon as the previous ends.
        """
        while True:
            item = await _delivery_queue.get()
            # Wait until TTS finishes any current speech
            while _current_state == "speaking":
                await asyncio.sleep(0.05)

            text = item["text"].strip()
            if not text:
                continue

            # Inject this sentence into the pipeline → Piper TTS → speakers
            await self.push_frame(TTSSpeakFrame(text), FrameDirection.DOWNSTREAM)

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            tid = self._tid
            self._tid += 1
            # Extract the latest user message for the "responding to" label
            messages = frame.context.get_messages()
            query = ""
            for msg in reversed(messages or []):
                if msg.get("role") == "user":
                    c = msg.get("content", "")
                    query = (c if isinstance(c, str) else
                             c[0].get("text", "") if isinstance(c, list) else str(c))[:80]
                    break
            asyncio.create_task(_bg_turn(tid, list(messages), query, self._memory))
            # Do NOT forward LLMContextFrame — it's been dispatched as a task
        else:
            await self.push_frame(frame, direction)


# ─── Build and run the Pipecat pipeline ──────────────────────────────────────

async def _run_pipeline() -> None:
    global _llm_context

    # Wire up async broadcast so tools can push WebSocket messages from threads
    import tools as _tools_mod
    _tools_mod._init_async(asyncio.get_event_loop(), broadcast)

    memory    = load_memory()
    saved_mic = load_mic_config()

    if not ANTHROPIC_API_KEY:
        ilog("ERROR: ANTHROPIC_API_KEY not set in .env!")
        await broadcast({"type": "error", "text": "ANTHROPIC_API_KEY not set in .env!"})

    # ── Mic selection ─────────────────────────────────────────────────────────
    mic_index: Optional[int] = None
    if saved_mic is not None:
        if _test_device(saved_mic["index"]):
            mic_index = saved_mic["index"]
            ilog(f"Mic [{mic_index}]: {saved_mic['name']}")
        else:
            ilog(f"Saved mic [{saved_mic['index']}] unavailable, scanning for best device...")
            mic_index = _find_working_mic()
    else:
        ilog("No saved mic config, scanning for best device...")
        mic_index = _find_working_mic()

    transport_kwargs: Dict = dict(audio_in_enabled=True, audio_out_enabled=True)
    if mic_index is not None:
        transport_kwargs["input_device_index"] = mic_index
    transport = LocalAudioTransport(LocalAudioTransportParams(**transport_kwargs))

    # ── STT ───────────────────────────────────────────────────────────────────
    stt = WhisperSTTService(
        device="cpu",
        compute_type="int8",
        settings=WhisperSTTService.Settings(model=WHISPER_MODEL, language="en"),
    )

    # ── TTS ───────────────────────────────────────────────────────────────────
    _voice_dir = Path(__file__).parent / "voices"
    tts = PiperTTSService(
        download_dir=_voice_dir,
        settings=PiperTTSService.Settings(voice="en_US-lessac-medium"),
    )

    # ── Context (for user_agg conversation history) ───────────────────────────
    _llm_context = LLMContext()
    _llm_context.add_message({"role": "system", "content": build_system_prompt(memory)})

    # ── VAD + turn detection ──────────────────────────────────────────────────
    # No wake word — always listening, like ChatGPT voice mode.
    # SpeechTimeout waits TURN_PAUSE_SECS of silence before ending the turn,
    # replacing the default Smart Turn ML model that cut off after every phrase.
    vad = SileroVADAnalyzer(
        params=VADParams(
            stop_secs=VAD_STOP_SECS,
            start_secs=VAD_START_SECS,
            min_volume=VAD_MIN_VOLUME,
        )
    )

    turn_strategies = UserTurnStrategies(
        stop=[SpeechTimeoutUserTurnStopStrategy(user_speech_timeout=TURN_PAUSE_SECS)],
    )

    user_agg, _ = LLMContextAggregatorPair(
        _llm_context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=vad,
            user_turn_strategies=turn_strategies,
        ),
    )

    broadcaster    = JarvisBroadcaster()
    turn_disp      = TurnDispatcher(memory)
    speaker_gate   = SpeakerGate()   # drops speech that isn't the enrolled owner

    # ── Pipeline layout ───────────────────────────────────────────────────────
    # broadcaster is NOW before user_agg so it intercepts TranscriptionFrame
    # before user_agg consumes it.
    #
    # DOWNSTREAM: transport.input → stt → broadcaster → user_agg → turn_disp → tts → transport.output
    # UPSTREAM:   transport.output → tts → turn_disp → user_agg → broadcaster → stt → transport.input
    #
    # TranscriptionFrame (downstream): stt → broadcaster ✓ (transcript sent to UI)
    # LLMContextFrame (downstream):    user_agg → turn_disp → fires asyncio task, returns immediately
    # BotStarted/StoppedSpeaking (upstream from tts): tts → turn_disp → user_agg → broadcaster ✓
    # TTSSpeakFrame injected by TurnDispatcher._deliver → flows downstream to tts ✓
    has_hw_aec = mic_index is not None and _device_is_wasapi(mic_index)
    if has_hw_aec:
        ilog("Hardware AEC (WASAPI) — EchoGate disabled, full barge-in + parallel processing")
        pipeline_procs = [
            transport.input(),   # raw mic audio
            stt,                 # Whisper → TranscriptionFrame
            speaker_gate,        # ← drops non-owner speech before it can trigger a turn
            broadcaster,         # ← before user_agg: sees TranscriptionFrame ✓
            user_agg,            # VAD + turn collection → LLMContextFrame
            turn_disp,           # fires asyncio background task per turn; never blocks
            tts,                 # Piper TTS — receives TTSSpeakFrame injected by turn_disp
            transport.output(),  # speakers
        ]
    else:
        ilog("No hardware AEC — using software EchoGate (barge-in limited)")
        echo_gate = EchoGate()
        pipeline_procs = [
            transport.input(),
            echo_gate,
            stt,
            speaker_gate,
            broadcaster,
            user_agg,
            turn_disp,
            tts,
            transport.output(),
        ]

    # ── Pipeline ──────────────────────────────────────────────────────────────
    pipeline = Pipeline(pipeline_procs)

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,   # TTS stops when user speaks; background tasks keep running
            enable_metrics=False,
        ),
        idle_timeout_secs=None,
    )

    await broadcast({"type": "state", "value": "idle"})
    await broadcast({"type": "ready"})
    await broadcast({"type": "log",   "text": "Jarvis ready — just speak!"})
    ilog("━━━ Jarvis ready — just speak ━━━")

    # ── Start the response delivery loop ─────────────────────────────────────
    turn_disp.start_delivery_loop()

    # ── Voice enrollment prompt OR first-run greeting ────────────────────────
    if getattr(speaker_gate, "_mode", "") == "enroll":
        # No voiceprint yet → ask the owner to speak so we can learn their voice
        async def _prompt_enroll():
            await asyncio.sleep(5)   # wait for Whisper + Piper to finish loading
            await speaker_gate.announce_enrollment()
        asyncio.create_task(_prompt_enroll())
    elif _is_first_run():
        asyncio.create_task(_piper_greeting(broadcaster))

    runner = WorkerRunner()
    await runner.add_workers(worker)
    await runner.run()


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    dev = "--dev" in sys.argv
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=WS_PORT,
        log_level="warning",
        reload=dev,
        reload_dirs=[str(Path(__file__).parent)] if dev else None,
    )
