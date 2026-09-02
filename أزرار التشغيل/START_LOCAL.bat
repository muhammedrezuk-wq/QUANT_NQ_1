@echo off
REM Legacy/internal combined launcher. Official launch uses the two market buttons.
setlocal
cd /d "%~dp0.."

if not exist "venv\Scripts\python.exe" (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -m venv venv
  ) else (
    python -m venv venv
  )
)
if not exist "venv\Scripts\python.exe" (
  echo Python virtual environment was not created.
  pause
  exit /b 1
)

"venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Required libraries could not be installed.
  pause
  exit /b 1
)

"venv\Scripts\python.exe" scripts\launch_market.py --both
set CODE=%ERRORLEVEL%
echo.
if "%CODE%"=="0" (
  echo Local stack is ready.
  echo Forex dashboard: http://127.0.0.1:8090
  echo Crypto dashboard: http://127.0.0.1:8091
) else (
  echo Startup failed with code %CODE%.
)
pause
endlocal & exit /b %CODE%
