@echo off
rem ============================================================
rem  Screengu server keeper window
rem  --------------------------------------------------------
rem  Why this file exists:
rem  start.bat used to launch the server as `cmd /c uvicorn ...`.
rem  If the user accidentally clicked the window X, pressed Ctrl+C,
rem  or uvicorn itself crashed, the window DISAPPEARED instantly:
rem  no error visible, no way to recover. Symptom reported as
rem  "the cmd window randomly vanishes and the app stops".
rem
rem  This script runs the server in a titled window and:
rem    1) prints the exit reason first, then pauses (window stays)
rem    2) keeps the error visible instead of closing on crash
rem    3) rescues the sync thread: sync runs with daemon=False, so
rem       quitting waits for an in-flight sync to finish cleanly
rem ============================================================
title Screengu-Server (keep open; close = stops server)
cd /d "%~dp0"

set "PORT=8765"
set "PYEXE="
for %%P in ("python" "py -3" "C:\Python311\python.exe" "C:\Python312\python.exe" "C:\Python313\python.exe") do (
  if not defined PYEXE (
    %%~P -c "import uvicorn, fastapi, pandas, numpy, requests, akshare" >nul 2>&1
    if not errorlevel 1 set "PYEXE=%%~P"
  )
)
if not defined PYEXE (
  echo [ERROR] No python with the required packages was found.
  echo         Run start.bat first to install dependencies.
  echo.
  pause
  exit /b 1
)

echo ============================================================
echo   Screengu server starting on http://127.0.0.1:%PORT%/
echo   Python: %PYEXE%
echo.
echo   * Keep THIS window open while using the app.
echo   * To stop the server: press Ctrl+C here, or close it.
echo   * If it crashes, the error stays on screen (no vanish).
echo ============================================================
echo.

"%PYEXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT% --log-level info
set "RC=%errorlevel%"

echo.
echo ============================================================
if "%RC%"=="0" (
  echo   Server exited normally.
) else (
  echo   [WARN] Server exited with code %RC%.
  echo.
  echo   Common fixes:
  echo     - Port %PORT% busy:  netstat -ano ^| findstr :%PORT%
  echo     - Missing deps:      "%PYEXE%" -m pip install -r requirements.txt
  echo     - Read the traceback printed above.
  echo.
  echo   You can also run the server manually in this folder:
  echo     "%PYEXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
)
echo ============================================================
echo.
echo Window stays open so the error above remains readable.
pause
