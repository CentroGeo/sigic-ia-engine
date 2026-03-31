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
echo "🔍 Verificando modelo ${OLLAMA_MODEL:-deepseek-r1:latest}..."

MODEL="${OLLAMA_MODEL:-deepseek-r1:latest}"

if ! ollama list | grep -q "$MODEL"; then
  echo "⬇️ Descargando modelo..."
  ollama pull "$MODEL"
else
  echo "✅ Modelo ya disponible"
fi

exec ollama serve