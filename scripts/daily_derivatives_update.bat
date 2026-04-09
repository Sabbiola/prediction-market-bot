@echo off
:: Daily BTC derivatives data collection
:: Appends only new records — safe to run multiple times per day.
::
:: Setup (run once as Administrator):
::   schtasks /create /tn "BTC Derivatives Daily" /tr "C:\path\to\daily_derivatives_update.bat" /sc daily /st 07:00 /ru SYSTEM
::
:: Or schedule manually:
::   Start > Task Scheduler > Create Basic Task > Daily > 07:00

setlocal

:: --- CONFIGURE THIS PATH ---
set BOT_DIR=C:\Users\dennis.bobu\Desktop\prediction-market-bot-feat-beta-live-staging-hardening\prediction-market-bot-feat-beta-live-staging-hardening
set PYTHON=python

:: ----------------------------

cd /d "%BOT_DIR%"

echo [%date% %time%] Starting daily derivatives fetch...

%PYTHON% scripts\btc_fetch_derivatives.py --months 1 --period 5m

if %ERRORLEVEL% neq 0 (
    echo [%date% %time%] ERROR: fetch failed with code %ERRORLEVEL%
    exit /b %ERRORLEVEL%
)

echo [%date% %time%] Done.
endlocal
