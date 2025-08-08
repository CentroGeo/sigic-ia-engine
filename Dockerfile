# Dockerfile
FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt .

# Instala torch versión CPU estable (optimizado)
#RUN pip install torch==2.3.1+cpu -f https://download.pytorch.org/whl/cpu/torch_stable.html

RUN pip install --no-cache-dir -r base.txt
#RUN pip install --no-cache-dir sentence-transformers==2.2.2

# Precarga modelo
#RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY . .

EXPOSE 8001

ENTRYPOINT ["sh", "./scripts/entrypoint.sh"]
