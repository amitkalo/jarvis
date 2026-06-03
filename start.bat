@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion
title Jarvis

if not exist "%~dp0.env" (
    echo [ERROR] .env not found. Run install.bat first.
    pause & exit /b 1
)

:: ── Admin check ─────────────────────────────────────────────────────────────
:: Running as admin gives the agent full OS permissions (registry, services, etc.)
:: If not admin, re-launch this script elevated via PowerShell.
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [Jarvis] Requesting admin permissions for full OS access...
    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

:: Kill any stale backend left over from a previous session
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0kill_port.ps1"

:: ── Resolve uv (the Python launcher that has all dependencies) ────────────────
set "UV="
for /f "delims=" %%i in ('powershell -NoProfile -Command "(Get-Command uv -ErrorAction SilentlyContinue).Source"') do set "UV=%%i"

:: ── One-time voice enrollment ────────────────────────────────────────────────
:: If no voiceprint exists yet, learn the owner's voice ONCE (records ~15s).
:: On every later run the saved jarvis_voiceprint.npy is just loaded by the
:: backend — it never re-records. Delete that file to re-enroll or turn it off.
if not exist "%~dp0jarvis_voiceprint.npy" (
    echo.
    echo ============================================================
    echo   [Jarvis] First-time setup — learning your voice.
    echo   So I only respond to YOU, speak naturally for 15 seconds.
    echo   ^(Press Ctrl+C to skip — Jarvis will then answer anyone.^)
    echo ============================================================
    echo.
    if defined UV (
        "%UV%" run python "%~dp0backend\enroll_voice.py"
    ) else (
        python "%~dp0backend\enroll_voice.py"
    )
    echo.
)

echo Starting Jarvis (Administrator)...
"%~dp0frontend\node_modules\.bin\electron.cmd" "%~dp0frontend"
