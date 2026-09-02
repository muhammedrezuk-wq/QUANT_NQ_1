@echo off
chcp 65001 >nul
title QUANT_NQ_NEWS - بوت الأخبار
cd /d "%~dp0"
set PYTHONUTF8=1

rem 1) البيئة
if not exist ".venv\Scripts\python.exe" (
  echo   [1/3] تجهيز بيئة بايثون لأول مرة...
  py -3 -m venv .venv
  if errorlevel 1 goto fail
)

rem 2) المكتبات — تُثبَّت فقط إذا كانت ناقصة
".venv\Scripts\python.exe" -c "import telegram, apscheduler, feedparser, dotenv, requests; from deep_translator import GoogleTranslator" >nul 2>&1
if errorlevel 1 (
  echo   [2/3] في مكتبات ناقصة — عم تتثبت الآن...
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
  echo   تم تثبيت المكتبات.
)

rem 3) ملف الإعدادات والتوكن — بيد المالك وحده
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo.
  echo   انعمل ملف .env — لسا ما فيه توكن.
  echo   حط توكن البوت بسطر BOT_TOKEN ثم شغل هذا الملف من جديد.
  echo.
  notepad ".env"
  goto end
)

".venv\Scripts\python.exe" -c "import io,sys;s=io.open('.env',encoding='utf-8',errors='ignore').read();v=s.split('BOT_TOKEN=')[1].split(chr(10))[0].strip() if 'BOT_TOKEN=' in s else '';sys.exit(0 if len(v)>20 and ':' in v else 1)"
if errorlevel 1 (
  echo.
  echo   التوكن لسا ما انحط داخل .env — بفتحه لك الآن.
  echo   بعد ما تحطه واتحفظ، شغل هذا الملف من جديد.
  echo.
  notepad ".env"
  goto end
)

echo.
echo   البوت شغال — لا تسكر هذه النافذة.
echo   من تلغرام ابعتله /id لتاخد رقمك، و /add_user لإضافة طالب.
echo.
".venv\Scripts\python.exe" main.py
if errorlevel 2 (
  echo.
  echo   البوت شغال بنافذة ثانية — سكر النافذة القديمة ثم اعد المحاولة.
)
goto end

:fail
echo.
echo   فشل التجهيز — اقرا الخطأ فوق.

:end
echo.
pause
