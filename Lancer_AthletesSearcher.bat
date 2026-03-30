@echo off
if /I not "%~1"=="hidden" (
  mshta vbscript:CreateObject("WScript.Shell").Run("""%~f0"" hidden",0)(window.close)
  exit /b
)

setlocal
cd /d "%~dp0"

rem Root of the delivery (CSV/logs created here)
set "ATHLETES_ROOT=%CD%"

rem If you later move code/venv into .\app\, update these 2 paths:
set "PY_EXE=%CD%\app\.buildvenv\Scripts\pythonw.exe"
set "APP_FILE=%CD%\app\app.py"

if not exist "%PY_EXE%" (
  rem fallback (can show console only if pythonw missing)
  set "PY_EXE=%CD%\app\.buildvenv\Scripts\python.exe"
)

if not exist "%PY_EXE%" exit /b 1
if not exist "%APP_FILE%" (
  exit /b 1
)

"%PY_EXE%" "%APP_FILE%"
exit /b 0

