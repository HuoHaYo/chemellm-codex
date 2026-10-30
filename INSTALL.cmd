@echo off
setlocal
chcp 65001 >nul
title ChemELLM Codex Installer
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-windows.ps1" %*
set "install_exit=%ERRORLEVEL%"
if not "%install_exit%"=="0" echo Installation failed. See the message above and the installation guide.
echo.
pause
exit /b %install_exit%
