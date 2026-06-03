"""
OS Operator Agent — autonomous multi-step computer control via Claude Computer Use.

Architecture:
    Jarvis (voice) ──run_os_task()──► OperatorAgent ──► Claude Computer Use API
                                                       ↓
                          screenshot ──► decide ──► click/type/scroll ──► repeat
                                                       ↓
                               returns summary ──► Jarvis speaks result

Everything runs locally; only the "what to do next" decisions use the API.

Usage from tools.py:
    from operator_agent import run_operator_task
    result = run_operator_task("open YouTube and search for jazz", api_key)
"""
import base64
import io
import time
from typing import Callable, Optional

import pyautogui
from PIL import Image

pyautogui.FAILSAFE = False

# ── Config ────────────────────────────────────────────────────────────────────
OPERATOR_MODEL   = "claude-opus-4-5"        # best visual reasoning for computer use
OPERATOR_BETA    = "computer-use-2025-01-24"
COMPUTER_TOOL_TYPE = "computer_20250124"
MAX_STEPS        = 20                        # max action loops (raise for very long tasks)
STEP_DELAY       = 0.35                      # seconds to wait after each action (let UI settle)
SCREENSHOT_MAX_W = 1280                      # resize wide screens to reduce token cost


# ── Screen capture ────────────────────────────────────────────────────────────

def _screenshot() -> tuple[str, int, int]:
    """Capture full screen → (PNG base64, width, height)."""
    img = pyautogui.screenshot()
    if img.width > SCREENSHOT_MAX_W:
        ratio = SCREENSHOT_MAX_W / img.width
        img = img.resize((SCREENSHOT_MAX_W, int(img.height * ratio)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), img.width, img.height


# ── Action executor ───────────────────────────────────────────────────────────

def _execute_action(action: dict) -> str:
    """
    Execute one computer-use action from Claude.
    Returns a short description string (logged as progress).
    Raises on hard failures.
    """
    t = action.get("type", "")

    try:
        if t == "screenshot":
            return "screenshot"   # handled separately by the caller

        elif t in ("left_click", "right_click", "middle_click", "double_click"):
            x, y = int(action["coordinate"][0]), int(action["coordinate"][1])
            {
                "left_click":   pyautogui.click,
                "right_click":  pyautogui.rightClick,
                "middle_click": pyautogui.middleClick,
                "double_click": pyautogui.doubleClick,
            }[t](x, y)
            return f"{t} ({x},{y})"

        elif t == "left_click_drag":
            sx, sy = int(action["start_coordinate"][0]), int(action["start_coordinate"][1])
            ex, ey = int(action["coordinate"][0]), int(action["coordinate"][1])
            pyautogui.moveTo(sx, sy)
            pyautogui.dragTo(ex, ey, duration=0.4, button="left")
            return f"drag ({sx},{sy})→({ex},{ey})"

        elif t == "mouse_move":
            x, y = int(action["coordinate"][0]), int(action["coordinate"][1])
            pyautogui.moveTo(x, y, duration=0.15)
            return f"move ({x},{y})"

        elif t == "type":
            text = action.get("text", "")
            pyautogui.write(text, interval=0.02)
            return f"type '{text[:50]}'"

        elif t == "key":
            keys = action.get("text", "")
            # Claude sends "ctrl+c" style; pyautogui.hotkey takes separate args
            parts = keys.replace("+", " ").split()
            pyautogui.hotkey(*parts)
            return f"key {keys}"

        elif t == "scroll":
            x, y = int(action["coordinate"][0]), int(action["coordinate"][1])
            direction = action.get("direction", "down")
            amount = int(action.get("amount", 3))
            pyautogui.moveTo(x, y)
            clicks = -abs(amount) if direction in ("down", "right") else abs(amount)
            pyautogui.scroll(clicks)
            return f"scroll {direction} ×{amount} at ({x},{y})"

        elif t == "cursor_position":
            pos = pyautogui.position()
            return f"cursor at ({pos.x},{pos.y})"

        else:
            return f"(unknown action type: {t})"

    except Exception as e:
        return f"action error: {e}"


# ── Main operator loop ────────────────────────────────────────────────────────

def run_operator_task(
    task: str,
    api_key: str,
    progress_cb: Optional[Callable[[str], None]] = None,
    max_steps: int = MAX_STEPS,
) -> str:
    """
    Run an autonomous multi-step OS task using Claude's Computer Use API.

    Steps:
      1. Take initial screenshot
      2. Send screenshot + task to Claude (with computer tool)
      3. Claude replies with tool_use blocks (actions to take)
      4. Execute actions; if screenshot was requested, take a new one
      5. Send results back; repeat until Claude does end_turn
      6. Return Claude's final text summary (spoken by Jarvis)

    Args:
        task        : Natural language task description.
        api_key     : Anthropic API key.
        progress_cb : Optional callback(str) for step-by-step status updates.
        max_steps   : Safety cap on iterations.

    Returns:
        1-2 sentence plain-English summary of what was accomplished (spoken by Jarvis).
    """
    import anthropic

    def _log(msg: str) -> None:
        if progress_cb:
            progress_cb(msg)

    client = anthropic.Anthropic(api_key=api_key)

    # ── Initial screenshot ────────────────────────────────────────────────────
    b64, screen_w, screen_h = _screenshot()
    _log(f"[operator] task: {task[:60]}")

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": "image/png", "data": b64},
                },
                {
                    "type": "text",
                    "text": (
                        f"Current screen. Please complete this task: {task}\n\n"
                        "Be direct and efficient. Take screenshots to verify your actions worked. "
                        "When done, write a 1-2 sentence plain-English summary of what you accomplished "
                        "(no markdown, no bullet points — this will be spoken aloud)."
                    ),
                },
            ],
        }
    ]

    computer_tool = {
        "type": COMPUTER_TOOL_TYPE,
        "name": "computer",
        "display_width_px":  screen_w,
        "display_height_px": screen_h,
    }

    # ── Agentic loop ──────────────────────────────────────────────────────────
    for step in range(max_steps):
        try:
            resp = client.beta.messages.create(
                model=OPERATOR_MODEL,
                max_tokens=4096,
                system=(
                    "You are a Windows desktop operator. You see and control the screen to complete tasks. "
                    "Use screenshots frequently to verify that your actions worked before proceeding. "
                    "When the task is complete, write a brief summary in 1-2 conversational sentences. "
                    "No markdown. No bullet points. The text will be spoken aloud."
                ),
                tools=[computer_tool],
                messages=messages,
                betas=[OPERATOR_BETA],
            )
        except anthropic.BadRequestError as e:
            # Model might not support computer-use beta — surface a clear message
            return f"Computer use is not supported by the configured model: {e}"
        except Exception as e:
            return f"Operator error on step {step + 1}: {e}"

        # ── Done ─────────────────────────────────────────────────────────────
        if resp.stop_reason == "end_turn":
            for block in resp.content:
                if hasattr(block, "text") and block.text.strip():
                    summary = block.text.strip()
                    _log(f"[operator] done: {summary[:80]}")
                    return summary
            return "Task completed."

        # ── Collect tool-use actions ──────────────────────────────────────────
        tool_blocks = [b for b in resp.content if b.type == "tool_use"]
        if not tool_blocks:
            # Claude stopped without end_turn and without tool calls — unusual
            for block in resp.content:
                if hasattr(block, "text") and block.text.strip():
                    return block.text.strip()
            break

        messages.append({"role": "assistant", "content": resp.content})

        tool_results = []
        for tb in tool_blocks:
            action = tb.input
            action_type = action.get("type", "")

            if action_type == "screenshot":
                # Take a fresh screenshot
                time.sleep(STEP_DELAY)
                b64, screen_w, screen_h = _screenshot()
                _log("[operator] screenshot taken")
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tb.id,
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                    ],
                })
            else:
                desc = _execute_action(action)
                _log(f"[operator] {desc}")
                time.sleep(STEP_DELAY)   # give UI time to react
                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": tb.id,
                    "content": [{"type": "text", "text": f"Done: {desc}"}],
                })

        messages.append({"role": "user", "content": tool_results})

    return "Task completed (reached step limit)."
