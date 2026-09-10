@echo off
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Create it and install requirements.txt first.
  pause
  exit /b 1
)
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env from .env.example
  echo Fill TELEGRAM_BOT_TOKEN and LLM_API_KEY, then run this file again.
  pause
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0main.py"
if errorlevel 1 pause
