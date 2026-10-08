#!/bin/bash
# Start script for Mock AI Interview (development)
#
# The app creates its own schema on startup: SQLite locally, PostgreSQL as soon
# as DATABASE_URL is set. Production runs under gunicorn instead - see
# render.yaml and PRODUCTION_DEPLOYMENT.md.

cd "$(dirname "$0")"

if [ ! -f .env ]; then
    echo "NOTE: no .env found - copying .env.example to .env (placeholders only)."
    cp .env.example .env
fi

# Start the Flask server. It serves the frontend AND the API, so open
# http://localhost:5000 rather than opening the HTML files directly.
cd backend && python app.py