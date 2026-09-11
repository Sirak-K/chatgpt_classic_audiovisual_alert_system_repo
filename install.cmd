@echo off
setlocal
cd /d "%~dp0"

echo [1/3] Locating Python...
set "PYTHON="
where py >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON where python >nul 2>nul && set "PYTHON=python"
if not defined PYTHON (
  echo ERROR: Python 3 was not found in PATH.
  echo Install Python 3.10+ and rerun this file.
  exit /b 1
)

echo [2/3] Creating local virtual environment...
if not exist ".venv\Scripts\python.exe" %PYTHON% -m venv .venv
if errorlevel 1 exit /b %errorlevel%

echo [3/3] Installing watcher dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b %errorlevel%
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b %errorlevel%

echo.
echo Installation complete.
echo Next: open ChatGPT Classic, then run diagnose_chatgpt.cmd.
exit /b 0
