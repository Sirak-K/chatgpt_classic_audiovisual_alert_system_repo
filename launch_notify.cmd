@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE="
if exist ".venv\Scripts\python.exe" set "PYTHON_EXE=.venv\Scripts\python.exe"
if not defined PYTHON_EXE if defined CHATGPT_NOTIFY_PYTHON if exist "%CHATGPT_NOTIFY_PYTHON%" set "PYTHON_EXE=%CHATGPT_NOTIFY_PYTHON%"
if not defined PYTHON_EXE where python >nul 2>nul && set "PYTHON_EXE=python"
if not defined PYTHON_EXE (
  echo ERROR: Python not found. Run install.cmd first.
  exit /b 1
)
"%PYTHON_EXE%" "%~dp0notify.py" %*
exit /b %errorlevel%
