@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Missing local .venv. See README.md.
  exit /b 1
)
set CHEMELLM_KERNEL_HOST=127.0.0.1
set CHEMELLM_KERNEL_PORT=8766
set CHEMELLM_DATA_ROOT=%cd%\data
set CHEMELLM_KERNEL_DATA=%cd%\data\kernel
set CHEMELLM_DATA_DIR=%cd%\data\kernel
set CHEMELLM_SNAPSHOT_DIR=%cd%\.chem
set CHEMELLM_APP_DIR=%cd%\app
".venv\Scripts\python.exe" -X utf8 kernel\server.py
exit /b %ERRORLEVEL%
