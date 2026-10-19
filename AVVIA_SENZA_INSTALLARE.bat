@echo off
setlocal
cd /d "%~dp0"

set PYTHON_CMD=py
where py >nul 2>nul
if errorlevel 1 (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python non risulta installato. Esegui prima CREA_EXE.bat oppure installa Python.
    pause
    exit /b 1
  )
  set PYTHON_CMD=python
)

%PYTHON_CMD% -m pip show PySide6 >nul 2>nul
if errorlevel 1 (
  echo Esegui prima CREA_EXE.bat per installare le librerie necessarie.
  pause
  exit /b 1
)

if "%PYTHON_CMD%"=="py" (
  pyw main.py
  if errorlevel 1 py main.py
) else (
  start "" pythonw main.py 2>nul || python main.py
)
