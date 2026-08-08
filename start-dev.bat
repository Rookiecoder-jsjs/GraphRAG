@echo off
setlocal enabledelayedexpansion

echo ========================================
echo  Knowledge Graph System
echo ========================================
echo.

REM Resolve venv python to an ABSOLUTE path so later cwd changes don't break it.
REM Search order: repo root, parent of repo, inside backend.
set "VENV_PYTHON="
if exist "%~dp0.venv\Scripts\python.exe" (
    for %%I in ("%~dp0.venv\Scripts\python.exe") do set "VENV_PYTHON=%%~fI"
) else if exist "%~dp0..\.venv\Scripts\python.exe" (
    for %%I in ("%~dp0..\.venv\Scripts\python.exe") do set "VENV_PYTHON=%%~fI"
) else if exist "%~dp0backend\.venv\Scripts\python.exe" (
    for %%I in ("%~dp0backend\.venv\Scripts\python.exe") do set "VENV_PYTHON=%%~fI"
) else (
    echo ERROR: Could not find Python virtual environment.
    echo        Looked for .venv\Scripts\python.exe under:
    echo          %~dp0
    echo          %~dp0..
    echo          %~dp0backend
    pause
    exit /b 1
)

echo Using Python: !VENV_PYTHON!
echo.

echo Starting Backend on port 8001...
start "KG Backend" /D "%~dp0backend" cmd /k !VENV_PYTHON! -m uvicorn app.main:app --host 0.0.0.0 --port 8001 --reload

timeout /t 2 /nobreak > nul

echo Starting Frontend on port 5173...
start "KG Frontend" /D "%~dp0frontend" cmd /k npm run dev

echo.
echo ========================================
echo  Services starting:
echo    - Backend:  http://localhost:8001
echo    - Frontend: http://localhost:5173
echo ========================================
echo.
echo Close the command windows to stop the services.
pause
