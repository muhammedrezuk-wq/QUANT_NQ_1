@echo off
cd /d "%~dp0\..\.."
set PYTHONUTF8=1
if exist "venv\Scripts\python.exe" ("venv\Scripts\python.exe" governance\checks\check_governance.py) else (py -3 governance\checks\check_governance.py)
pause
