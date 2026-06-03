"""Jarvis system tools — executed when Claude calls a tool."""
import asyncio as _asyncio
import os
import subprocess
import webbrowser
import shutil
import json
import io
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# ─── Async broadcast bridge ───────────────────────────────────────────────────
# Set by main.py so tools can push WebSocket messages from executor threads.
_broadcast_fn: Optional[Callable]   = None
_event_loop:   Optional[Any]        = None

def _init_async(loop, broadcast_fn) -> None:
    """Called from main._run_pipeline() to wire up WebSocket broadcast."""
    global _broadcast_fn, _event_loop
    _broadcast_fn = broadcast_fn
    _event_loop   = loop

def _async_broadcast(msg: dict) -> None:
    """Fire-and-forget async broadcast from a sync (thread) context."""
    if _broadcast_fn and _event_loop and _event_loop.is_running():
        _asyncio.run_coroutine_threadsafe(_broadcast_fn(msg), _event_loop)

import psutil
import pyautogui
import pyperclip
from PIL import ImageGrab, Image

pyautogui.FAILSAFE = False  # don't raise exception if mouse hits corner

try:
    import win32gui
    import win32con
    import win32api
    _HAS_WIN32 = True
except ImportError:
    _HAS_WIN32 = False

try:
    import pygetwindow as gw
    _HAS_GW = True
except ImportError:
    _HAS_GW = False

# ─── App name → executable mapping (Windows) ────────────────────────────────
_APP_MAP: Dict[str, str] = {
    "notepad":        "notepad.exe",
    "calculator":     "calc.exe",
    "paint":          "mspaint.exe",
    "explorer":       "explorer.exe",
    "task manager":   "taskmgr.exe",
    "cmd":            "cmd.exe",
    "command prompt": "cmd.exe",
    "powershell":     "powershell.exe",
    "control panel":  "control.exe",
    "settings":       "ms-settings:",
    "calendar":       "outlookcal:",
    "mail":           "outlookmail:",
    "spotify":        os.path.join(os.environ.get("APPDATA", ""), "Spotify", "Spotify.exe"),
    "discord":        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Discord", "Update.exe"),
    "steam":          r"C:\Program Files (x86)\Steam\steam.exe",
    "chrome":         r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "firefox":        r"C:\Program Files\Mozilla Firefox\firefox.exe",
    "edge":           r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "vscode":         os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Microsoft VS Code", "Code.exe"),
    "word":           r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
    "excel":          r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
    "powerpoint":     r"C:\Program Files\Microsoft Office\root\Office16\POWERPNT.EXE",
    "teams":          os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Teams", "Update.exe"),
    "obs":            r"C:\Program Files\obs-studio\bin\64bit\obs64.exe",
    "vlc":            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
}

# URL shortcuts
_URL_MAP: Dict[str, str] = {
    "youtube":  "https://youtube.com",
    "gmail":    "https://mail.google.com",
    "github":   "https://github.com",
    "google":   "https://google.com",
    "netflix":  "https://netflix.com",
    "reddit":   "https://reddit.com",
    "twitter":  "https://twitter.com",
    "x":        "https://x.com",
    "linkedin": "https://linkedin.com",
    "chatgpt":  "https://chatgpt.com",
    "claude":   "https://claude.ai",
    "maps":     "https://maps.google.com",
    "drive":    "https://drive.google.com",
}

# ─── Tool implementations ────────────────────────────────────────────────────

def open_application(app_name: str) -> str:
    name = app_name.lower().strip()

    if name in _URL_MAP:
        webbrowser.open(_URL_MAP[name])
        return f"Opened {app_name} in browser."

    # Try known exe map
    exe = _APP_MAP.get(name)
    if exe:
        if exe.endswith(":"):           # ms-settings: or similar URI
            os.startfile(exe)
            return f"Opened {app_name}."
        if os.path.isfile(exe):
            if name == "discord":
                subprocess.Popen([exe, "--processStart", "Discord.exe"])
            elif name == "teams":
                subprocess.Popen([exe, "--processStart", "Teams.exe"])
            else:
                subprocess.Popen([exe])
            return f"Opened {app_name}."
        # File not found at known path — fall through to shell

    # Try PATH lookup
    found = shutil.which(name) or shutil.which(f"{name}.exe")
    if found:
        subprocess.Popen([found])
        return f"Opened {app_name}."

    # Last resort: Windows shell "start" command
    try:
        subprocess.Popen(["cmd", "/c", "start", name], shell=False)
        return f"Launched {app_name} via shell."
    except Exception as e:
        return f"Could not open '{app_name}': {e}"


def open_url(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url} in default browser."


def search_web(query: str) -> str:
    try:
        try:
            from ddgs import DDGS          # new package name
        except ImportError:
            from duckduckgo_search import DDGS  # fallback to old name
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            webbrowser.open(f"https://duckduckgo.com/?q={query.replace(' ', '+')}")
            return f"No instant results found. Opened browser search for '{query}'."
        lines = [f"Search results for '{query}':"]
        for r in results:
            lines.append(f"• {r['title']}: {r['body'][:120]}...")
        return "\n".join(lines)
    except Exception:
        webbrowser.open(f"https://duckduckgo.com/?q={query.replace(' ', '+')}")
        return f"Opened browser search for '{query}'."


def get_time() -> str:
    now = datetime.now()
    return now.strftime("Today is %A, %B %d %Y. The time is %I:%M %p.")


def get_system_info() -> str:
    cpu = psutil.cpu_percent(interval=0.5)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return (
        f"CPU: {cpu:.1f}% | "
        f"RAM: {ram.used / 1e9:.1f} GB used of {ram.total / 1e9:.1f} GB ({ram.percent}%) | "
        f"Disk C: {disk.used / 1e9:.1f} GB used of {disk.total / 1e9:.1f} GB ({disk.percent}%)"
    )


def take_screenshot(save_path: str = "") -> str:
    img = ImageGrab.grab()
    if not save_path:
        desktop = Path.home() / "Desktop"
        save_path = str(desktop / f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
    img.save(save_path)
    return f"Screenshot saved to {save_path}"


def list_directory(path: str = "") -> str:
    if not path:
        path = str(Path.home() / "Desktop")
    try:
        entries = list(Path(path).iterdir())
        dirs = sorted([e.name for e in entries if e.is_dir()])
        files = sorted([e.name for e in entries if e.is_file()])
        lines = [f"Contents of {path}:"]
        for d in dirs:
            lines.append(f"  [DIR]  {d}")
        for f in files:
            lines.append(f"  [FILE] {f}")
        return "\n".join(lines) if len(lines) > 1 else f"{path} is empty."
    except Exception as e:
        return f"Error listing directory: {e}"


def create_file(path: str, content: str) -> str:
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path(__file__).parent.parent / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"File written: {p} ({len(content)} chars)"
    except Exception as e:
        return f"Error creating file: {e}"


def read_file(path: str) -> str:
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path(__file__).parent.parent / path   # resolve relative to jarvis root
        text = p.read_text(encoding="utf-8")
        if len(text) > 24000:
            text = text[:24000] + "\n...[truncated — file larger than 24 000 chars]"
        return text
    except Exception as e:
        return f"Error reading file: {e}"


def type_text(text: str) -> str:
    pyautogui.sleep(0.5)
    pyautogui.write(text, interval=0.03)
    return f"Typed: {text[:50]}{'...' if len(text) > 50 else ''}"


def press_keys(keys: str) -> str:
    key_list = [k.strip() for k in keys.replace("+", " ").split()]
    pyautogui.hotkey(*key_list)
    return f"Pressed: {keys}"


def set_volume(level: int) -> str:
    level = max(0, min(100, level))
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        volume.SetMasterVolumeLevelScalar(level / 100.0, None)
        return f"Volume set to {level}%."
    except Exception as e:
        # Fallback: use nircmd-style via PowerShell
        try:
            script = f"(New-Object -ComObject WScript.Shell).SendKeys([char]174 * {(100-level)//2})"
            subprocess.run(["powershell", "-Command", script], capture_output=True)
            return f"Volume adjusted to approximately {level}%."
        except Exception:
            return f"Could not set volume: {e}"


def get_clipboard() -> str:
    text = pyperclip.paste()
    if not text:
        return "Clipboard is empty."
    if len(text) > 500:
        text = text[:500] + "...[truncated]"
    return f"Clipboard: {text}"


def set_clipboard(text: str) -> str:
    pyperclip.copy(text)
    return f"Copied to clipboard: {text[:80]}{'...' if len(text) > 80 else ''}"


# ─── Window management (win32gui — inspired by github.com/Blazehue/J.A.R.V.I.S) ─

def _require_win32():
    if not _HAS_WIN32:
        return "pywin32 not installed — run: pip install pywin32"
    return None

def _find_hwnd(title: str):
    """Return (hwnd, window_title) for the best partial-title match, or (None, err)."""
    err = _require_win32()
    if err:
        return None, None, err
    title_lower = title.lower()
    found = []
    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t and title_lower in t.lower():
                found.append((hwnd, t))
    win32gui.EnumWindows(_cb, None)
    if not found:
        all_titles = []
        def _all(hwnd, _):
            t = win32gui.GetWindowText(hwnd)
            if t and win32gui.IsWindowVisible(hwnd):
                all_titles.append(t)
        win32gui.EnumWindows(_all, None)
        return None, None, f"No window matching '{title}'. Open: {all_titles[:10]}"
    hwnd, wtitle = found[0]
    return hwnd, wtitle, None


def list_open_windows() -> str:
    err = _require_win32()
    if err:
        # Fallback via PowerShell
        r = subprocess.run(
            'powershell -command "Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object -ExpandProperty MainWindowTitle"',
            shell=True, capture_output=True, text=True)
        titles = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        return ("Open windows:\n" + "\n".join(f"• {t}" for t in titles)) if titles else "No windows found."
    titles = []
    def _cb(hwnd, _):
        t = win32gui.GetWindowText(hwnd)
        if t and win32gui.IsWindowVisible(hwnd):
            titles.append(t)
    win32gui.EnumWindows(_cb, None)
    return ("Open windows:\n" + "\n".join(f"• {t}" for t in titles)) if titles else "No windows found."


def get_active_window_info() -> str:
    """Return title and size of the currently focused window."""
    err = _require_win32()
    if err:
        return err
    hwnd = win32gui.GetForegroundWindow()
    title = win32gui.GetWindowText(hwnd)
    rect  = win32gui.GetWindowRect(hwnd)
    w, h  = rect[2] - rect[0], rect[3] - rect[1]
    return f"Active window: '{title}' — {w}×{h} at ({rect[0]}, {rect[1]})"


def focus_window(title: str) -> str:
    hwnd, wtitle, err = _find_hwnd(title)
    if err:
        return err
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return f"Brought '{wtitle}' to the front."
    except Exception as e:
        return f"Could not focus '{wtitle}': {e}"


def maximize_window(title: str = "") -> str:
    if title:
        hwnd, wtitle, err = _find_hwnd(title)
        if err:
            return err
    else:
        err = _require_win32()
        if err: return err
        hwnd   = win32gui.GetForegroundWindow()
        wtitle = win32gui.GetWindowText(hwnd)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        return f"Maximized '{wtitle}'."
    except Exception as e:
        return f"Error: {e}"


def minimize_window(title: str = "") -> str:
    if title:
        hwnd, wtitle, err = _find_hwnd(title)
        if err:
            return err
    else:
        err = _require_win32()
        if err: return err
        hwnd   = win32gui.GetForegroundWindow()
        wtitle = win32gui.GetWindowText(hwnd)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        return f"Minimized '{wtitle}'."
    except Exception as e:
        return f"Error: {e}"


def restore_window(title: str = "") -> str:
    if title:
        hwnd, wtitle, err = _find_hwnd(title)
        if err:
            return err
    else:
        err = _require_win32()
        if err: return err
        hwnd   = win32gui.GetForegroundWindow()
        wtitle = win32gui.GetWindowText(hwnd)
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        return f"Restored '{wtitle}'."
    except Exception as e:
        return f"Error: {e}"


def resize_window(title: str, width: int, height: int) -> str:
    hwnd, wtitle, err = _find_hwnd(title)
    if err:
        return err
    try:
        rect = win32gui.GetWindowRect(hwnd)
        win32gui.MoveWindow(hwnd, rect[0], rect[1], width, height, True)
        return f"Resized '{wtitle}' to {width}×{height}."
    except Exception as e:
        return f"Error: {e}"


def move_window(title: str, x: int, y: int) -> str:
    hwnd, wtitle, err = _find_hwnd(title)
    if err:
        return err
    try:
        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
        return f"Moved '{wtitle}' to ({x}, {y})."
    except Exception as e:
        return f"Error: {e}"


def snap_window(title: str, position: str) -> str:
    """Snap using real screen metrics (left/right half, top/bottom, center, maximize, minimize)."""
    err = _require_win32()
    if err: return err
    if title:
        hwnd, wtitle, err = _find_hwnd(title)
        if err:
            return err
    else:
        hwnd   = win32gui.GetForegroundWindow()
        wtitle = win32gui.GetWindowText(hwnd)
    try:
        sw = win32api.GetSystemMetrics(0)   # screen width
        sh = win32api.GetSystemMetrics(1)   # screen height
        pos = position.lower().strip()
        layouts = {
            "left":        (0,       0,       sw//2,  sh),
            "right":       (sw//2,   0,       sw//2,  sh),
            "top":         (0,       0,       sw,     sh//2),
            "bottom":      (0,       sh//2,   sw,     sh//2),
            "top-left":    (0,       0,       sw//2,  sh//2),
            "top-right":   (sw//2,   0,       sw//2,  sh//2),
            "bottom-left": (0,       sh//2,   sw//2,  sh//2),
            "bottom-right":(sw//2,   sh//2,   sw//2,  sh//2),
            "center":      (sw//4,   sh//8,   sw//2,  3*sh//4),
            "maximize":    (0,       0,       sw,     sh),
        }
        if pos == "minimize":
            win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
            return f"Minimized '{wtitle}'."
        if pos not in layouts:
            return f"Unknown position '{position}'. Options: {list(layouts.keys())} or minimize."
        x, y, w, h = layouts[pos]
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
        return f"Snapped '{wtitle}' to {position} ({w}×{h})."
    except Exception as e:
        return f"Error: {e}"


def center_window(title: str = "") -> str:
    err = _require_win32()
    if err: return err
    if title:
        hwnd, wtitle, err = _find_hwnd(title)
        if err:
            return err
    else:
        hwnd   = win32gui.GetForegroundWindow()
        wtitle = win32gui.GetWindowText(hwnd)
    try:
        rect = win32gui.GetWindowRect(hwnd)
        w, h = rect[2] - rect[0], rect[3] - rect[1]
        sw, sh = win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)
        x, y = (sw - w) // 2, (sh - h) // 2
        win32gui.MoveWindow(hwnd, x, y, w, h, True)
        return f"Centered '{wtitle}' on screen."
    except Exception as e:
        return f"Error: {e}"


def run_command(command: str, timeout: int = 15) -> str:
    """Run a shell command and return its output (like Claude Code does)."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(Path.home()),
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        parts = []
        if out:
            parts.append(out[:2000])
        if err:
            parts.append(f"[stderr] {err[:500]}")
        if result.returncode != 0:
            parts.append(f"[exit code {result.returncode}]")
        return "\n".join(parts) if parts else "(command ran, no output)"
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except Exception as e:
        return f"Command error: {e}"


def run_python(code: str) -> str:
    """Execute Python code and return stdout/result."""
    import sys
    from io import StringIO
    buf = StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        exec(compile(code, "<jarvis>", "exec"), {})  # noqa: S102
        output = buf.getvalue().strip()
        return output if output else "(ran successfully, no output)"
    except Exception as e:
        return f"Python error: {e}"
    finally:
        sys.stdout = old_stdout


def run_powershell(script: str, timeout: int = 30) -> str:
    """
    Execute a PowerShell script and return its output.
    More powerful than run_command — supports PS cmdlets, .NET APIs,
    registry access (HKLM:/HKCU:), service control, WMI, etc.
    If Jarvis was launched as Administrator, this runs with full admin rights.
    """
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        parts = []
        if out:
            parts.append(out[:3000])
        if err:
            parts.append(f"[stderr] {err[:500]}")
        if result.returncode != 0:
            parts.append(f"[exit {result.returncode}]")
        return "\n".join(parts) if parts else "(script ran, no output)"
    except subprocess.TimeoutExpired:
        return f"PowerShell timed out after {timeout}s"
    except Exception as e:
        return f"PowerShell error: {e}"


def delete_file(path: str) -> str:
    """Delete a file or an empty/non-empty directory."""
    try:
        p = Path(path)
        if not p.exists():
            return f"Not found: {path}"
        if p.is_dir():
            shutil.rmtree(p)
            return f"Deleted directory: {path}"
        else:
            p.unlink()
            return f"Deleted file: {path}"
    except Exception as e:
        return f"Delete error: {e}"


def move_file(source: str, destination: str) -> str:
    """Move or rename a file or directory."""
    try:
        shutil.move(source, destination)
        return f"Moved {source} → {destination}"
    except Exception as e:
        return f"Move error: {e}"


def copy_file(source: str, destination: str) -> str:
    """Copy a file or directory tree to destination."""
    try:
        src = Path(source)
        if src.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        return f"Copied {source} → {destination}"
    except Exception as e:
        return f"Copy error: {e}"


# ─── Mouse & keyboard control ─────────────────────────────────────────────────

def click_at(x: int, y: int, button: str = "left") -> str:
    """Click the mouse at specific screen coordinates."""
    try:
        btn = button.lower()
        if btn not in ("left", "right", "middle"):
            btn = "left"
        pyautogui.click(x, y, button=btn)
        return f"Clicked {btn} at ({x}, {y})."
    except Exception as e:
        return f"Click error: {e}"


def double_click_at(x: int, y: int) -> str:
    """Double-click at specific screen coordinates."""
    try:
        pyautogui.doubleClick(x, y)
        return f"Double-clicked at ({x}, {y})."
    except Exception as e:
        return f"Double-click error: {e}"


def right_click_at(x: int, y: int) -> str:
    """Right-click at specific screen coordinates."""
    try:
        pyautogui.rightClick(x, y)
        return f"Right-clicked at ({x}, {y})."
    except Exception as e:
        return f"Right-click error: {e}"


def move_mouse_to(x: int, y: int) -> str:
    """Move the mouse cursor to specific screen coordinates without clicking."""
    try:
        pyautogui.moveTo(x, y, duration=0.2)
        return f"Moved mouse to ({x}, {y})."
    except Exception as e:
        return f"Move error: {e}"


def scroll_at(x: int, y: int, direction: str = "down", amount: int = 3) -> str:
    """Scroll the mouse wheel at specific coordinates."""
    try:
        pyautogui.moveTo(x, y, duration=0.1)
        clicks = -abs(amount) if direction.lower() == "down" else abs(amount)
        pyautogui.scroll(clicks)
        return f"Scrolled {direction} {abs(amount)} steps at ({x}, {y})."
    except Exception as e:
        return f"Scroll error: {e}"


def drag_mouse(from_x: int, from_y: int, to_x: int, to_y: int, duration: float = 0.5) -> str:
    """Click and drag the mouse from one position to another."""
    try:
        pyautogui.moveTo(from_x, from_y, duration=0.1)
        pyautogui.dragTo(to_x, to_y, duration=duration, button="left")
        return f"Dragged from ({from_x}, {from_y}) to ({to_x}, {to_y})."
    except Exception as e:
        return f"Drag error: {e}"


def get_mouse_position() -> str:
    """Get the current mouse cursor position and screen dimensions."""
    try:
        pos = pyautogui.position()
        size = pyautogui.size()
        return (f"Mouse at ({pos.x}, {pos.y}). "
                f"Screen: {size.width}×{size.height} px.")
    except Exception as e:
        return f"Position error: {e}"


# ─── Screen vision ────────────────────────────────────────────────────────────

def analyze_screen(region: dict = None) -> list:
    """
    Take a screenshot of the screen (or a region) and return it as image
    content that Claude can visually analyze.
    Returns a list of content blocks (text + image) for the tool result.
    """
    import base64
    from io import BytesIO

    try:
        if region:
            bbox = (
                int(region.get("x", 0)),
                int(region.get("y", 0)),
                int(region.get("x", 0)) + int(region.get("width", 1920)),
                int(region.get("y", 0)) + int(region.get("height", 1080)),
            )
            img = ImageGrab.grab(bbox=bbox)
        else:
            img = ImageGrab.grab()

        # Resize if very large (keep readable for Claude, reduce token cost)
        max_w = 1280
        if img.width > max_w:
            ratio  = max_w / img.width
            new_h  = int(img.height * ratio)
            img    = img.resize((max_w, new_h), Image.LANCZOS)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=82)
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        return [
            {"type": "text", "text": f"Current screen ({img.width}×{img.height}):"},
            {"type": "image", "source": {
                "type":       "base64",
                "media_type": "image/jpeg",
                "data":       b64,
            }},
        ]
    except Exception as e:
        return [{"type": "text", "text": f"Screen capture failed: {e}"}]


# ─── Self-modification tools ─────────────────────────────────────────────────

def edit_file(path: str, search: str, replace: str) -> str:
    """
    Replace the first occurrence of `search` with `replace` in the file at `path`.
    More surgical than rewriting the whole file — use for targeted code/CSS/HTML edits.
    Supports paths relative to the jarvis root (e.g. 'frontend/styles.css').
    """
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path(__file__).parent.parent / path
        content = p.read_text(encoding="utf-8")
        if search not in content:
            return (f"Text not found in {p.name}. "
                    f"Tip: use read_file first to get the exact text, including whitespace.")
        new_content = content.replace(search, replace, 1)
        p.write_text(new_content, encoding="utf-8")
        return f"Edited {p.name} successfully."
    except Exception as e:
        return f"edit_file error: {e}"


def reload_ui() -> str:
    """
    Tell the Electron renderer to reload itself.
    Call this after editing frontend/styles.css, index.html, or renderer.js
    so changes take effect immediately without restarting.
    """
    _async_broadcast({"type": "reload"})
    return "UI reload signal sent — frontend reloading now."


def restart_backend() -> str:
    """
    Tell Electron to kill and restart the Python backend process.
    Call this after editing backend Python files (main.py, tools.py, operator_agent.py)
    so code changes take effect.  The frontend reconnects automatically in ~3 seconds.
    """
    _async_broadcast({"type": "restart_backend"})
    return "Backend restart signal sent — reconnecting in ~3 seconds."


def clear_chat_log() -> str:
    """
    Clear the chat log shown in the Jarvis UI right panel.
    Call this when the user asks to clear the chat, wipe the log, or start fresh.
    """
    _async_broadcast({"type": "clear_log"})
    return "Chat log cleared."


# ─── OS Operator Agent ───────────────────────────────────────────────────────

def run_os_task(task: str) -> str:
    """
    Delegate a complex multi-step computer task to the autonomous OS operator agent.
    The agent sees the screen, takes actions (click, type, scroll), and loops until done.
    Returns a plain-English summary of what was accomplished.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "Cannot run operator task: ANTHROPIC_API_KEY not set in .env"

    def _progress(msg: str) -> None:
        _async_broadcast({"type": "operator_status", "text": msg})

    try:
        from operator_agent import run_operator_task
        return run_operator_task(task, api_key, progress_cb=_progress)
    except ImportError as e:
        return f"operator_agent module not found: {e}"
    except Exception as e:
        return f"Operator agent error: {e}"


# ─── Browser automation (Playwright + persistent Chrome profile) ──────────────
#
# Uses a Jarvis-owned Chrome profile stored at jarvis_browser_profile/ so all
# logins (Google, GitHub, etc.) persist between sessions.  The browser window
# is visible so Kalo can see what Jarvis is doing.
#
# Claude uses these tools iteratively:
#   1. browser_navigate  → go to a page
#   2. browser_snapshot  → understand what's on it (text + interactive elements)
#   3. browser_click / browser_type / browser_press_key  → act on it
#   4. browser_screenshot  → visual verification when needed
#   5. browser_close  → tidy up when done

import threading as _threading

_CHROME_EXE      = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
_BROWSER_PROFILE = Path(__file__).parent.parent / "jarvis_browser_profile"
_browser_lock    = _threading.Lock()

# module-level singletons (survive across turns)
_pw_instance  = None
_browser_ctx  = None
_browser_page = None


def _get_page():
    """Return the active Playwright page, creating the browser if needed."""
    global _pw_instance, _browser_ctx, _browser_page
    with _browser_lock:
        _BROWSER_PROFILE.mkdir(exist_ok=True)
        if _browser_ctx is None:
            from playwright.sync_api import sync_playwright
            _pw_instance = sync_playwright().start()
            _browser_ctx = _pw_instance.chromium.launch_persistent_context(
                user_data_dir=str(_BROWSER_PROFILE),
                executable_path=_CHROME_EXE,
                headless=False,
                no_viewport=True,
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-infobars",
                ],
            )
            _browser_page = (_browser_ctx.pages[0]
                             if _browser_ctx.pages
                             else _browser_ctx.new_page())
        elif _browser_page is None or _browser_page.is_closed():
            _browser_page = (_browser_ctx.pages[0]
                             if _browser_ctx.pages
                             else _browser_ctx.new_page())
        return _browser_page


def browser_navigate(url: str) -> str:
    """Navigate the browser to a URL and return the page title."""
    page = _get_page()
    page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    page.wait_for_timeout(600)   # brief settle
    return f"Navigated to: {page.title()} — {page.url}"


def browser_snapshot() -> str:
    """
    Return a compact view of the current page: title, URL, visible text,
    and a list of interactive elements (buttons, inputs, links, selects).
    Use this to understand the page before clicking or typing.
    """
    page = _get_page()

    # Visible text (stripped, deduplicated, capped at 4 000 chars)
    text: str = page.evaluate(r"""() => {
        const skip = new Set(['SCRIPT','STYLE','NOSCRIPT','SVG','HEAD']);
        const seen = new Set();
        const lines = [];
        const walker = document.createTreeWalker(
            document.body, NodeFilter.SHOW_TEXT, {
                acceptNode: n => {
                    const p = n.parentElement;
                    if (!p) return NodeFilter.FILTER_REJECT;
                    if (skip.has(p.tagName)) return NodeFilter.FILTER_REJECT;
                    const s = window.getComputedStyle(p);
                    if (s.display === 'none' || s.visibility === 'hidden') return NodeFilter.FILTER_REJECT;
                    return NodeFilter.FILTER_ACCEPT;
                }
            });
        let node;
        while ((node = walker.nextNode())) {
            const t = node.textContent.trim();
            if (t && !seen.has(t)) { seen.add(t); lines.push(t); }
        }
        return lines.join('\n');
    }""")[:4_000]

    # Interactive elements
    elements: list = page.evaluate(r"""() => {
        const items = [];
        document.querySelectorAll('a[href], button, input, select, textarea, [role="button"], [role="link"]')
            .forEach(el => {
                if (items.length >= 40) return;
                const tag  = el.tagName.toLowerCase();
                const type = el.type || '';
                const label = (el.getAttribute('aria-label') || el.placeholder ||
                               el.name || el.id || el.textContent?.trim() || el.href || '').slice(0, 80);
                if (label) items.push(`[${tag}${type ? ':'+type : ''}] ${label}`);
            });
        return items;
    }""")

    out  = f"URL: {page.url}\nTitle: {page.title()}\n\n"
    out += "── Visible text ──\n" + (text or "(empty)") + "\n\n"
    out += "── Interactive elements ──\n" + "\n".join(elements or ["(none found)"])
    return out


def browser_click(target: str) -> str:
    """
    Click a page element.  `target` can be:
    - visible text of the element  (e.g. 'Create API key')
    - an aria-label                (e.g. 'Close dialog')
    - a CSS selector               (e.g. '#submit-btn')
    """
    page = _get_page()
    strategies = [
        lambda: page.get_by_text(target, exact=True).first.click(timeout=5_000),
        lambda: page.get_by_text(target, exact=False).first.click(timeout=5_000),
        lambda: page.get_by_role("button", name=target).first.click(timeout=5_000),
        lambda: page.get_by_label(target).first.click(timeout=5_000),
        lambda: page.locator(target).first.click(timeout=5_000),
    ]
    for fn in strategies:
        try:
            fn()
            page.wait_for_timeout(400)
            return f"Clicked: '{target}'"
        except Exception:
            pass
    return f"Could not find element to click: '{target}'"


def browser_type(selector: str, text: str, clear_first: bool = True) -> str:
    """
    Type text into a form field.  `selector` can be:
    - a placeholder string   (e.g. 'Enter email')
    - an aria-label          (e.g. 'Password')
    - a CSS selector         (e.g. 'input[name=email]')
    Set clear_first=True (default) to wipe any existing value first.
    """
    page = _get_page()
    strategies = [
        lambda: page.get_by_placeholder(selector).first,
        lambda: page.get_by_label(selector).first,
        lambda: page.locator(selector).first,
    ]
    for get_loc in strategies:
        try:
            loc = get_loc()
            loc.wait_for(timeout=4_000)
            if clear_first:
                loc.fill("")
            loc.type(text, delay=30)
            return f"Typed into '{selector}'"
        except Exception:
            pass
    return f"Could not find input field: '{selector}'"


def browser_press_key(key: str) -> str:
    """
    Press a keyboard key in the browser.  Examples: 'Enter', 'Tab', 'Escape',
    'Control+a', 'Control+c'.
    """
    page = _get_page()
    page.keyboard.press(key)
    page.wait_for_timeout(300)
    return f"Pressed key: {key}"


def browser_screenshot() -> list:
    """
    Take a screenshot of the current browser page.
    Returns an image block so Claude can visually verify the page state.
    Use this to confirm a step worked or to read content that snapshot() misses.
    """
    import base64
    page = _get_page()
    png  = page.screenshot(full_page=False)
    b64  = base64.b64encode(png).decode()
    return [
        {"type": "text",  "text": f"Browser screenshot — {page.url}"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
    ]


def browser_close() -> str:
    """Close the Playwright browser and free resources."""
    global _pw_instance, _browser_ctx, _browser_page
    with _browser_lock:
        if _browser_ctx:
            try:
                _browser_ctx.close()
            except Exception:
                pass
            _browser_ctx  = None
            _browser_page = None
        if _pw_instance:
            try:
                _pw_instance.stop()
            except Exception:
                pass
            _pw_instance = None
    return "Browser closed."


# ─── Tool registry ────────────────────────────────────────────────────────────

def execute_tool(name: str, inputs: Dict[str, Any]):
    """Execute a tool by name. Returns str for most tools, list for vision tools."""
    registry = {
        "open_application": lambda i: open_application(i["app_name"]),
        "open_url":         lambda i: open_url(i["url"]),
        "search_web":       lambda i: search_web(i["query"]),
        "get_time":         lambda i: get_time(),
        "get_system_info":  lambda i: get_system_info(),
        "take_screenshot":  lambda i: take_screenshot(i.get("save_path", "")),
        "list_directory":   lambda i: list_directory(i.get("path", "")),
        "create_file":      lambda i: create_file(i["path"], i["content"]),
        "read_file":        lambda i: read_file(i["path"]),
        "type_text":        lambda i: type_text(i["text"]),
        "press_keys":       lambda i: press_keys(i["keys"]),
        "set_volume":       lambda i: set_volume(int(i["level"])),
        "get_clipboard":    lambda i: get_clipboard(),
        "set_clipboard":    lambda i: set_clipboard(i["text"]),
        "run_command":      lambda i: run_command(i["command"], int(i.get("timeout", 15))),
        "run_python":       lambda i: run_python(i["code"]),
        "run_powershell":   lambda i: run_powershell(i["script"], int(i.get("timeout", 30))),
        "delete_file":      lambda i: delete_file(i["path"]),
        "move_file":        lambda i: move_file(i["source"], i["destination"]),
        "copy_file":        lambda i: copy_file(i["source"], i["destination"]),
        # Window management
        "list_open_windows": lambda i: list_open_windows(),
        "get_active_window": lambda i: get_active_window_info(),
        "focus_window":      lambda i: focus_window(i["title"]),
        "maximize_window":   lambda i: maximize_window(i.get("title", "")),
        "minimize_window":   lambda i: minimize_window(i.get("title", "")),
        "restore_window":    lambda i: restore_window(i.get("title", "")),
        "resize_window":     lambda i: resize_window(i["title"], int(i["width"]), int(i["height"])),
        "move_window":       lambda i: move_window(i["title"], int(i["x"]), int(i["y"])),
        "snap_window":       lambda i: snap_window(i.get("title", ""), i["position"]),
        "center_window":     lambda i: center_window(i.get("title", "")),
        # Mouse & keyboard
        "click_at":         lambda i: click_at(int(i["x"]), int(i["y"]), i.get("button", "left")),
        "double_click_at":  lambda i: double_click_at(int(i["x"]), int(i["y"])),
        "right_click_at":   lambda i: right_click_at(int(i["x"]), int(i["y"])),
        "move_mouse_to":    lambda i: move_mouse_to(int(i["x"]), int(i["y"])),
        "scroll_at":        lambda i: scroll_at(int(i["x"]), int(i["y"]), i.get("direction", "down"), int(i.get("amount", 3))),
        "drag_mouse":       lambda i: drag_mouse(int(i["from_x"]), int(i["from_y"]), int(i["to_x"]), int(i["to_y"]), float(i.get("duration", 0.5))),
        "get_mouse_position": lambda i: get_mouse_position(),
        # Screen vision
        "analyze_screen":   lambda i: analyze_screen(i.get("region")),
        # Self-modification
        "edit_file":        lambda i: edit_file(i["path"], i["search"], i["replace"]),
        "reload_ui":        lambda i: reload_ui(),
        "restart_backend":  lambda i: restart_backend(),
        "clear_chat_log":   lambda i: clear_chat_log(),
        # Operator agent
        "run_os_task":      lambda i: run_os_task(i["task"]),
        # Browser automation
        "browser_navigate":   lambda i: browser_navigate(i["url"]),
        "browser_snapshot":   lambda i: browser_snapshot(),
        "browser_click":      lambda i: browser_click(i["target"]),
        "browser_type":       lambda i: browser_type(i["selector"], i["text"], i.get("clear_first", True)),
        "browser_press_key":  lambda i: browser_press_key(i["key"]),
        "browser_screenshot": lambda i: browser_screenshot(),
        "browser_close":      lambda i: browser_close(),
    }
    handler = registry.get(name)
    if not handler:
        return f"Unknown tool: {name}"
    try:
        return handler(inputs)
    except Exception as e:
        return f"Tool '{name}' error: {e}"


TOOLS_DEFINITION = [
    {
        "name": "open_application",
        "description": "Open an application on the user's Windows computer by name. Handles common apps (Chrome, Notepad, Calculator, Spotify, Discord, VSCode, etc.) and URL shortcuts (YouTube, Gmail, GitHub, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "app_name": {"type": "string", "description": "Name of the app to open, e.g. 'chrome', 'notepad', 'spotify', 'youtube'"}
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "open_url",
        "description": "Open a specific URL in the default web browser.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to open, e.g. 'https://github.com'"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "search_web",
        "description": "Search the web using DuckDuckGo and return a summary of results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query string"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_time",
        "description": "Get the current date and time.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_system_info",
        "description": "Get CPU usage, RAM usage, and disk space information.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "take_screenshot",
        "description": "Take a screenshot of the current screen and save it.",
        "input_schema": {
            "type": "object",
            "properties": {
                "save_path": {"type": "string", "description": "Optional file path to save the screenshot. Defaults to Desktop."}
            }
        }
    },
    {
        "name": "list_directory",
        "description": "List files and folders in a directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path. Defaults to Desktop if not specified."}
            }
        }
    },
    {
        "name": "create_file",
        "description": "Create a text file at a given path with specified content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path to create"},
                "content": {"type": "string", "description": "Text content to write"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "read_file",
        "description": "Read the text content of a file.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute file path to read"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "type_text",
        "description": "Type text at the current cursor position (keyboard simulation).",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "press_keys",
        "description": "Press keyboard shortcuts like ctrl+c, win+d, alt+f4.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {"type": "string", "description": "Key combination, e.g. 'ctrl+c', 'win+d', 'ctrl+alt+t'"}
            },
            "required": ["keys"]
        }
    },
    {
        "name": "set_volume",
        "description": "Set the Windows system master volume.",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Volume level 0–100"}
            },
            "required": ["level"]
        }
    },
    {
        "name": "get_clipboard",
        "description": "Get the current text content of the clipboard.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "set_clipboard",
        "description": "Copy text to the clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to copy to clipboard"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "run_command",
        "description": "Run any Windows shell command (PowerShell/cmd) and return its output. Use this to install software, manage files, check processes, run scripts, query system info, or do anything a command prompt can do.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run, e.g. 'dir C:\\Users', 'tasklist | findstr chrome', 'pip install requests'"},
                "timeout": {"type": "integer", "description": "Max seconds to wait (default 15)"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "run_python",
        "description": "Execute a Python code snippet and return the output. Use for calculations, data processing, generating files, or any task that benefits from running Python.",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to execute"}
            },
            "required": ["code"]
        }
    },
    {
        "name": "run_powershell",
        "description": (
            "Execute a PowerShell script and return its output. "
            "Prefer this over run_command for anything that needs PowerShell-specific features: "
            "registry editing (Get/Set-ItemProperty HKLM:/HKCU:), service management (Start/Stop-Service), "
            "WMI queries (Get-WmiObject), scheduled tasks, firewall rules, user/group management, "
            "network configuration, environment variables, or multi-line scripts. "
            "If Jarvis was launched as Administrator, this runs with full admin rights."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "script":  {"type": "string",  "description": "PowerShell script to run"},
                "timeout": {"type": "integer", "description": "Max seconds to wait (default 30)"}
            },
            "required": ["script"]
        }
    },
    {
        "name": "delete_file",
        "description": "Delete a file or directory (including all its contents). Permanent — no recycle bin.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to the file or folder to delete"}
            },
            "required": ["path"]
        }
    },
    {
        "name": "move_file",
        "description": "Move or rename a file or directory.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source":      {"type": "string", "description": "Current path of the file/folder"},
                "destination": {"type": "string", "description": "New path or destination directory"}
            },
            "required": ["source", "destination"]
        }
    },
    {
        "name": "copy_file",
        "description": "Copy a file or directory tree to a new location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source":      {"type": "string", "description": "Path of the file/folder to copy"},
                "destination": {"type": "string", "description": "Destination path"}
            },
            "required": ["source", "destination"]
        }
    },
    {
        "name": "list_open_windows",
        "description": "List all currently open and visible windows by title. Call this first if unsure of a window's exact name.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_active_window",
        "description": "Get the title, size, and position of the currently focused window.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "focus_window",
        "description": "Bring a window to the front by partial title match (e.g. 'Chrome', 'Spotify').",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title"}
            },
            "required": ["title"]
        }
    },
    {
        "name": "maximize_window",
        "description": "Maximize a window to fill the screen. If title is omitted, acts on the currently active window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title, or omit for the active window"}
            }
        }
    },
    {
        "name": "minimize_window",
        "description": "Minimize a window to the taskbar. If title is omitted, acts on the currently active window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title, or omit for the active window"}
            }
        }
    },
    {
        "name": "restore_window",
        "description": "Restore a minimized/maximized window to its normal size. If title is omitted, acts on the active window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title, or omit for the active window"}
            }
        }
    },
    {
        "name": "snap_window",
        "description": "Snap a window to a screen region. Positions: left, right, top, bottom, top-left, top-right, bottom-left, bottom-right, center, maximize, minimize. If title is omitted, acts on the active window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":    {"type": "string", "description": "Partial window title, or omit for the active window"},
                "position": {"type": "string", "description": "left | right | top | bottom | top-left | top-right | bottom-left | bottom-right | center | maximize | minimize"}
            },
            "required": ["position"]
        }
    },
    {
        "name": "resize_window",
        "description": "Resize a window to specific pixel dimensions.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title":  {"type": "string", "description": "Partial window title"},
                "width":  {"type": "integer", "description": "Width in pixels"},
                "height": {"type": "integer", "description": "Height in pixels"}
            },
            "required": ["title", "width", "height"]
        }
    },
    {
        "name": "move_window",
        "description": "Move a window to specific screen coordinates (top-left corner).",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title"},
                "x":     {"type": "integer", "description": "Pixels from left edge"},
                "y":     {"type": "integer", "description": "Pixels from top edge"}
            },
            "required": ["title", "x", "y"]
        }
    },
    {
        "name": "center_window",
        "description": "Center a window on the screen without changing its size. If title is omitted, acts on the active window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Partial window title, or omit for the active window"}
            }
        }
    },
    # ── Mouse & keyboard ──────────────────────────────────────────────────────
    {
        "name": "click_at",
        "description": "Click the mouse at exact screen pixel coordinates. Use analyze_screen first to see the screen and find target coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x":      {"type": "integer", "description": "Horizontal pixel coordinate"},
                "y":      {"type": "integer", "description": "Vertical pixel coordinate"},
                "button": {"type": "string",  "description": "Mouse button: left | right | middle (default: left)"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "double_click_at",
        "description": "Double-click at exact screen coordinates (e.g. to open a file or select a word).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Horizontal pixel coordinate"},
                "y": {"type": "integer", "description": "Vertical pixel coordinate"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "right_click_at",
        "description": "Right-click at screen coordinates to open a context menu.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Horizontal pixel coordinate"},
                "y": {"type": "integer", "description": "Vertical pixel coordinate"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "move_mouse_to",
        "description": "Move the mouse cursor to a position without clicking (useful for hovering).",
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Horizontal pixel coordinate"},
                "y": {"type": "integer", "description": "Vertical pixel coordinate"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "scroll_at",
        "description": "Scroll the mouse wheel at specific coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "x":         {"type": "integer", "description": "Horizontal pixel coordinate"},
                "y":         {"type": "integer", "description": "Vertical pixel coordinate"},
                "direction": {"type": "string",  "description": "Scroll direction: up | down (default: down)"},
                "amount":    {"type": "integer", "description": "Number of scroll steps (default: 3)"}
            },
            "required": ["x", "y"]
        }
    },
    {
        "name": "drag_mouse",
        "description": "Click and drag the mouse from one screen position to another (for moving windows, sliders, selecting text, etc.).",
        "input_schema": {
            "type": "object",
            "properties": {
                "from_x":   {"type": "integer", "description": "Start X coordinate"},
                "from_y":   {"type": "integer", "description": "Start Y coordinate"},
                "to_x":     {"type": "integer", "description": "End X coordinate"},
                "to_y":     {"type": "integer", "description": "End Y coordinate"},
                "duration": {"type": "number",  "description": "Duration of drag in seconds (default: 0.5)"}
            },
            "required": ["from_x", "from_y", "to_x", "to_y"]
        }
    },
    {
        "name": "get_mouse_position",
        "description": "Get the current mouse cursor position and screen dimensions in pixels.",
        "input_schema": {"type": "object", "properties": {}}
    },
    # ── Screen vision ─────────────────────────────────────────────────────────
    {
        "name": "analyze_screen",
        "description": "Take a screenshot of the current screen and analyze it visually. Use this to see what is on screen, find buttons/text/UI elements, read on-screen content, or determine where to click. Optionally capture just a region.",
        "input_schema": {
            "type": "object",
            "properties": {
                "region": {
                    "type": "object",
                    "description": "Optional: capture a sub-region instead of full screen. Provide {x, y, width, height} in pixels.",
                    "properties": {
                        "x":      {"type": "integer"},
                        "y":      {"type": "integer"},
                        "width":  {"type": "integer"},
                        "height": {"type": "integer"}
                    }
                }
            }
        }
    },
    # ── Self-modification ─────────────────────────────────────────────────────
    {
        "name": "edit_file",
        "description": (
            "Replace the first occurrence of specific text inside a file. "
            "Use this for surgical edits — changing a CSS color, adding a UI element, tweaking a config value. "
            "You MUST read the file first to get the exact text to search for. "
            "After editing frontend files, call reload_ui so changes appear immediately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "Absolute file path to edit"},
                "search":  {"type": "string", "description": "Exact text to find (must match precisely, including whitespace)"},
                "replace": {"type": "string", "description": "New text to put in its place"},
            },
            "required": ["path", "search", "replace"]
        }
    },
    {
        "name": "reload_ui",
        "description": (
            "Reload the Jarvis frontend window immediately. "
            "Call this after editing frontend/styles.css, frontend/index.html, or frontend/renderer.js "
            "so the changes appear without restarting the whole app."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "restart_backend",
        "description": (
            "Kill and restart the Python backend process so that code changes take effect. "
            "Call this after editing backend/main.py, backend/tools.py, or backend/operator_agent.py. "
            "The Electron window stays open and the frontend reconnects automatically in ~3 seconds. "
            "Do NOT call this after frontend edits — use reload_ui instead."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "clear_chat_log",
        "description": (
            "Clear the chat log shown in the Jarvis UI. "
            "Call this when the user asks to clear the chat, wipe the conversation log, "
            "clear the screen, or start fresh."
        ),
        "input_schema": {"type": "object", "properties": {}}
    },
    # ── OS Operator Agent ─────────────────────────────────────────────────────
    {
        "name": "run_os_task",
        "description": (
            "Delegate a complex multi-step computer task to an autonomous OS operator agent. "
            "The agent sees the screen and loops: screenshot → decide → act (click/type/scroll) → screenshot → repeat, until the task is done. "
            "Use this for tasks that require navigating a UI or completing a workflow: "
            "filling out a web form, navigating to a specific page and clicking a button, "
            "interacting with an app's UI over multiple steps, composing and sending an email, "
            "booking something on a website, etc. "
            "Do NOT use this for simple single-step actions — prefer direct tools (click_at, open_url, etc.) for those."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": (
                        "Clear, specific description of the complete task to accomplish. "
                        "Examples: 'Open Google Calendar and create an event tomorrow at 3pm called Team Meeting', "
                        "'Go to gmail.com and compose an email to test@example.com with subject Hello and body Hi there', "
                        "'Open YouTube, search for lofi hip hop, and play the first video'"
                    )
                }
            },
            "required": ["task"]
        }
    },
    # ── Browser tools ────────────────────────────────────────────────────────
    {
        "name": "browser_navigate",
        "description": (
            "Open a URL in the Jarvis browser. The browser uses a persistent profile so "
            "any site the user has previously signed into (Google, GitHub, cloud consoles, etc.) "
            "will already be logged in. Use this as the first step of any web task."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Full URL to navigate to, e.g. 'https://console.anthropic.com/settings/keys'"}
            },
            "required": ["url"]
        }
    },
    {
        "name": "browser_snapshot",
        "description": (
            "Get a text overview of the current browser page: title, URL, visible text, "
            "and all interactive elements (buttons, inputs, links). "
            "Always call this after navigating to a new page to understand what's on it "
            "before clicking or typing. Much faster than browser_screenshot."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "browser_click",
        "description": (
            "Click an element on the current browser page. Try the element's visible text first, "
            "then its aria-label, then a CSS selector. "
            "Examples: 'Create new key', 'Sign in', '#submit-button', 'button.primary'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Visible text, aria-label, or CSS selector of the element to click"
                }
            },
            "required": ["target"]
        }
    },
    {
        "name": "browser_type",
        "description": (
            "Type text into a form field on the current page. "
            "Identify the field by its placeholder text, label, or CSS selector. "
            "Examples: 'Email address', 'Search', 'input[name=q]'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "Placeholder text, field label, or CSS selector identifying the input field"
                },
                "text": {
                    "type": "string",
                    "description": "Text to type into the field"
                },
                "clear_first": {
                    "type": "boolean",
                    "description": "Clear any existing value before typing (default: true)"
                }
            },
            "required": ["selector", "text"]
        }
    },
    {
        "name": "browser_press_key",
        "description": (
            "Press a keyboard key in the browser. Use this after typing to submit a form "
            "or navigate (Enter), move between fields (Tab), or dismiss popups (Escape). "
            "Also supports combos like 'Control+a', 'Control+c'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "description": "Key to press, e.g. 'Enter', 'Tab', 'Escape', 'Control+a'"
                }
            },
            "required": ["key"]
        }
    },
    {
        "name": "browser_screenshot",
        "description": (
            "Take a visual screenshot of the current browser page and return it as an image. "
            "Use this to visually verify a completed action, read a newly generated key or token, "
            "or understand page layout that the snapshot missed."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "browser_close",
        "description": "Close the Jarvis browser window when a web task is complete.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
]
