@echo off
setlocal
cd /d "%~dp0"

rem 设备电量显示 launcher: creates a local venv on first run, then starts silently in tray.
set "PYEXE=.venv\Scripts\python.exe"
set "PYW=.venv\Scripts\pythonw.exe"

if not exist "%PYW%" goto bootstrap
goto run

:bootstrap
echo [setup] creating virtual environment ...
python -m venv .venv
if errorlevel 1 (
  echo [error] python not found. Install Python 3.11+ and try again.
  pause
  exit /b 1
)
echo [setup] installing dependencies ...
"%PYEXE%" -m pip install --upgrade pip -q
"%PYEXE%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo [error] dependency installation failed.
  pause
  exit /b 1
)

:run
start "" "%PYW%" main.py %*
exit /b 0
