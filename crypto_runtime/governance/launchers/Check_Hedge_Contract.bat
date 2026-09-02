@echo off
cd /d "%~dp0\..\.."
set PYTHONUTF8=1
"venv\Scripts\python.exe" governance\checks\check_hedge_contract.py
"venv\Scripts\python.exe" governance\checks\check_hedge_chain.py
pause
