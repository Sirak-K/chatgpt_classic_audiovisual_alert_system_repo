@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv is missing. Run install.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "%~dp0chatgpt_watcher.py" --diagnose
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%
