@echo off
REM ---------------------------------------------------------------------
REM  One interpreter for every button.  ASCII only on purpose: cmd reads a
REM  .bat in the console OEM codepage, so Arabic on a command line is a
REM  parsing hazard.  Keep this file ASCII forever.
REM
REM  Order of preference:
REM    1. vendor\python\runtime\python.exe  - OUR OWN Python, shipped with
REM       the project.  Nothing on the machine can move it, upgrade it or
REM       break it.  Owner ruling 2026-09-01: "we mount on the machine and
REM       use its resources without anyone disturbing us - if someone
REM       installs an app or updates a library, our house must not break."
REM    2. venv\Scripts\python.exe           - a project venv, if one was
REM       built from a system Python (older setups).
REM    3. py -3                             - last resort only.
REM
REM  2026-09-01, measured: the project had TWO interpreters in use.
REM    venv\Scripts\python.exe -> complete against requirements.txt
REM    bare "python" (system)  -> missing deep-translator
REM  Twelve of twenty buttons called the bare one, so those runs started
REM  the platform with an incomplete environment and news headlines stayed
REM  untranslated (the import failure is swallowed by design).
REM ---------------------------------------------------------------------
setlocal
set "ROOT=%~dp0.."
REM  "-s" drops the per-user site directory
REM  (%APPDATA%\Python\Python312\site-packages) from sys.path.  Without it
REM  anything the owner pip-installs for his account leaks into our runtime
REM  and can shadow a vendored library.  Measured 2026-09-01: that folder
REM  does not exist yet, so this closes the door before it is used.
if exist "%ROOT%\vendor\python\runtime\python.exe" (
  "%ROOT%\vendor\python\runtime\python.exe" -s %*
  exit /b %ERRORLEVEL%
)
if exist "%ROOT%\venv\Scripts\python.exe" (
  "%ROOT%\venv\Scripts\python.exe" -s %*
  exit /b %ERRORLEVEL%
)

REM  No project interpreter.  Falling through to the machine Python is how
REM  the forex core spent 16.5 hours on an environment missing
REM  deep-translator (measured 2026-09-01) while every failure was swallowed
REM  by design.  A loud stop beats a silent wrong interpreter: run the setup
REM  button once and the vendored runtime appears.
echo.
echo   [QUANT_NQ] No project interpreter found.
echo.
echo     expected: %ROOT%\vendor\python\runtime\python.exe
echo     fallback: %ROOT%\venv\Scripts\python.exe
echo.
echo   Run this once, then press the button again:
echo     "azrar altashghyl\tahyyat almashrou almuwahad.bat"
echo     ^(the setup button in the buttons folder^)
echo.
echo   Refusing to run on the machine Python - libraries would be missing
echo   and the failures are swallowed silently.
echo.
exit /b 9009
