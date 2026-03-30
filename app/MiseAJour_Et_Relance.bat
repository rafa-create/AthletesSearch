@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

rem Root of delivery (CSV/logs created here):
rem script is in ./app => delivery root is its parent.
set "ROOT_DIR=%CD%\.."

set "LOG_DIR=%ROOT_DIR%\.appdata\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
set "LOG_FILE=%LOG_DIR%\update.log"

echo === Mise a jour Athletes Searcher ===>"%LOG_FILE%"
echo Debut: %DATE% %TIME%>>"%LOG_FILE%"
echo.>>"%LOG_FILE%"

echo Mise a jour en cours... (journal: "%LOG_FILE%")
echo === Mise a jour Athletes Searcher ===

rem 1) Verifier Git
where git >nul 2>&1
if errorlevel 1 (
  echo [ERREUR] Git n'est pas installe sur ce PC.>>"%LOG_FILE%"
  echo Installez Git for Windows puis relancez.>>"%LOG_FILE%"
  echo [ERREUR] Git n'est pas installe sur ce PC.
  exit /b 1
)

rem 2) Verifier repo
pushd "%ROOT_DIR%" >nul
if not exist ".git" (
  echo [ERREUR] Dossier .git introuvable.>>"%LOG_FILE%"
  echo Cette livraison doit contenir le repo git (ou etre un clone).>>"%LOG_FILE%"
  echo [ERREUR] Dossier .git introuvable.
  popd >nul
  exit /b 1
)

echo [1/4] Recuperation des mises a jour (git pull)...
echo [1/4] git pull --rebase>>"%LOG_FILE%"
git pull --rebase>>"%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo [ERREUR] Echec git pull.>>"%LOG_FILE%"
  echo [ERREUR] Echec git pull.
  popd >nul
  exit /b 1
)
popd >nul

rem 3) Mettre a jour deps Python si venv present
set "PY_EXE=%CD%\.buildvenv\Scripts\python.exe"
if exist "%PY_EXE%" (
  echo [2/4] Mise a jour dependances (pip install -r requirements.txt)...
  echo [2/4] pip install -r requirements.txt>>"%LOG_FILE%"
  "%PY_EXE%" -m pip install -r "requirements.txt">>"%LOG_FILE%" 2>&1
) else (
  echo [2/4] Venv .buildvenv absent, dependances non mises a jour.
  echo [2/4] Venv .buildvenv absent, dependances non mises a jour.>>"%LOG_FILE%"
)

rem 4) Relancer l'app
echo [3/4] Relance de l'application...
if exist "%PY_EXE%" (
  echo [3/4] relance via .buildvenv>>"%LOG_FILE%"
  start "" "%PY_EXE%" "%CD%\app.py"
  echo Fin: %DATE% %TIME%>>"%LOG_FILE%"
  exit /b 0
)

rem fallback: essayer python systeme
where python >nul 2>&1
if errorlevel 1 (
  echo [ERREUR] Python introuvable et .buildvenv absent.>>"%LOG_FILE%"
  echo [ERREUR] Python introuvable et .buildvenv absent.
  exit /b 1
)
echo [3/4] relance via python systeme>>"%LOG_FILE%"
start "" python "%CD%\app.py"
echo Fin: %DATE% %TIME%>>"%LOG_FILE%"
exit /b 0

