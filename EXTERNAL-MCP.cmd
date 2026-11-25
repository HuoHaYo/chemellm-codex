@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run INSTALL.cmd first.
    pause
    exit /b 1
)
if not "%~1"=="" (
    ".venv\Scripts\python.exe" -X utf8 scripts\setup-external-mcp.py %*
    exit /b
)
echo Enable an optional engineering MCP for this ChemELLM install.
echo   B  Blender Lab MCP - needs Blender 5.1+ and Git
echo   F  FreeCAD MCP Bridge - needs FreeCAD 1.1+ and Node.js 18+
echo   V  Interactive Vega Charts
echo   Q  QElectroTech project reading and checks
echo   D  DWSIM steady-state MCP - needs supported Windows DWSIM
echo   S  Show configuration
echo   X  Exit
choice /c BFVQDSX /n /m "Choose: "
set "selection=%ERRORLEVEL%"
if "%selection%"=="7" exit /b 0
if "%selection%"=="6" set "tool=status"
if "%selection%"=="5" set "tool=dwsim"
if "%selection%"=="4" set "tool=qet"
if "%selection%"=="3" set "tool=vega"
if "%selection%"=="2" set "tool=freecad"
if "%selection%"=="1" set "tool=blender"
if not defined tool exit /b 1
".venv\Scripts\python.exe" -X utf8 scripts\setup-external-mcp.py %tool%
set "result=%ERRORLEVEL%"
pause
exit /b %result%
