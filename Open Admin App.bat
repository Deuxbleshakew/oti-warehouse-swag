@echo off
setlocal
title Oti-Warehouse Swag - Admin App
cd /d "%~dp0"

rem =====================================================================
rem  Oti-Warehouse Swag - Open Admin App
rem  Double-click to open the admin desktop app (Pending Orders /
rem  Inventory / Users). On the login screen, Server can be your Render
rem  URL or http://localhost:8000 if the local backend is running.
rem  First run creates a private Python environment (.venv) and installs
rem  packages - takes a few minutes once, instant after that.
rem =====================================================================

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

if exist "%VENV%\Scripts\pythonw.exe" (
    start "" "%VENV%\Scripts\pythonw.exe" "admin_app\main.py"
) else (
    start "" "%VENV%\Scripts\python.exe" "admin_app\main.py"
)
goto :end

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
:end
endlocal
