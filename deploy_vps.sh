#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
PORT="${PORT:-8080}"
WORKERS="${WORKERS:-3}"

echo "==> [1/6] Verificando configuración (.env)..."
if [ ! -f .env ]; then
  cp .env.example .env
  echo "    Se creó .env a partir de .env.example. Revisa DATABASE_URL y SECRET_KEY."
fi
sed -i 's/^DEBUG=.*/DEBUG=False/' .env

echo "==> [2/6] Preparando entorno virtual (.venv)..."
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate

echo "==> [3/6] Instalando dependencias..."
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "==> [4/6] Preparando sesiones y archivos estáticos..."
python manage.py migrate --noinput
python manage.py collectstatic --noinput

echo "==> [5/6] Ejecutando verificación del sistema..."
python manage.py check

echo "==> [6/6] Iniciando servidor Gunicorn en 0.0.0.0:${PORT}..."
pkill -f "gunicorn.*core.wsgi:application" 2>/dev/null || true

if [ "${1:-}" = "--fg" ]; then
  exec gunicorn core.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers "${WORKERS}" \
    --timeout 60 \
    --access-logfile - \
    --error-logfile -
else
  gunicorn core.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers "${WORKERS}" \
    --timeout 60 \
    --access-logfile gunicorn.log \
    --error-logfile gunicorn.log \
    --daemon
  echo "Listo: Minimarket Web corriendo en segundo plano en http://0.0.0.0:${PORT}"
  echo "Logs disponibles en: $(pwd)/gunicorn.log"
fi

