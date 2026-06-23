@echo off
cd /d "%~dp0"
if not exist data mkdir data
set LOG=data\auto_update.log
py -3.13 fetch_update.py >> "%LOG%" 2>&1
exit /b 0
