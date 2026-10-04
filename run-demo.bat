@echo off
rem Start FundFlow with a separate demo ledger (data\demo.db) full of sample data.
cd /d "%~dp0"
python -m fundflow --demo --port 5051 --open %*
pause
