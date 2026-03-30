@echo off
setlocal
cd /d "%~dp0"

rem Root of the delivery (CSV/logs created here)
set "ATHLETES_ROOT=%CD%"

rem If you later move code/venv into .\app\, update these 2 paths:
set "PY_EXE=%CD%\app\.buildvenv\Scripts\python.exe"
set "APP_FILE=%CD%\app\app.py"

if not exist "%PY_EXE%" (
  echo [ERREUR] Python introuvable: "%PY_EXE%"
  echo Assurez-vous que l'environnement .buildvenv existe.
  pause
  exit /b 1
)

if not exist "%APP_FILE%" (
  echo [ERREUR] app.py introuvable: "%APP_FILE%"
  pause
  exit /b 1
)

echo Lancement Athletes Searcher...
"%PY_EXE%" "%APP_FILE%"
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Le programme s'est arrete avec le code %EXIT_CODE%.
  pause
)

exit /b %EXIT_CODE%

