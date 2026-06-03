@echo off
setlocal enabledelayedexpansion
title Jarvis – Installer

echo.
echo  ============================================
echo   J A R V I S  –  Installation
echo  ============================================
echo.

:: ── Python check ───────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install from https://python.org
    pause & exit /b 1
)

:: ── Node check ─────────────────────────────────────────────────────────────
node --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Node.js not found. Install from https://nodejs.org
    pause & exit /b 1
)

:: ── Python deps ─────────────────────────────────────────────────────────────
echo [1/4] Installing Python dependencies...
pip install -r backend\requirements.txt
if errorlevel 1 (
    echo [WARN] Some packages may have failed – check output above.
)

:: PyAudio needs PortAudio on Windows.
:: pip ships pre-built wheels for common Python versions; if the above failed,
:: try the fallback:  pip install pipwin ^&^& pipwin install pyaudio
pip show pyaudio >nul 2>&1
if errorlevel 1 (
    echo [WARN] PyAudio not found – trying pipwin fallback...
    pip install pipwin --quiet
    pipwin install pyaudio
)

:: ── Node deps (Electron) ─────────────────────────────────────────────────────
echo.
echo [2/4] Installing Electron...
cd frontend
call npm install
cd ..
if errorlevel 1 (
    echo [ERROR] npm install failed.
    pause & exit /b 1
)

:: ── .env file ────────────────────────────────────────────────────────────────
echo.
echo [3/4] Setting up .env file...
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env from template.
) else (
    echo .env already exists – skipping.
)

:: ── Whisper model pre-download ───────────────────────────────────────────────
echo.
echo [4/5] Pre-downloading Whisper model (base, ~145 MB)...
python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8'); print('Whisper model ready.')"

:: ── Piper TTS local voice ─────────────────────────────────────────────────────
echo.
echo [5/5] Downloading Piper local voice (en_US-lessac-medium female, ~62 MB)...
if not exist "backend\voices" mkdir "backend\voices"
python -c ^
"import urllib.request, os; ^
v='backend/voices/en_US-lessac-medium'; ^
model=v+'.onnx'; cfg=v+'.onnx.json'; ^
base='https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/'; ^
[urllib.request.urlretrieve(base+os.path.basename(f), f) for f in [model,cfg] if not os.path.exists(f)]; ^
print('Piper voice ready.')"
if errorlevel 1 (
    echo [WARN] Piper voice download failed - Jarvis will fall back to cloud TTS.
)

:: ── Done ─────────────────────────────────────────────────────────────────────
echo.
echo  ============================================
echo   Installation complete!
echo  ============================================
echo.
echo  Next steps:
echo    1. Edit .env  and set your ANTHROPIC_API_KEY
echo    2. Run start.bat  to launch Jarvis
echo.
pause
