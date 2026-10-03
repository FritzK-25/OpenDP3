@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo OpenPowerstation dependencies are not installed. Follow docs/REFERENCE.md first.
  pause
  exit /b 1
)
rem Retakes Bluetooth and restarts the Home Assistant bridge in one step, for the
rem case where the DP3 was off at boot. Safe to run twice: a collector or bridge
rem that is already running keeps running instead of being duplicated.
".venv\Scripts\python.exe" "%~dp0scripts\start_all.py" --data-dir "%~dp0data" %*
pause
