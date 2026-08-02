@echo off
setlocal
title Oti-Warehouse Swag - Backend
cd /d "%~dp0"

rem =====================================================================
rem  Oti-Warehouse Swag - Start Backend
rem  Double-click to run the backend on THIS machine (local/dev server).
rem  First run creates a private Python environment (.venv) and installs
rem  packages - takes a few minutes once, instant after that.
rem  The ordering page opens in your browser automatically when ready.
rem  Close this window (or Ctrl+C) to stop the server.
rem =====================================================================

rem ---- settings you might want to change -------------------------------
set "HOST=0.0.0.0"
set "PORT=8000"
rem  HOST 0.0.0.0 = reachable from other machines on the network.
rem  Change to 127.0.0.1 if you want this-machine-only.
rem ---------------------------------------------------------------------

call :find_python
if errorlevel 1 goto :no_python

set "VENV=%~dp0.venv"
if not exist "%VENV%\Scripts\python.exe" (
    echo First-time setup: creating Python environment...
    %PYTHON% -m venv "%VENV%"
    if errorlevel 1 goto :venv_fail
)

fc /b "%VENV%\installed_requirements.txt" "requirements.txt" >nul 2>&1
if errorlevel 1 (
    echo Installing packages - one-time, may take a few minutes...
    "%VENV%\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
    "%VENV%\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :pip_fail
    copy /y "requirements.txt" "%VENV%\installed_requirements.txt" >nul
)

echo.
echo   Oti-Warehouse Swag backend starting...
echo   Ordering page:  http://localhost:%PORT%
echo   Stop the server: close this window or press Ctrl+C.
echo.

rem Opens the ordering page once the server answers /health (waits up to 60s).
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 60;$i++){try{Invoke-WebRequest 'http://127.0.0.1:%PORT%/health' -UseBasicParsing -TimeoutSec 1|Out-Null;Start-Process 'http://localhost:%PORT%';break}catch{Start-Sleep 1}}"

"%VENV%\Scripts\python.exe" -m uvicorn backend.main:app --host %HOST% --port %PORT%

echo.
echo   Server stopped.
goto :end_pause

:find_python
py -3 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
    exit /b 0
)
python --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python"
    exit /b 0
)
exit /b 1

:no_python
echo.
echo   Python was not found on this machine.
echo   Install it from https://www.python.org/downloads/
echo   IMPORTANT: check "Add python.exe to PATH" during install,
echo   then double-click this file again.
goto :end_pause

:venv_fail
echo.
echo   Could not create the Python environment (.venv folder).
echo   Try deleting the .venv folder and running this again.
goto :end_pause

:pip_fail
echo.
echo   Package install failed - see the messages above.
echo   Usually a network hiccup; run this file again to retry.
goto :end_pause

:end_pause
echo.
pause
endlocal
