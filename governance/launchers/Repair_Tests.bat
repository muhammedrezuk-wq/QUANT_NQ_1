@echo off
cd /d "%~dp0\..\.."
set PYTHONUTF8=1
if not exist "venv\Scripts\python.exe" py -3 -m venv venv
"venv\Scripts\python.exe" -m pip install -r governance\setup\requirements.txt
pause
