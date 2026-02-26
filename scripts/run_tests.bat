@echo off
cd /d %~dp0\..
call .venv\Scripts\activate.bat
pytest -q
pause
