@echo off
setlocal
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv
    if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe laptop_setup.py
if errorlevel 1 exit /b 1
set AEGIS_PROFILE=laptop
set AEGIS_MODEL=smollm2:135m-instruct-q4_K_M
set OLLAMA_URL=http://127.0.0.1:11435
echo Open http://127.0.0.1:8000/aegis . Keep this window open; Ctrl+C stops the gateway.
echo Model weights unload after each generation. The small isolated server remains available.
.venv\Scripts\python.exe -m uvicorn gateway:app --host 127.0.0.1 --port 8000 --no-proxy-headers
