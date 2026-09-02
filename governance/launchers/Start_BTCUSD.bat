@echo off
cd /d "%~dp0\..\.."
set PYTHONUTF8=1
if not defined NQ_BRIDGE_DB set "NQ_BRIDGE_DB=%APPDATA%\MetaQuotes\Terminal\Common\Files\nq_brain.db"
if exist "venv\Scripts\python.exe" ("venv\Scripts\python.exe" governance\scripts\start_asset.py BTCUSD 100) else (py -3 governance\scripts\start_asset.py BTCUSD 100)
pause
