@echo off
setlocal
cd /d "%~dp0"
title Creazione Photo Selector EXE

echo ==========================================
echo   CREAZIONE PHOTO SELECTOR.EXE
echo ==========================================
echo.

set PYTHON_CMD=py
where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (
    echo ERRORE: Python non risulta installato.
    echo Installa Python 3.12 da https://www.python.org/downloads/windows/
    echo Durante l'installazione seleziona "Add python.exe to PATH".
    echo Poi esegui di nuovo questo file.
    echo.
    pause
    exit /b 1
  )
  set PYTHON_CMD=python
)

echo [1/3] Installazione/aggiornamento delle librerie...
%PYTHON_CMD% -m pip install --upgrade pip
if errorlevel 1 goto errore
%PYTHON_CMD% -m pip install -r requirements.txt
if errorlevel 1 goto errore
%PYTHON_CMD% -m pip install pyinstaller
if errorlevel 1 goto errore

echo.
echo [2/3] Creazione dell'eseguibile. Potrebbe richiedere alcuni minuti...
%PYTHON_CMD% -m PyInstaller --noconfirm --clean --windowed --onefile --name "Photo Selector" main.py
if errorlevel 1 goto errore

echo.
echo [3/3] Operazione completata.
echo L'eseguibile si trova in:
echo %CD%\dist\Photo Selector.exe
echo.
explorer "%CD%\dist"
pause
exit /b 0

:errore
echo.
echo Si e' verificato un errore. Leggi il messaggio qui sopra.
echo Se Python e' stato installato adesso, chiudi questa finestra e riapri CREA_EXE.bat.
echo.
pause
exit /b 1
