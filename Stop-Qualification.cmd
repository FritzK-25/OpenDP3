@echo off
setlocal
if not exist "%~dp0data" mkdir "%~dp0data"
echo stop>"%~dp0data\qualification.stop"
echo The background qualification recorder will stop and release Bluetooth shortly.
pause
