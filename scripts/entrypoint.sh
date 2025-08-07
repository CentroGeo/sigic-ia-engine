#!/bin/bash

echo "Esperando a que la base de datos esté disponible..."

while ! nc -z db 5432; do
  sleep 1
done

echo "Base de datos disponible, ejecutando migraciones..."

python manage.py migrate --noinput

python manage.py collectstatic --noinput

echo "Iniciando el servidor Gunicorn..."
exec gunicorn llm.wsgi:application \
  --bind 0.0.0.0:8001 --timeout 600 --workers=1 --threads=2 \
  --env DJANGO_SETTINGS_MODULE=llm.settings.dev
