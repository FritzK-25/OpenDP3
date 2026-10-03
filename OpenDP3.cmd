@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo OpenDP3 dependencies are not installed. Follow docs/REFERENCE.md first.
  pause
  exit /b 1
)
start "" "%~dp0.venv\Scripts\pythonw.exe" -m opendp3 --data-dir "%~dp0data" gui

