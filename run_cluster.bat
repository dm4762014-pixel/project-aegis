@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv
    if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
if errorlevel 1 exit /b 1
echo Open http://127.0.0.1:8000 in your browser. Press Ctrl+C to stop.
echo Ollama must be running with a model installed. No automatic model downloads.
.venv\Scripts\python.exe -m uvicorn gateway:app --host 127.0.0.1 --port 8000 --no-proxy-headers
