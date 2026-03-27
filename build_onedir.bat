@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=D:\Pythons\Python3.11.4Global\python.exe"
set "BUILD_VENV=.buildvenv"

if not exist "%PYTHON_EXE%" (
  echo Python 3.11 introuvable: "%PYTHON_EXE%"
  echo Modifiez PYTHON_EXE dans build_onedir.bat puis relancez.
  pause
  exit /b 1
)

echo [0/5] Fermeture des instances en cours...
taskkill /IM SportifsManager.exe /F >nul 2>&1

echo [1/5] Nettoyage anciens artefacts...
if exist "build" rmdir /S /Q "build"
if exist "dist" rmdir /S /Q "dist"
if exist "release\SportifsManager" rmdir /S /Q "release\SportifsManager"

echo [2/5] Preparation venv de build (propre)...
if exist "%BUILD_VENV%" rmdir /S /Q "%BUILD_VENV%"
"%PYTHON_EXE%" -m venv "%BUILD_VENV%"

echo [3/5] Installation des dependances (venv)...
call "%BUILD_VENV%\\Scripts\\python.exe" -m pip install --upgrade pip
rem Evite des crashs de modules compiles (numpy 2.x)
call "%BUILD_VENV%\\Scripts\\python.exe" -m pip install "numpy<2"
call "%BUILD_VENV%\\Scripts\\python.exe" -m pip install -r requirements.txt
call "%BUILD_VENV%\\Scripts\\python.exe" -m pip install pyinstaller

echo [4/5] Build (onedir)...
call "%BUILD_VENV%\\Scripts\\python.exe" -m PyInstaller --clean --noconfirm --onedir --windowed --name SportifsManager ^
  --collect-submodules selenium ^
  --collect-data selenium ^
  --exclude-module pkg_resources ^
  --exclude-module setuptools ^
  --exclude-module PySide6 ^
  --exclude-module shiboken6 ^
  --exclude-module matplotlib ^
  app.py

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
call "release\Lancer_SportifsManager.bat"
pause
