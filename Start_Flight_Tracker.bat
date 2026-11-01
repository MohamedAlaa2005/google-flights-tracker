@echo off
title Google Flights Intelligence Tracker
cd /d "%~dp0"
echo ====================================================================
echo    Starting Google Flights Intelligence Tracker (Python 3.13)
echo    Connecting directly to Supabase...
echo    Initializing Daily 12:00 PM Scheduler...
echo    Opening your Web Dashboard automatically in browser...
echo ====================================================================
python web_app.py
pause

