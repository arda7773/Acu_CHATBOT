#!/bin/bash
set -e

echo "=== ACU ChatBot Starting ==="

echo "[1/6] Waiting for PostgreSQL..."
until nc -z db 5432; do
  sleep 1
done
echo "PostgreSQL is ready!"

echo "[2/6] Running migrations..."
python manage.py makemigrations chat scraper
python manage.py migrate

echo "[3/6] Collecting static files..."
python manage.py collectstatic --no-input

echo "[4/6] Waiting for Ollama..."
until curl -s http://ollama:11434/api/tags > /dev/null 2>&1; do
  sleep 3
done
echo "Ollama is ready!"

echo "[5/5] Pulling llama3.2:3b model (background)..."
python manage.py pull_model &

echo "=== Starting Gunicorn on port 8000 ==="
exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 2 \
  --timeout 120 \
  --access-logfile - \
  --error-logfile -
