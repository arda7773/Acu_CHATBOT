#!/bin/bash
set -e

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"
WAIT_FOR_DB="${WAIT_FOR_DB:-true}"
WAIT_FOR_OLLAMA="${WAIT_FOR_OLLAMA:-true}"
LOAD_SEED_DATA="${LOAD_SEED_DATA:-true}"
PULL_MODELS_ON_START="${PULL_MODELS_ON_START:-true}"
OLLAMA_URL="${OLLAMA_URL:-http://ollama:11434}"
USE_DJANGO_RUNSERVER="${USE_DJANGO_RUNSERVER:-false}"

echo "=== ACU ChatBot Starting ==="

if [ "$WAIT_FOR_DB" = "true" ]; then
  echo "[1/7] Waiting for PostgreSQL..."
  until nc -z "$DB_HOST" "$DB_PORT"; do
    sleep 1
  done
  echo "PostgreSQL is ready!"
else
  echo "[1/7] Skipping PostgreSQL wait."
fi

echo "[2/7] Enabling pgvector extension..."
python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.db import connection
with connection.cursor() as c:
    c.execute('CREATE EXTENSION IF NOT EXISTS vector;')
print('pgvector extension ready.')
" || true

echo "[3/7] Running migrations..."
python manage.py migrate
python manage.py check

echo "[4/7] Collecting static files..."
python manage.py collectstatic --no-input

if [ "$LOAD_SEED_DATA" = "true" ]; then
  echo "[5/7] Loading seed data into database..."
  python manage.py load_acu_data --file acu_data.json
else
  echo "[5/7] Skipping seed data load."
fi

if [ "$WAIT_FOR_OLLAMA" = "true" ]; then
  echo "[6/7] Waiting for Ollama..."
  until curl -s "$OLLAMA_URL/api/tags" > /dev/null 2>&1; do
    sleep 3
  done
  echo "Ollama is ready!"
else
  echo "[6/7] Skipping Ollama wait."
fi

if [ "$PULL_MODELS_ON_START" = "true" ]; then
  echo "[7/7] Pulling models (background)..."
  python manage.py pull_model &
else
  echo "[7/7] Skipping model pull."
fi

if [ "$USE_DJANGO_RUNSERVER" = "true" ]; then
  echo "=== Starting Django development server on port 8000 ==="
  exec python manage.py runserver 0.0.0.0:8000
fi

echo "=== Starting Gunicorn on port 8000 ==="
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 2 \
  --timeout 300 \
  --access-logfile - \
  --error-logfile -
