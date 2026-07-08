@echo off
setlocal

pushd "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\Connect-RealDevice.ps1" -StopExisting %*
set "EXIT_CODE=%ERRORLEVEL%"
popd

if not "%EXIT_CODE%"=="0" (
  echo.
  echo Real-device SOP finished with exit code %EXIT_CODE%.
  echo The web console may still be running for inspection.
  pause
)

exit /b %EXIT_CODE%
