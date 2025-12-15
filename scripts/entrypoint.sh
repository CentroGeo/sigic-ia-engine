#!/bin/bash

set -e  # Salir si hay error

# Determinar entorno (por DJANGO_ENV o default "dev")
#DJANGO_ENV=${DJANGO_ENV:-dev}
#ENV_FILE=".env_${DJANGO_ENV}"

# Cargar variables si el archivo existe
#if [ -f "$ENV_FILE" ]; then
#  echo "Cargando variables desde $ENV_FILE"
#  set -o allexport
#  . "$ENV_FILE"
#  set +o allexport
#else
#  echo "⚠️ Archivo de entorno $ENV_FILE no encontrado. Algunas variables pueden faltar."
#fi

echo "Entorno Django: ${DJANGO_ENV}"
DJANGO_SETTINGS="llm.settings.${DJANGO_ENV}"

echo "Esperando a que la base de datos esté disponible..."

# Esperar hasta que la DB responda en el puerto
until nc -z "$DB_HOST" "$DB_PORT"; do
  echo "Base de datos no disponible, esperando..."
  sleep 1
done


echo "Base de datos disponible, ejecutando migraciones..."
python manage.py makemigrations --noinput
python manage.py migrate --noinput

echo "Recopilando archivos estáticos..."
python manage.py collectstatic --noinput

# Iniciar servidor según entorno
if [ "${DJANGO_ENV}" = "dev" ]; then
  echo "▶️ Iniciando servidor de desarrollo con autoreload"
  exec python manage.py runserver 0.0.0.0:8001 --settings=$DJANGO_SETTINGS
else
  echo "⚙ Precargando modelo deepseek-r1:32b desde Ollama en el host..."
  # Esperar a que Ollama esté disponible (máximo 30s)
  for i in {1..30}; do
    if curl -s http://host.docker.internal:11434 > /dev/null; then
      echo "✅ Ollama está disponible"
      break
    fi
    echo "⏳ Esperando a Ollama..."
    sleep 1
  done
  # Hacer una llamada para precargar el modelo
  curl -s http://host.docker.internal:11434/api/generate \
    -d '{
      "model": "deepseek-r1:32b",
      "keep_alive": -1
    }' > /dev/null && echo "✅ Modelo precargado"


  echo "▶️ Iniciando servidor Gunicorn para producción..."
  exec gunicorn llm.wsgi:application \
    --bind 0.0.0.0:8001 --timeout 600 --workers=1 --threads=2 \
    --env DJANGO_SETTINGS_MODULE=$DJANGO_SETTINGS
fi
  
