@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0data" mkdir "%~dp0data"
rem Graceful counterpart to Start-OpenPowerstation.cmd. The collector finishes its session
rem and releases Bluetooth; the bridges mark their devices offline before exiting.
echo stop>"%~dp0data\collector.stop"
echo stop>"%~dp0data\bridge.stop"
echo stop>"%~dp0data\jackery.stop"
echo stop>"%~dp0data\jackery-bridge.stop"
echo Recording and the bridges will stop and release Bluetooth shortly.
pause
