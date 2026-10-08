@echo off
REM Start script for Mock AI Interview - Windows version
REM This script initializes the database and starts the Flask server

cd /d "%~dp0"

REM The app initialises its own schema on startup (SQLite locally, PostgreSQL
REM when DATABASE_URL is set), so there is no separate setup step.

REM Start the Flask server
REM  - It serves the frontend AND the API, so open http://localhost:5000
REM  - Production runs under gunicorn instead (see render.yaml)
if not exist ".env" (
    echo NOTE: no .env found - copying .env.example to .env ^(placeholders only^).
    copy ".env.example" ".env" >nul
)
echo Starting Mock AI Interview server on http://localhost:5000
cd backend
python app.py

pause