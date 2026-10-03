@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo OpenDP3 dependencies are not installed. Follow docs/REFERENCE.md first.
  pause
  exit /b 1
)
rem Publishes current readings to Home Assistant over MQTT. Reads the database
rem read-only, so it is safe to start and stop while a recording is running.
"%~dp0.venv\Scripts\python.exe" -m opendp3 --data-dir "%~dp0data" bridge %*
if errorlevel 1 pause
