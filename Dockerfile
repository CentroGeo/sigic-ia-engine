# Dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app


RUN apt-get update && \
    apt-get install -y netcat-openbsd && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements/base.txt .
RUN pip install --upgrade pip && pip install -r base.txt



COPY . .

RUN chmod +x scripts/entrypoint.sh

CMD ["gunicorn", "llm.wsgi:application", "--bind", "0.0.0.0:8000"]