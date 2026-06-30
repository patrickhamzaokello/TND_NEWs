#!/bin/bash

set -euo pipefail

echo "📦 Collecting static files"
python manage.py collectstatic --noinput || {
    echo "❌ Failed to collect static files"; exit 1;
}

python manage.py migrate --noinput || {
    echo "❌ Failed to migrate files"; exit 1;
}

echo "🚀 Starting Gunicorn (Uvicorn ASGI workers — serves HTTP + WebSocket)"
exec gunicorn TNDNEWS.asgi:application \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:6200 \
  --workers 3 \
  --access-logfile - \
  --error-logfile -
