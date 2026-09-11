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

if not "%RC%"=="0" (
  echo.
  echo Primary detection did not find ChatGPT Classic.
  echo Running raw Windows discovery probe...
  echo.
  ".venv\Scripts\python.exe" "%~dp0probe_windows.py"
)

echo.
pause
exit /b %RC%
