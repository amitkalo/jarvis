@echo off
chcp 65001 > nul
title Jarvis [DEV]
echo ============================================================
echo  Jarvis  ^|  DEV MODE
echo  - Backend:  uvicorn --reload  (restarts on .py changes)
echo  - Frontend: fs.watch          (reloads on .js/.css/.html)
echo  - DevTools: opens detached
echo ============================================================
echo.
"%~dp0frontend\node_modules\.bin\electron.cmd" "%~dp0frontend" --dev
