@echo off
setlocal
cd /d "%~dp0\.."
set PYEXE=%~dp0..\venv\Scripts\python.exe

if not exist "%PYEXE%" (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -m venv "%~dp0..\venv"
  ) else (
    python -m venv "%~dp0..\venv"
  )
)
if not exist "%PYEXE%" (
  echo Python venv was not created.
  pause
  exit /b 1
)

"%PYEXE%" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo Could not install Python requirements.
  pause
  exit /b 1
)

"%PYEXE%" scripts\launch_unified.py %*
set CODE=%ERRORLEVEL%
if not "%CODE%"=="0" pause
endlocal & exit /b %CODE%
