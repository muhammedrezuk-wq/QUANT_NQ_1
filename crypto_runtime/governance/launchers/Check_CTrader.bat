@echo off
cd /d "%~dp0\..\.."
set PYTHONUTF8=1
if exist "venv\Scripts\python.exe" ("venv\Scripts\python.exe" governance\scripts\check_ctrader.py) else (py -3 governance\scripts\check_ctrader.py)
pause
