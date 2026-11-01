@echo off
title Hopper Flight Price Scraper & Intelligence Tracker
cd /d "%~dp0"
echo ====================================================================
echo    Starting Hopper Flight Price Scraper (Python + Playwright)
echo    Connecting directly to Supabase...
echo    Opening your Web Dashboard automatically in browser...
echo ====================================================================
python web_app.py
pause

