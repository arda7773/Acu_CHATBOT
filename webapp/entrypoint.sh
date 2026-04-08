#!/bin/bash
set -e

echo "=== ACU ChatBot Starting ==="

echo "[1/6] Waiting for PostgreSQL..."
until nc -z db 5432; do
  sleep 1
done
echo "PostgreSQL is ready!"

echo "[2/6] Enabling pgvector extension..."
python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.db import connection
with connection.cursor() as c:
    c.execute('CREATE EXTENSION IF NOT EXISTS vector;')
print('pgvector extension ready.')
" || true

echo "[3/6] Running migrations..."
python manage.py migrate
python manage.py check

echo "[4/6] Collecting static files..."
python manage.py collectstatic --no-input

echo "[5/6] Waiting for Ollama..."
until curl -s http://ollama:11434/api/tags > /dev/null 2>&1; do
  sleep 3
done
echo "Ollama is ready!"

echo "[6/6] Pulling models (background)..."
python manage.py pull_model &

echo "=== Starting Gunicorn on port 8000 ==="
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 2 \
  --timeout 300 \
  --access-logfile - \
  --error-logfile -
