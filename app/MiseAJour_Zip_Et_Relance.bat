@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

rem Delivery root = parent folder of ./app (normalize path to avoid '..' quirks)
for %%I in ("%CD%\..") do set "ROOT_DIR=%%~fI"

set "LOG_DIR=%ROOT_DIR%\.appdata\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
set "LOG_FILE=%LOG_DIR%\update.log"

set "REPO_ZIP_URL=https://github.com/rafa-create/AthletesSearch/archive/refs/heads/main.zip"

echo === Mise a jour Athletes Searcher (ZIP GitHub) ===>"%LOG_FILE%"
echo Debut: %DATE% %TIME%>>"%LOG_FILE%"
echo URL: %REPO_ZIP_URL%>>"%LOG_FILE%"
echo.>>"%LOG_FILE%"

echo Mise a jour en cours... (journal: "%LOG_FILE%")

echo [0/5] Preparation...
echo [0/5] Preparation...>>"%LOG_FILE%"

where powershell >nul 2>&1
if errorlevel 1 (
  echo [ERREUR] PowerShell introuvable.>>"%LOG_FILE%"
  echo [ERREUR] PowerShell introuvable.
  exit /b 1
)

set "TMP_DIR=%TEMP%\athletes_upd_%RANDOM%%RANDOM%"
set "ZIP_PATH=%TMP_DIR%\repo.zip"
set "EXTRACT_DIR=%TMP_DIR%\extract"
mkdir "%TMP_DIR%" >nul 2>&1
mkdir "%EXTRACT_DIR%" >nul 2>&1

echo [1/5] Telechargement du ZIP...>>"%LOG_FILE%"
echo [1/5] Telechargement du ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; Invoke-WebRequest -Uri '%REPO_ZIP_URL%' -OutFile '%ZIP_PATH%' -UseBasicParsing; Write-Host 'ZIP OK'" >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo [ERREUR] Echec telechargement ZIP.>>"%LOG_FILE%"
  echo [ERREUR] Echec telechargement ZIP.
  exit /b 1
)

echo [2/5] Extraction du ZIP...>>"%LOG_FILE%"
echo [2/5] Extraction du ZIP...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; Expand-Archive -Path '%ZIP_PATH%' -DestinationPath '%EXTRACT_DIR%' -Force; Write-Host 'EXTRACT OK'" >>"%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo [ERREUR] Echec extraction ZIP.>>"%LOG_FILE%"
  echo [ERREUR] Echec extraction ZIP.
  exit /b 1
)

rem Find source dir (GitHub usually adds one root folder)
set "SRC_DIR=%EXTRACT_DIR%"
for /d %%D in ("%EXTRACT_DIR%\*") do (
  set "SRC_DIR=%%D"
  goto :got_src
)
:got_src
if not exist "%SRC_DIR%" (
  echo [ERREUR] Dossier source introuvable apres extraction.>>"%LOG_FILE%"
  echo [ERREUR] Dossier source introuvable apres extraction.
  exit /b 1
)
echo Source: %SRC_DIR%>>"%LOG_FILE%"

echo [3/5] Mise a jour des fichiers...>>"%LOG_FILE%"
echo [3/5] Mise a jour des fichiers...

rem robocopy: on ne bloque pas le script si ça échoue (on relance quand même l’app).
robocopy "%SRC_DIR%" "%ROOT_DIR%" /E /NFL /NDL /NJH /NJS /NP /XD ".git" ".buildvenv" ".appdata" "Data" "release" "__pycache__" "build" "dist" "exemple" ".vscode" ".venv" "test" /XF "*.pyc" "build_onedir.bat" "LaunchApp.bat" "README.md" "GUIDE_1_PAGE.md" "*.spec" "requirements.txt" ".gitignore" >nul 2>&1

echo [4/5] Mise a jour dependances (si venv present)...>>"%LOG_FILE%"

rem 4) Update deps if venv present
set "PY_EXE=%ROOT_DIR%\app\.buildvenv\Scripts\python.exe"
if exist "%PY_EXE%" goto :do_pip
echo [4/5] Venv .buildvenv absent, dependances non mises a jour.>>"%LOG_FILE%"
echo [4/5] Venv .buildvenv absent, dependances non mises a jour.
goto :after_pip

:do_pip
echo [4/5] Mise a jour dependances (pip install -r app\requirements.txt)...>>"%LOG_FILE%"
echo [4/5] Mise a jour dependances...
"%PY_EXE%" -m pip install -r "%ROOT_DIR%\app\requirements.txt" >>"%LOG_FILE%" 2>&1

:after_pip

rem 5) Relaunch app
echo [5/5] Relance de l'application...>>"%LOG_FILE%"
echo [5/5] Relance de l'application...
if exist "%PY_EXE%" (
  start "" "%PY_EXE%" "%ROOT_DIR%\app\app.py"
) else (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [ERREUR] Python introuvable.>>"%LOG_FILE%"
    echo [ERREUR] Python introuvable.
    exit /b 1
  )
  start "" python "%ROOT_DIR%\app\app.py"
)

echo Fin: %DATE% %TIME%>>"%LOG_FILE%"
exit /b 0

