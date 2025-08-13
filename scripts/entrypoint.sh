#!/bin/bash

set -e  # Salir si hay error

# Cargar variables de entorno desde .env si existe
[ -f .env ] && export $(grep -v '^#' .env | xargs)

echo "Entorno Django: ${DJANGO_ENV:-dev}"

# Usar 'dev' como valor por defecto si no se define DJANGO_ENV
DJANGO_SETTINGS="llm.settings.${DJANGO_ENV:-dev}"


echo "Esperando a que la base de datos esté disponible..."

# Esperar hasta que la DB responda en el puerto
until nc -z "$DB_HOST" "$DB_PORT"; do
  echo "Base de datos no disponible, esperando..."
  sleep 1
done


echo "Base de datos disponible, ejecutando migraciones..."
#python manage.py makemigrations --noinput
python manage.py migrate --noinput

#python manage.py collectstatic --noinput

echo "Iniciando el servidor Gunicorn..."
exec gunicorn llm.wsgi:application \
  --bind 0.0.0.0:8001 --timeout 600 --workers=1 --threads=2 \
  --env DJANGO_SETTINGS_MODULE=$DJANGO_SETTINGS
