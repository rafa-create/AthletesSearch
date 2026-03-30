@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."

rem Create a clean delivery folder for the client:
rem - root: Lancer_AthletesSearcher.bat + app\
rem - never deliver: .appdata\, Data\, dev\, root-level *.py, build artifacts, editor folders

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
robocopy "%CD%\app" "%OUT_DIR%\app" /E /NFL /NDL /NJH /NJS /NP ^
  /XD "__pycache__" ".pytest_cache" ".mypy_cache" ".ruff_cache" ^
  /XF "*.pyc" >nul
if errorlevel 8 goto :copy_fail

rem 3) Hard cleanup: never deliver these folders/files
if exist "%OUT_DIR%\.appdata" rmdir /S /Q "%OUT_DIR%\.appdata" >nul 2>&1
if exist "%OUT_DIR%\Data" rmdir /S /Q "%OUT_DIR%\Data" >nul 2>&1
if exist "%OUT_DIR%\dev" rmdir /S /Q "%OUT_DIR%\dev" >nul 2>&1
if exist "%OUT_DIR%\exemple" rmdir /S /Q "%OUT_DIR%\exemple" >nul 2>&1
del /Q "%OUT_DIR%\*.py" >nul 2>&1
del /Q "%OUT_DIR%\Creer_Livraison_Client.bat" >nul 2>&1

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

