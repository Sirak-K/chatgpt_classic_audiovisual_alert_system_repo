@echo off
setlocal
set "LINK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ChatGPT Classic Alert Watcher.lnk"
if exist "%LINK%" del /q "%LINK%"
echo Startup disabled.
exit /b 0
