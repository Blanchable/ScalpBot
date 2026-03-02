@echo off
cd /d %~dp0\..
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt --upgrade
pause
