#!/bin/sh
set -eu

echo "🔍 Verificando GPU disponible..."
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  export OLLAMA_USE_GPU=1
  echo "✅ GPU detectada"
else
  export OLLAMA_USE_GPU=0
  echo "⚙️ Ejecutando en CPU"
fi

echo "🏁 Iniciando servidor Ollama..."

# 👉 AGREGADO: levantar ollama en background
ollama serve &
PID=$!

# 👉 AGREGADO: esperar a que esté listo
echo "⏳ Esperando a que Ollama esté listo..."
for i in $(seq 1 30); do
  if ollama list >/dev/null 2>&1; then
    echo "✅ Ollama listo"
    break
  fi
  sleep 1
done

echo "🔍 Verificando modelo ${OLLAMA_MODEL:-deepseek-r1:latest}..."

MODEL="${OLLAMA_MODEL:-deepseek-r1:latest}"

if ! ollama list | grep -q "$MODEL"; then
  echo "⬇️ Descargando modelo..."
  ollama pull "$MODEL"
else
  echo "✅ Modelo ya disponible"
fi

# 👉 AGREGADO: detener background
kill $PID

# 👉 ORIGINAL (no se toca)
exec ollama serve