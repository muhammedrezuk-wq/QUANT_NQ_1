@echo off
chcp 65001 >nul
REM ---------------------------------------------------------------------
REM  chcp MUST stay on line 2.  cmd parses each line in the console OEM
REM  codepage as it reaches it, so any Arabic ABOVE this line is read as
REM  garbage and cmd tries to run the fragments as commands.
REM  Measured 2026-09-01: an Arabic REM on line 2 produced
REM      "'..' is not recognized as an internal or external command"
REM  on every single press.  Same hazard documented in scripts\py.bat.
REM ---------------------------------------------------------------------
setlocal
set PYTHONUTF8=1
set QUANT_LOCAL_MODE=1
cd /d "%~dp0"

REM  If the platform is already up, app.py refuses a second instance and
REM  exits 0.  Without the branch below the window closed instantly and
REM  the button looked dead (owner report 2026-09-01).
netstat -ano | findstr /r /c:"TCP.*127.0.0.1:8090.*LISTENING" >nul
if not errorlevel 1 (
  echo.
  echo   المنصّة تعمل أصلًا — لا تُفتح نسخة ثانية.
  echo   اللوحة:  http://127.0.0.1:8090
  echo.
  echo   أفتحها لك الآن...
  start "" "http://127.0.0.1:8090"
  ping -n 5 127.0.0.1 >nul
  endlocal & exit /b 0
)

call scripts\py.bat scripts\log_button.py "control_room" START >nul 2>&1
call scripts\py.bat governance\app.py
set CODE=%ERRORLEVEL%
echo.
if "%CODE%"=="0" (
  echo   غرفة القيادة أُغلقت طبيعيًّا.
) else (
  echo   غرفة القيادة خرجت بالرمز %CODE%.
)
echo.
pause
endlocal & exit /b %CODE%
