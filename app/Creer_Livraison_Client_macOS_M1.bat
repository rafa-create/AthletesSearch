@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Prepare a macOS M1 (arm64) delivery folder to send to the client.
rem This script DOES NOT build the .app (must be produced on a Mac / CI).
rem It only packages the already-built zip + a short README.

cd /d "%~dp0\.."

set "ZIP_NAME=AthletesSearcher-macOS-arm64.zip"
set "ZIP_PATH=%CD%\%ZIP_NAME%"

if not exist "%ZIP_PATH%" (
  echo.
  echo === Creation livraison client macOS (M1/arm64) ===
  echo [ERREUR] ZIP macOS introuvable a la racine du projet:
  echo   %ZIP_PATH%
  echo.
  echo Place le ZIP ici (a cote de Lancer_AthletesSearcher.bat), puis relance ce script.
  echo Attendu: %ZIP_NAME%
  echo.
  pause
  exit /b 1
)

set "STAMP=%DATE:~-4%%DATE:~3,2%%DATE:~0,2%_%TIME:~0,2%%TIME:~3,2%%TIME:~6,2%"
set "STAMP=%STAMP: =0%"
set "OUT_DIR=%CD%\Livraison_Client_macOS_M1_%STAMP%"

echo.
echo === Creation livraison client macOS (M1/arm64) ===
echo Dossier sortie:
echo   %OUT_DIR%
echo.

if exist "%OUT_DIR%" (
  echo [ERREUR] Le dossier existe deja.
  pause
  exit /b 1
)

mkdir "%OUT_DIR%" >nul 2>&1

copy /Y "%ZIP_PATH%" "%OUT_DIR%\" >nul
if errorlevel 1 (
  echo [ERREUR] Copie du ZIP echouee.
  rmdir /S /Q "%OUT_DIR%" >nul 2>&1
  pause
  exit /b 1
)

rem Write README for the client
(
  echo Athletes Searcher - Installation macOS (M1/arm64)
  echo.
  echo 1^)^) Dezippez: %ZIP_NAME%
  echo 2^)^) Deplacez "Athletes Searcher.app" dans Applications (ou ~/Applications)
  echo 3^)^) Premier lancement:
  echo     - clic droit ^> Ouvrir (si macOS bloque l'app non signee)
  echo.
  echo Mise a jour:
  echo - Dans l'app, bouton "Mise a jour" telecharge la derniere version depuis GitHub Releases
  echo   et remplace automatiquement l'app, puis relance.
  echo.
  echo Logs MAJ (sur Mac):
  echo   ~/Library/Application Support/AthletesSearcher/logs/update.log
  echo.
  echo Important:
  echo - Cette livraison est pour Apple Silicon (M1/M2/M3) uniquement.
  echo - Si votre Mac est Intel, il faut une autre version.
) > "%OUT_DIR%\README_macOS.txt"
if errorlevel 1 (
  echo [ERREUR] Ecriture du README echouee.
  rmdir /S /Q "%OUT_DIR%" >nul 2>&1
  pause
  exit /b 1
)

echo OK.
echo A envoyer au client: le contenu du dossier
echo   %OUT_DIR%
echo.
pause
exit /b 0

