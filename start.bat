@echo off
title Screengu - Unified Stock Screener
cd /d "%~dp0"
setlocal

rem ---------- find a python that can actually run this app ----------
rem Tries PATH python first, then the py launcher, then common install
rem paths. Every candidate must be able to import the real deps, so a
rem bare python without uvicorn/pandas will be skipped automatically.
set "PYEXE="
for %%P in ("python" "py -3" "C:\Python311\python.exe" "C:\Python312\python.exe" "C:\Python313\python.exe") do (
  if not defined PYEXE (
    %%~P -c "import uvicorn, fastapi, pandas, numpy, requests, akshare" >nul 2>&1
    if not errorlevel 1 set "PYEXE=%%~P"
  )
)

rem ---------- first run: no ready-to-use python, try auto setup ----------
if not defined PYEXE (
  echo [SETUP] No python with the required packages was found.
  echo         Trying to install dependencies automatically, 1-3 min...
  echo.
  where py >nul 2>&1
  if not errorlevel 1 (set "PYEXE=py -3") else (set "PYEXE=python")
  %PYEXE% -m pip install -r requirements.txt
  %PYEXE% -c "import uvicorn, fastapi, pandas, numpy, requests, akshare" >nul 2>&1
  if errorlevel 1 (
    echo.
    echo [ERROR] Auto setup failed. Please run manually then retry:
    echo         %PYEXE% -m pip install -r requirements.txt
    pause
    exit /b 1
  )
  echo.
  echo [SETUP] Dependencies installed OK.
)

set "PORT=8765"

rem ---------- check if port is already in use ----------
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" > nul
if %errorlevel%==0 (
  echo Port %PORT% is already in use, opening browser...
  start "" "http://127.0.0.1:%PORT%/"
  exit /b 0
)

rem ---------- launch server in a NEW independent window ----------
echo Starting server (new window), please wait...
start "Screengu-Server" cmd /c "%PYEXE% -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%"

rem ---------- wait until server is ready (max 40s) ----------
set /a tries=0
:waitloop
timeout /t 1 /nobreak > nul
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" > nul
if %errorlevel%==0 goto ready
set /a tries+=1
if %tries% geq 40 goto fail
goto waitloop

:ready
echo Server is up.
start "" "http://127.0.0.1:%PORT%/"
echo.
echo ===============================================
echo   Server is running, browser opened:
echo   http://127.0.0.1:%PORT%/
echo   Closing THIS window will NOT stop the server.
echo   To stop: close the black window titled
echo   "Screengu-Server".
echo ===============================================
pause
exit /b 0

:fail
echo.
echo [ERROR] Server failed to start or timed out.
echo         First run may need: "%PYEXE%" -m pip install -r requirements.txt
echo         Or run manually:
echo         "%PYEXE%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%
pause
exit /b 1
