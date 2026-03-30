@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

rem Create a clean delivery folder for the client:
rem - root: Lancer_AthletesSearcher.bat + app\
rem - excludes: caches, logs, csv, build artifacts, editor folders

set "STAMP=%DATE:~-4%%DATE:~3,2%%DATE:~0,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "STAMP=%STAMP: =0%"
set "OUT_DIR=%CD%\Livraison_Client_%STAMP%"

echo Creation du dossier de livraison:
echo   %OUT_DIR%
echo.

if exist "%OUT_DIR%" (
  echo [ERREUR] Le dossier existe deja.
  pause
  exit /b 1
)

mkdir "%OUT_DIR%" >nul 2>&1
mkdir "%OUT_DIR%\app" >nul 2>&1

rem 1) Copy launcher
if not exist "Lancer_AthletesSearcher.bat" (
  echo [ERREUR] Launcher introuvable: Lancer_AthletesSearcher.bat
  pause
  exit /b 1
)
copy /Y "Lancer_AthletesSearcher.bat" "%OUT_DIR%\" >nul

rem 2) Copy app folder (including embedded venv)
if not exist "app\app.py" (
  echo [ERREUR] Dossier app invalide: app\app.py introuvable
  pause
  exit /b 1
)

echo Copie de app\ ...
robocopy "%CD%\app" "%OUT_DIR%\app" /E /NFL /NDL /NJH /NJS /NP /XD "__pycache__" ".pytest_cache" ".mypy_cache" ".ruff_cache" /XF "*.pyc" >nul
if errorlevel 8 goto :copy_fail

rem 3) Do NOT copy runtime / dev artifacts to root (client creates them)
rem - .appdata\, Data\, *.csv, build/dist/release, dev, .vscode, .venv, etc.

echo.
echo OK.
echo Dossier client pret:
echo   %OUT_DIR%
echo.
echo A livrer: le contenu de ce dossier uniquement.
pause
exit /b 0

:copy_fail
echo [ERREUR] Echec robocopy (errorlevel>=8).
pause
exit /b 1

