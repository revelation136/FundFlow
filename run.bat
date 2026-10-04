@echo off
rem Start FundFlow with your real ledger (data\fundflow.db) and open the browser.
cd /d "%~dp0"
python -m fundflow --open %*
pause
