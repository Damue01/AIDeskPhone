@echo off
setlocal

pushd "%~dp0"

set "ARG1=%~1"
set "ARG2=%~2"
set "SERIAL_PORT="
set "WEB_PORT=8765"
set "OPEN_PATH=/command-center/"

if not "%ARG1%"=="" (
  echo %ARG1%| findstr /r "^[0-9][0-9]*$" >nul
  if not errorlevel 1 (
    set "WEB_PORT=%ARG1%"
  ) else if /I "%ARG1%"=="auto" (
    if not "%ARG2%"=="" set "WEB_PORT=%ARG2%"
  ) else (
    set "SERIAL_PORT=%ARG1%"
    if not "%ARG2%"=="" set "WEB_PORT=%ARG2%"
  )
)

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul
  if errorlevel 1 (
    echo Existing Python virtual environment is not usable. Recreating .venv...
    rmdir /s /q ".venv"
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  py -3.12 -m venv .venv
  if errorlevel 1 (
    py -3.11 -m venv .venv
  )
  if errorlevel 1 (
    python -m venv .venv
  )
  if errorlevel 1 (
    echo Failed to create .venv. Install Python 3.11 or 3.12 and try again.
    pause
    popd
    exit /b 1
  )
  set "NEED_INSTALL=1"
)

set "PYTHON=.venv\Scripts\python.exe"

"%PYTHON%" -c "import serial" >nul 2>nul
if errorlevel 1 set "NEED_INSTALL=1"

"%PYTHON%" -c "import sounddevice" >nul 2>nul
if errorlevel 1 set "NEED_INSTALL=1"

if defined NEED_INSTALL (
  echo Installing Python dependencies...
  "%PYTHON%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Failed to install dependencies.
    pause
    popd
    exit /b 1
  )
)

echo Starting AI Desk Phone console...
if "%SERIAL_PORT%"=="" (
  echo Serial port: auto scan
) else (
  echo Serial port: %SERIAL_PORT%
)
echo Agent page: http://127.0.0.1:%WEB_PORT%%OPEN_PATH%
echo Console URL: http://127.0.0.1:%WEB_PORT%/

if not "%AI_DESK_PHONE_NO_BROWSER%"=="1" (
  start "" "http://127.0.0.1:%WEB_PORT%%OPEN_PATH%"
)

if "%SERIAL_PORT%"=="" (
  "%PYTHON%" tools\ai_desk_phone_console.py --web-port "%WEB_PORT%"
) else (
  "%PYTHON%" tools\ai_desk_phone_console.py --port "%SERIAL_PORT%" --web-port "%WEB_PORT%"
)

popd
