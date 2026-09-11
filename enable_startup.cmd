@echo off
setlocal
cd /d "%~dp0"
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ChatGPT Classic Alert Watcher.lnk"
set "TARGET=%~dp0launch_watcher.cmd"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; $s=$w.CreateShortcut('%LINK%'); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%~dp0'; $s.Save()"
if errorlevel 1 exit /b %errorlevel%
echo Startup enabled.
exit /b 0
