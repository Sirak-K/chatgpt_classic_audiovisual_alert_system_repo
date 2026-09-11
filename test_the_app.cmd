@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv is missing. Run install.cmd first.
  pause
  exit /b 1
)
echo Running audiovisual self-test...
".venv\Scripts\python.exe" "%~dp0notify.py" --self-test
set "RC=%errorlevel%"
echo.
echo Self-test exit code: %RC%
pause
exit /b %RC%
