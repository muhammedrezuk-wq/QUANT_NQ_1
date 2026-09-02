@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
cd /d "%~dp0\.."
call scripts\py.bat scripts\log_button.py "stop_all" START >nul 2>&1
echo ======================================================================
echo   ايقاف خدمات QUANT_NQ - اغلاق نظيف تُكتب فيه اللقطات قبل الاغلاق
echo ======================================================================
call scripts\py.bat scripts\stop_all.py
set CODE=%ERRORLEVEL%
echo.
if not "%CODE%"=="0" echo فشل الإيقاف — راجع سجل التشغيل.
pause
endlocal & exit /b %CODE%
