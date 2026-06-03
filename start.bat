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

:: Voice enrollment is handled IN-APP: on first launch (no jarvis_voiceprint.npy)
:: Jarvis greets you and asks you to speak, then learns your voice by ear.
:: Delete jarvis_voiceprint.npy to re-enroll, or set SPEAKER_LOCK=False to disable.

echo Starting Jarvis (Administrator)...
"%~dp0frontend\node_modules\.bin\electron.cmd" "%~dp0frontend"
