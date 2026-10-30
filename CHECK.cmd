@echo off
setlocal
chcp 65001 >nul
title ChemELLM Codex Check
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Run INSTALL.cmd first.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0scripts\check-installation.py"
set "check_exit=%ERRORLEVEL%"
echo.
pause
exit /b %check_exit%
