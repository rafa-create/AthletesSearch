@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
set "PYTHON_EXE=D:\Pythons\Python3.11.4Global\python.exe"
set "BUILD_VENV=.buildvenv"

rem Mode rapide par defaut (venv + cache PyInstaller conserves).
rem Build complet lent: set CLEAN_BUILD=1 avant de lancer ce script.
rem Exemple: set CLEAN_BUILD=1 && build_onedir.bat

if not exist "%PYTHON_EXE%" (
  echo Python 3.11 introuvable: "%PYTHON_EXE%"
  echo Modifiez PYTHON_EXE dans build_onedir.bat puis relancez.
  pause
  exit /b 1
)

echo [0/5] Fermeture des instances en cours...
taskkill /IM SportifsManager.exe /F >nul 2>&1

if "%CLEAN_BUILD%"=="1" (
  echo [1/5] Nettoyage COMPLET ^(build + dist + release + venv^)...
  if exist "build" rmdir /S /Q "build"
  if exist "dist" rmdir /S /Q "dist"
  if exist "release\SportifsManager" rmdir /S /Q "release\SportifsManager"
  if exist "%BUILD_VENV%" rmdir /S /Q "%BUILD_VENV%"
) else (
  echo [1/5] Nettoyage leger ^(release seulement ; cache build conserve pour PyInstaller^)...
  if exist "release\SportifsManager" rmdir /S /Q "release\SportifsManager"
)

echo [2/5] Preparation venv de build...
if not exist "%BUILD_VENV%\Scripts\python.exe" (
  echo Creation du venv ^(premiere fois ou apres CLEAN_BUILD^)...
  "%PYTHON_EXE%" -m venv "%BUILD_VENV%"
)

echo [3/5] Installation des dependances ^(venv^)...
call "%BUILD_VENV%\Scripts\python.exe" -m pip install --upgrade pip -q
call "%BUILD_VENV%\Scripts\python.exe" -m pip install "numpy<2" -q
call "%BUILD_VENV%\Scripts\python.exe" -m pip install -r requirements.txt -q
call "%BUILD_VENV%\Scripts\python.exe" -m pip install pyinstaller -q

echo [4/5] Build ^(onedir^)...
set "PI_OPTS=--noconfirm --onedir --windowed --name SportifsManager"
if "%CLEAN_BUILD%"=="1" (
  set "PI_OPTS=!PI_OPTS! --clean"
  echo PyInstaller avec --clean ^(analyse complete^).
) else (
  echo PyInstaller sans --clean ^(build incremental plus rapide^).
)
call "%BUILD_VENV%\Scripts\python.exe" -m PyInstaller !PI_OPTS! ^
  --collect-submodules selenium ^
  --collect-data selenium ^
  --exclude-module pkg_resources ^
  --exclude-module setuptools ^
  --exclude-module PySide6 ^
  --exclude-module shiboken6 ^
  --exclude-module matplotlib ^
  app.py

if errorlevel 1 (
  echo Echec PyInstaller.
  pause
  exit /b 1
)

echo [5/5] Preparation du dossier release...
if not exist release mkdir release
if exist "release\SportifsManager" rmdir /S /Q "release\SportifsManager"
xcopy /E /I /Y "dist\SportifsManager" "release\SportifsManager" >nul

echo Ajout du lanceur...
(
  echo @echo off
  echo setlocal
  echo cd /d "%%~dp0"
  echo start "" "SportifsManager\SportifsManager.exe"
) > "release\Lancer_SportifsManager.bat"

echo Build termine.
echo - Dossier: release\SportifsManager\
echo - Lanceur: release\Lancer_SportifsManager.bat
echo.
echo Lancement automatique desactive ^(evite Data/.appdata parasites dans la release^).
echo Pour tester: double-cliquez release\Lancer_SportifsManager.bat dans une copie du dossier.
echo.
if "%CLEAN_BUILD%"=="1" (
  echo Prochain build: sans variable CLEAN_BUILD = mode rapide ^(incremental^).
) else (
  echo Build lent ou bizarre? relancez avec: set CLEAN_BUILD=1 ^&^& build_onedir.bat
)
pause
