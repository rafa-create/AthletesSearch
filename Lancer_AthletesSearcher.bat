@echo off
setlocal enableextensions
cd /d "%~dp0"

set VENV_DIR=.buildvenv
set PY="%VENV_DIR%\Scripts\python.exe"

if not exist "%PY%" (
  echo [Launcher] Creation du venv...
  py -3.11 -m venv "%VENV_DIR%" 2>nul
  if errorlevel 1 (
    py -3 -m venv "%VENV_DIR%"
  )
)

echo [Launcher] Installation/verification dependances...
"%PY%" -m pip -q install --upgrade pip >nul 2>&1
"%PY%" -m pip -q install -r requirements.txt

echo [Launcher] Lancement...
"%PY%" "%~dp0app.py"

