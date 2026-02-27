# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

SIGIC-IA-Engine is a Django-based AI module for intelligent document processing and semantic search with LLM integration. Part of the SIGIC platform (Sistema de Información Geográfica en la Infraestructura Crítica) by CentroGeo. It uses Ollama for offline/local LLM inference, pgvector for vector similarity search, and integrates with GeoNode (geo-spatial platform) and Keycloak (auth).

## Common Commands

```bash
# Docker development stack (Django + Celery + PostgreSQL + Redis)
docker-compose up --build -d
docker-compose down

# Django dev server (without Docker)
python manage.py runserver 0.0.0.0:8000 --settings=llm.settings.dev

# Migrations
python manage.py makemigrations --noinput
python manage.py migrate --noinput

# Celery worker
celery -A llm worker --loglevel=info

# Cleanup embedding cache
python manage.py cleanup_cache

# Production (Gunicorn)
gunicorn llm.wsgi:application --bind 0.0.0.0:8000 --timeout 600 --workers=1 --threads=2 --env DJANGO_SETTINGS_MODULE=llm.settings.prod
```

No test framework or linter is currently configured.

## Architecture

### Django Apps

- **`llm/`** — Django project config. Settings split into `settings/base.py`, `settings/dev.py`, `settings/prod.py`. Celery configured in `celery.py`.
- **`chat/`** — Chat/query processing. Main endpoint streams responses via SSE. Three prompt modes: `prompt_question.py` (JSON-mode SQL generation), `prompt_keys.py` (metadata-based queries), `prompt_semantico.py` (semantic search).
- **`fileuploads/`** — Document upload, embedding, and workspace/context management. Heavy processing in `utils.py` (text extraction, chunking, embedding). Async tasks in `tasks.py` via Celery. Embedding generation in `embeddings_service.py` (OllamaEmbedder).
- **`shared/`** — `authentication.py` contains KeycloakAuthentication (JWT/RS256 via JWKS).

### Key Data Flow

**Document Upload:** File → text extraction (PDF/DOCX/Excel/JSON) → recursive chunking (512 chars, 50-char overlap) with language detection → batch embedding via Ollama (`mxbai-embed-large`, 768-dim) → stored in `DocumentEmbedding` (pgvector) → async GeoNode metadata update via Celery.

**Chat Query:** User query → semantic search (top-20 relevant chunks via L2Distance) → extract metadata keys → LLM generates SQL over JSONB (`text_json`) → execute query → LLM formats results + insights → SSE stream to client.

### Database Models (fileuploads/models.py, chat/models.py)

- **Workspace** → multi-tenant container, has many Contexts
- **Context** → groups related Files within a Workspace
- **Files** → document metadata, linked to GeoNode via `geonode_id`/`geonode_uuid`
- **DocumentEmbedding** → vector chunks (768-dim pgvector), `text_json` (JSONB), `metadata_json` (JSONB)
- **History** (chat app) → conversation history with `chat` and `history_array` JSONB fields

### Infrastructure

- **Ollama**: Local LLM inference. URL configured via `OLLAMA_PROTO`/`OLLAMA_HOST`/`OLLAMA_PORT` env vars. Default embedding model: `mxbai-embed-large`.
- **PostgreSQL 15.4 + pgvector**: Vector similarity search with L2Distance.
- **Redis**: Celery broker (`redis://redis:6379/0`).
- **Docker services**: `ia-engine` (Django:8000), `modulo-ia-celery`, `modulo-ia-db` (PostgreSQL), `modulo-ia-redis`. Network: `red_geolex`.

### Notable Patterns

- **SSE Streaming**: Chat responses use Server-Sent Events for long-running queries.
- **Thread Locking**: `llm_lock` in `chat/views.py` for Ollama concurrency control.
- **ThreadPoolExecutor**: Batch embedding generation runs in parallel threads.
- **Celery Tasks**: GeoNode metadata updates are non-blocking async tasks with 30-min hard limit.
- **Multi-tenant scoping**: All queries scoped by `context_id` → `workspace`.

## Environment

Copy `.env.example` to `.env`. Key variables: `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DJANGO_SETTINGS_MODULE`, `DJANGO_ENV` (dev/prod), `OLLAMA_PROTO`, `OLLAMA_HOST`, `OLLAMA_PORT`, `GEONODE_SERVER`.

## API Documentation

Swagger UI at `/api/schema/swagger-ui/`, ReDoc at `/api/schema/redoc/`, OpenAPI schema at `/api/schema/`.
