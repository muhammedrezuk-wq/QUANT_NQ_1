@echo off
chcp 65001 >nul
REM ---------------------------------------------------------------------
REM  chcp MUST stay on line 2.  cmd parses each line in the console OEM
REM  codepage as it reaches it, so any Arabic ABOVE this line is read as
REM  garbage and cmd tries to run the fragments as commands.
REM  This file must also keep CRLF line endings - a .bat with LF-only
REM  endings breaks cmd on block constructs like the if (...) below.
REM  Both measured 2026-09-01.  See .gitattributes.
REM ---------------------------------------------------------------------
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"

echo.
echo ======================================================================
echo    إيقاف QUANT_NQ — إغلاق نظيف تُكتب فيه اللقطات قبل الإغلاق
echo ======================================================================
echo.

call scripts\py.bat scripts\log_button.py "stop_all" START >nul 2>&1
call scripts\py.bat scripts\stop_all.py
set CODE=%ERRORLEVEL%

echo.
if "%CODE%"=="0" (
  echo   ✓ تمّ الإيقاف — والمنافذ الثمانية مقيسة حرّة.
) else (
  echo   ✗ الإيقاف لم يكتمل — الرمز %CODE%. المنافذ الباقية مسمّاة أعلاه.
  echo     أعد الكبس، وإن تكرّر فالعمليّة تحتاج إنهاءً من مدير المهامّ.
)
echo.
pause
endlocal & exit /b %CODE%
