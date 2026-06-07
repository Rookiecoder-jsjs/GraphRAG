@echo off
setlocal enabledelayedexpansion

echo ========================================
echo NC Knowledge Graph System
echo ========================================
echo.

REM Find venv python
set "VENV_PYTHON="
if exist ".venv\Scripts\python.exe" (
    set "VENV_PYTHON=.venv\Scripts\python.exe"
) else if exist "..\.venv\Scripts\python.exe" (
    set "VENV_PYTHON=..\.venv\Scripts\python.exe"
) else (
    echo ERROR: Could not find Python virtual environment
    pause
    exit /b 1
)

echo Starting Backend on port 8001...
start "NC Backend" cmd /k "cd /d "%~dp0backend" && !VENV_PYTHON! -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload"

timeout /t 2 /nobreak > nul

echo Starting Frontend on port 5173...
start "NC Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"

echo.
echo ========================================
echo Services starting:
echo   - Backend: http://localhost:8001
echo   - Frontend: http://localhost:5173
echo ========================================
echo.
echo Close the command windows to stop the services
pause
