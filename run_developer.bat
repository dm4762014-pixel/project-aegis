@echo off
cd /d "%~dp0"
where node >nul 2>&1
if errorlevel 1 (
    echo Install Node.js 20 or newer before running the browser lab.
    exit /b 1
)
call npm ci
if errorlevel 1 exit /b 1
call npx playwright install chromium
if errorlevel 1 exit /b 1
echo Developer demo: http://127.0.0.1:8000/developer
call run_clinic.bat
