#!/bin/bash

set -e  # Salir si hay error

echo "Esperando a que la base de datos esté disponible..."

# Esperar hasta que la DB responda en el puerto
until nc -z "$DB_HOST" "$DB_PORT"; do
  echo "Base de datos no disponible, esperando..."
  sleep 1
done

echo "Base de datos disponible, ejecutando migraciones..."

#python manage.py makemigrations
python manage.py makemigrations --noinput
# Ejecutar migraciones
python manage.py migrate

echo "Iniciando el servidor Gunicorn..."
exec gunicorn llm.wsgi:application \
  --bind 0.0.0.0:8001 --timeout 600 --workers=1 --threads=2 \
  --env DJANGO_SETTINGS_MODULE=llm.settings.dev
