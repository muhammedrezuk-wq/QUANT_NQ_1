@echo off
chcp 65001 >nul
setlocal
set PYTHONUTF8=1
set QUANT_LOCAL_MODE=1
cd /d "%~dp0\.."
call scripts\py.bat scripts\log_button.py "تشغيل الكريبتو الموحد" START >nul 2>&1
call scripts\py.bat scripts\launch_market.py --market crypto
set CODE=%ERRORLEVEL%
echo.
echo لوحة الكريبتو: http://127.0.0.1:8091
pause
endlocal & exit /b %CODE%
