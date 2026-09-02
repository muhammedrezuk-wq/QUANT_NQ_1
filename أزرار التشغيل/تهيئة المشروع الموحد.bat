@echo off
REM ---------------------------------------------------------------------
REM  QUANT_NQ - first run setup.
REM
REM  ASCII ONLY.  Measured 2026-09-01: an earlier version of this file had
REM  Arabic comments and messages inside an IF block plus a "^" line
REM  continuation.  cmd.exe reads a .bat in the console OEM codepage and
REM  desynchronises after chcp, so those lines were executed as commands:
REM      'no' is not recognized as an internal or external command
REM      '" echo python312.zip' is not recognized ...
REM  and the interpreter was never unpacked.  All human text lives in
REM  scripts\setup_machine.py, which prints UTF-8 safely.
REM ---------------------------------------------------------------------
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d "%~dp0\.."

set "RT=vendor\python\runtime"
set "RTPY=%RT%\python.exe"

if exist "%RTPY%" goto RUN

echo [1/2] unpacking the project interpreter ...
if not exist "vendor\python" goto NOEMBED
for %%F in (vendor\python\python-*-embed-amd64.zip) do set "EMBED=%%F"
if not defined EMBED goto NOEMBED
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -LiteralPath '%EMBED%' -DestinationPath '%RT%' -Force"
if exist "%RTPY%" echo       done: %RTPY%
goto RUN

:NOEMBED
echo       no bundled interpreter found - will use system Python

:RUN
echo [2/2] running setup ...
echo.
if exist "%RTPY%" goto OURS
where py >nul 2>&1
if errorlevel 1 goto SYSPY
py -3 scripts\setup_machine.py
goto DONE
:SYSPY
python scripts\setup_machine.py
goto DONE
:OURS
"%RTPY%" scripts\setup_machine.py
:DONE
set CODE=%ERRORLEVEL%
echo.
pause
endlocal & exit /b %CODE%
