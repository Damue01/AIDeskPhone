@echo off
setlocal

pushd "%~dp0"

set "SERIAL_PORT=%~1"
set "WEB_PORT=%~2"
if "%WEB_PORT%"=="" set "WEB_PORT=8765"

if "%SERIAL_PORT%"=="" (
  for /f "usebackq delims=" %%P in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$port = Get-CimInstance Win32_SerialPort | Where-Object { $_.PNPDeviceID -match 'VID_303A.*PID_1001' -or $_.Name -match 'USB Serial Device' } | Select-Object -First 1 -ExpandProperty DeviceID; if ($port) { $port }"`) do set "SERIAL_PORT=%%P"
)

if "%SERIAL_PORT%"=="" set "SERIAL_PORT=COM3"

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
echo Serial port: %SERIAL_PORT%
echo Web URL: http://127.0.0.1:%WEB_PORT%

if not "%AI_DESK_PHONE_NO_BROWSER%"=="1" (
  start "" "http://127.0.0.1:%WEB_PORT%"
)

"%PYTHON%" tools\ai_desk_phone_console.py --port "%SERIAL_PORT%" --web-port "%WEB_PORT%"

popd
