#!/bin/sh

# Entrypoint inteligente para Redis
# Si ya existe un Redis corriendo en el host, este contenedor sale silenciosamente

REDIS_PORT="${REDIS_CHECK_PORT:-6379}"

echo "Verificando si Redis ya está disponible en el host..."

# Esperar un momento para que la red esté lista
sleep 1

# Verificar si hay un Redis corriendo en el host (host.docker.internal)
# Esto detecta si geogpt-load-balancer ya tiene Redis corriendo
if redis-cli -h host.docker.internal -p "$REDIS_PORT" ping 2>/dev/null | grep -q "PONG"; then
    echo "Redis ya está corriendo en host.docker.internal:$REDIS_PORT"
    echo "Este contenedor no es necesario, saliendo..."
    # Mantener el contenedor vivo pero sin hacer nada
    # Esto evita que Docker lo reinicie constantemente
    echo "Entrando en modo standby..."
    tail -f /dev/null
fi

echo "No se detectó Redis existente, iniciando servidor Redis..."
exec redis-server --protected-mode no
