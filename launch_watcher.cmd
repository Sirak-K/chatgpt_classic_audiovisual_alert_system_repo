@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo ERROR: .venv is missing. Run install.cmd first.
  exit /b 1
)
start "" /b ".venv\Scripts\pythonw.exe" "%~dp0chatgpt_watcher.py" --watch
exit /b 0
