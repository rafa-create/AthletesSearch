@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=D:\Pythons\Python3.11.4Global\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python 3.11 introuvable: "%PYTHON_EXE%"
  echo Modifiez PYTHON_EXE dans build_onedir.bat puis relancez.
  pause
  exit /b 1
)

echo [1/4] Installation des dependances...
"%PYTHON_EXE%" -m pip install -r requirements.txt

echo [2/4] Build (onedir)...
"%PYTHON_EXE%" -m PyInstaller --noconfirm --onedir --windowed --name SportifsManager --collect-submodules selenium --collect-data selenium app.py

echo [3/4] Preparation du dossier release...
if not exist release mkdir release
if exist "release\SportifsManager" rmdir /S /Q "release\SportifsManager"
xcopy /E /I /Y "dist\SportifsManager" "release\SportifsManager" >nul

echo [4/4] Ajout du lanceur...
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
