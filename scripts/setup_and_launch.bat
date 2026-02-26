@echo off
setlocal enableextensions enabledelayedexpansion

set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%..") do set ROOT_DIR=%%~fI
cd /d "%ROOT_DIR%"

echo [1/10] Detecting Python...
set PY_CMD=
where py >nul 2>nul && set PY_CMD=py -3
if "%PY_CMD%"=="" (
  where python >nul 2>nul && set PY_CMD=python
)
if "%PY_CMD%"=="" (
  echo Python 3.11+ is required.
  echo Install from https://www.python.org/downloads/windows/ then rerun this file.
  pause
  exit /b 1
)

echo [2/10] Creating virtual environment if missing...
if not exist .venv (
  %PY_CMD% -m venv .venv
  if errorlevel 1 goto :fail
)

echo [3/10] Activating virtual environment...
call .venv\Scripts\activate.bat
if errorlevel 1 goto :fail

echo [4/10] Upgrading pip tooling...
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :fail

echo [5/10] Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 goto :fail

echo [6/10] Ensuring folders...
if not exist data\db mkdir data\db
if not exist data\logs mkdir data\logs
if not exist data\exports mkdir data\exports

echo [7/10] Ensuring .env...
if not exist .env copy .env.template .env >nul

echo [8/10] Bootstrapping database and defaults...
python scripts\bootstrap.py
if errorlevel 1 goto :fail

echo [9/10] Running environment checks...
python scripts\verify_env.py
if errorlevel 1 goto :fail

echo [10/10] Launching GUI...
python -m app.main
if errorlevel 1 goto :fail

exit /b 0

:fail
echo.
echo Setup or launch failed. Please review the messages above.
pause
exit /b 1
