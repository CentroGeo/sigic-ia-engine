# API de Generación de Reportes — Documentación Frontend

**Base URL:** `http://localhost:8000` (dev) / `https://ia.dev.geoint.mx` (staging)
**Formato:** JSON
**Autenticación:** Bearer JWT (Keycloak) en los endpoints de reportes

---

## Índice

1. [Autenticación](#1-autenticación)
2. [Flujo completo recomendado](#2-flujo-completo-recomendado)
3. [Workspaces](#3-workspaces)
4. [Contextos](#4-contextos)
5. [Archivos](#5-archivos)
6. [Reportes](#6-reportes)
7. [Referencia de tipos y enumeraciones](#7-referencia-de-tipos-y-enumeraciones)
8. [Ejemplos cURL](#8-ejemplos-curl)
9. [Manejo de errores](#9-manejo-de-errores)

---

## 1. Autenticación

Los endpoints de **Reportes** (excepto `/pptx/`) requieren un JWT de Keycloak en el header:

```
Authorization: Bearer <token>
```

El `user_id` del token (campo `email`) se usa automáticamente para filtrar y asociar reportes.

Los endpoints de **Fileuploads** no requieren JWT, solo el parámetro `user_id` en la query string.

---

## 2. Flujo completo recomendado

```
1. Crear workspace        → POST /api/fileuploads/workspaces/admin/create
2. Crear contexto         → POST /api/fileuploads/workspaces/admin/contexts/create
3. Subir archivos PDF     → POST /api/fileuploads/workspaces/admin/contexts/files/create
   (el sistema procesa los embeddings automáticamente en segundo plano)
4. Solicitar reporte      → POST /api/reports/generate/
5. Consultar estado       → GET  /api/reports/{id}/
   (repetir hasta status = "done")
6. Descargar archivo      → GET  download_url  (cuando status = "done")
```

---

## 3. Workspaces

### 3.1 Listar workspaces del usuario

```
POST /api/fileuploads/workspaces/user?user_id={email}
```

**Response:**
```json
[
  {
    "id": 1,
    "title": "Proyecto SECIHTI 2024",
    "description": "Análisis de convocatorias",
    "user_id": "investigador@secihti.mx",
    "active": true,
    "public": false,
    "created_date": "2024-03-01T10:00:00Z",
    "numero_fuentes": 12,
    "numero_contextos": 3
  }
]
```

---

### 3.2 Crear workspace

```
POST /api/fileuploads/workspaces/admin/create?user_id={email}
Content-Type: multipart/form-data
```

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `title` | string | ✅ | Nombre del workspace |
| `description` | string | ❌ | Descripción |
| `public` | boolean | ❌ | Visibilidad pública (default: false) |

**Response:**
```json
{
  "id": 1,
  "saved": true,
  "files_uploaded": false,
  "uploaded_files": []
}
```

---

### 3.3 Listar contextos de un workspace

```
POST /api/fileuploads/workspaces/admin/{workspace_id}/contexts?user_id={email}
```

**Response:**
```json
[
  {
    "id": 2,
    "title": "Convocatorias 2024",
    "description": "Documentos de convocatorias vigentes",
    "num_files": 5
  }
]
```

---

## 4. Contextos

### 4.1 Crear contexto

```
POST /api/fileuploads/workspaces/admin/contexts/create?user_id={email}
Content-Type: multipart/form-data
```

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `proyecto_id` | integer | ✅ | ID del workspace padre |
| `nombre` | string | ✅ | Nombre del contexto |
| `descripcion` | string | ❌ | Descripción del contexto |
| `fuentes` | JSON string | ❌ | IDs de archivos a asociar, ej: `"[1,2,3]"` |
| `file` | file | ❌ | Imagen de portada |

**Response:**
```json
{
  "id": 2,
  "saved": true,
  "uploaded_file": {
    "name": "portada.jpg",
    "type": "image/jpeg",
    "size": 45231,
    "path": "/media/uploads/..."
  }
}
```

---

### 4.2 Listar archivos de un contexto

```
POST /api/fileuploads/workspaces/admin/{workspace_id}/contexts/{context_id}/files?user_id={email}
```

**Response:**
```json
[
  {
    "id": 3,
    "filename": "convocatoria_2024.pdf",
    "document_type": "pdf",
    "geonode_id": null,
    "processed": true
  }
]
```

---

## 5. Archivos

### 5.1 Subir archivo a un contexto

```
POST /api/fileuploads/workspaces/admin/contexts/files/create?user_id={email}
Content-Type: multipart/form-data
Authorization: Bearer <token>
```

| Campo | Tipo | Requerido | Descripción |
|-------|------|-----------|-------------|
| `file` | file | ✅ | Archivo PDF, DOCX, XLSX, JSON |
| `context_id` | integer | ✅ | ID del contexto destino |
| `title` | string | ❌ | Título descriptivo del archivo |

**Formatos soportados:** PDF, DOCX, XLSX, JSON

**Response:**
```json
{
  "status": "ok",
  "metadata": {
    "id": 3,
    "filename": "informe_anual.pdf",
    "processed": false
  }
}
```

> **Nota:** `processed: false` significa que los embeddings aún se están generando en segundo plano (Celery). El campo cambia a `true` cuando el RAG ya puede usar el archivo.

---

## 6. Reportes

Todos los endpoints de reportes requieren header `Authorization: Bearer <token>`.

---

### 6.1 Crear reporte (asíncrono)

```
POST /api/reports/generate/
Authorization: Bearer <token>
Content-Type: application/json
```

**Request body:**

```json
{
  "context_id": 2,
  "file_ids": [3, 4, 5],
  "report_name": "Análisis de Convocatorias SECIHTI 2024",
  "report_type": "institutional",
  "output_format": "markdown",
  "file_format": "pdf",
  "instructions": "Enfocarse en los requisitos de elegibilidad y montos de financiamiento",
  "use_letterhead": false,
  "text_format": null
}
```

| Campo | Tipo | Requerido | Valores posibles | Default |
|-------|------|-----------|-----------------|---------|
| `context_id` | integer | ✅ | ID de contexto existente | — |
| `file_ids` | integer[] | ✅ | IDs de archivos del contexto | — |
| `report_name` | string | ✅ | Nombre libre (máx. 255 chars) | — |
| `report_type` | string | ✅ | `institutional` \| `descriptive` \| `summary` \| `evaluation` | — |
| `output_format` | string | ❌ | `markdown` \| `plain_text` | `markdown` |
| `file_format` | string | ❌ | `pdf` \| `word` \| `csv` | `pdf` |
| `instructions` | string | ❌ | Instrucciones libres para el LLM | `""` |
| `use_letterhead` | boolean | ❌ | Guardar preferencia de membrete | `false` |
| `text_format` | object \| null | ❌ | Config. de fuente (guardado, no aplicado aún) | `null` |

**Response `202 Accepted`:**
```json
{
  "report_id": 7,
  "task_id": "a3f2c1d4-8b5e-4f9a-b2c1-d4e5f6a7b8c9",
  "status": "pending"
}
```

---

### 6.2 Consultar estado de un reporte

```
GET /api/reports/{report_id}/
Authorization: Bearer <token>
```

**Response:**
```json
{
  "id": 7,
  "context": 2,
  "report_name": "Análisis de Convocatorias SECIHTI 2024",
  "report_type": "institutional",
  "output_format": "markdown",
  "file_format": "pdf",
  "text_format": null,
  "instructions": "Enfocarse en los requisitos de elegibilidad...",
  "use_letterhead": false,
  "user_id": "investigador@secihti.mx",
  "status": "done",
  "task_id": "a3f2c1d4-8b5e-4f9a-b2c1-d4e5f6a7b8c9",
  "file_path": "reports/2/7/analisis-de-convocatorias-secihti-2024.pdf",
  "error_message": null,
  "created_date": "2024-03-15T14:30:00Z",
  "updated_date": "2024-03-15T14:31:42Z",
  "download_url": "http://localhost:8000/media/reports/2/7/analisis-de-convocatorias-secihti-2024.pdf"
}
```

**Ciclo de vida del `status`:**

```
pending → processing → done
                    ↘ error
```

| Status | Descripción |
|--------|-------------|
| `pending` | Tarea en cola, esperando worker Celery |
| `processing` | Worker procesando: RAG + LLM + render |
| `done` | Archivo generado, `download_url` disponible |
| `error` | Fallo; revisar `error_message` |

**Polling recomendado:** cada 3-5 segundos mientras `status` sea `pending` o `processing`.

---

### 6.3 Listar reportes del usuario

```
GET /api/reports/
Authorization: Bearer <token>
```

**Query params opcionales:**

| Parámetro | Tipo | Ejemplo | Descripción |
|-----------|------|---------|-------------|
| `context_id` | integer | `2` | Filtrar por contexto |
| `report_type` | string | `summary` | Filtrar por tipo |
| `file_format` | string | `pdf` | Filtrar por formato de archivo |
| `output_format` | string | `markdown` | Filtrar por formato de contenido |
| `status` | string | `done` | Filtrar por estado |
| `date_from` | date | `2024-01-01` | Desde fecha (YYYY-MM-DD) |
| `date_to` | date | `2024-12-31` | Hasta fecha (YYYY-MM-DD) |

**Response:**
```json
[
  {
    "id": 7,
    "report_name": "Análisis de Convocatorias SECIHTI 2024",
    "report_type": "institutional",
    "file_format": "pdf",
    "status": "done",
    "created_date": "2024-03-15T14:30:00Z",
    "download_url": "http://localhost:8000/media/reports/2/7/analisis-de-convocatorias-secihti-2024.pdf"
  }
]
```

---

### 6.4 Generar presentación PPTX (síncrono, sin auth)

```
POST /api/reports/pptx/
Content-Type: application/json
```

> Este endpoint es síncrono y puede tardar varios minutos. No requiere autenticación.

**Request body:**
```json
{
  "report_name": "Programa Nacional de Ciencia 2024",
  "report_type": "institutional",
  "file_ids": [3, 4],
  "guided_prompt": "Destacar objetivos estratégicos y presupuesto",
  "top_k": 20
}
```

**Response:**
```json
{
  "download_url": "http://localhost:8000/media/reports/programa-nacional-ciencia-2024-abc12345.pptx",
  "filename": "programa-nacional-ciencia-2024-abc12345.pptx"
}
```

---

## 7. Referencia de tipos y enumeraciones

### `report_type`

| Valor | Descripción | Estructura generada |
|-------|-------------|---------------------|
| `institutional` | Reporte formal institucional | Resumen ejecutivo → Antecedentes → Hallazgos → Conclusiones → Recomendaciones |
| `descriptive` | Análisis descriptivo con énfasis en datos | Secciones temáticas con tablas y cifras |
| `summary` | Resumen ejecutivo conciso (≈1 página) | Objetivo → Puntos clave (máx. 5) → Conclusión |
| `evaluation` | Evaluación crítica con criterios | Criterios → Fortalezas → Debilidades → Recomendaciones |

### `output_format`

| Valor | Descripción | Cuándo usarlo |
|-------|-------------|---------------|
| `markdown` | Contenido en Markdown (headings, tablas, listas) | PDF o Word con formato rico |
| `plain_text` | Texto plano sin caracteres especiales | Sistemas legacy, copiar/pegar |

### `file_format`

| Valor | Extensión | Descripción |
|-------|-----------|-------------|
| `pdf` | `.pdf` | PDF generado con WeasyPrint |
| `word` | `.docx` | Documento Word con estilos |
| `csv` | `.csv` | Tabla de datos (el LLM genera CSV directamente) |

> **Nota sobre CSV:** cuando `file_format=csv`, el LLM genera directamente una tabla CSV con los datos extraídos de los documentos. Es útil para datos tabulares como presupuestos, indicadores, etc.

---

## 8. Ejemplos cURL

### Crear workspace y contexto

```bash
# 1. Crear workspace
curl -X POST "http://localhost:8000/api/fileuploads/workspaces/admin/create?user_id=investigador@secihti.mx" \
  -F "title=Proyectos SECIHTI 2024" \
  -F "description=Análisis de programas y convocatorias" \
  -F "public=false"

# 2. Crear contexto (usar workspace_id del paso anterior)
curl -X POST "http://localhost:8000/api/fileuploads/workspaces/admin/contexts/create?user_id=investigador@secihti.mx" \
  -F "proyecto_id=1" \
  -F "nombre=Convocatorias Vigentes" \
  -F "descripcion=PDFs de convocatorias SECIHTI 2024"

# 3. Subir PDF
curl -X POST "http://localhost:8000/api/fileuploads/workspaces/admin/contexts/files/create?user_id=investigador@secihti.mx" \
  -H "Authorization: Bearer <token>" \
  -F "context_id=1" \
  -F "file=@convocatoria_2024.pdf" \
  -F "title=Convocatoria Fronteras 2024"
```

### Generar reporte y hacer polling

```bash
# 4. Solicitar reporte
RESPONSE=$(curl -s -X POST "http://localhost:8000/api/reports/generate/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "context_id": 1,
    "file_ids": [1, 2, 3],
    "report_name": "Análisis Convocatorias SECIHTI 2024",
    "report_type": "institutional",
    "output_format": "markdown",
    "file_format": "pdf",
    "instructions": "Enfocarse en requisitos, montos y fechas límite"
  }')

REPORT_ID=$(echo $RESPONSE | jq -r '.report_id')
echo "Report ID: $REPORT_ID"

# 5. Polling hasta done
while true; do
  STATUS=$(curl -s "http://localhost:8000/api/reports/$REPORT_ID/" \
    -H "Authorization: Bearer <TOKEN>" | jq -r '.status')
  echo "Status: $STATUS"
  if [ "$STATUS" = "done" ] || [ "$STATUS" = "error" ]; then
    break
  fi
  sleep 5
done

# 6. Obtener download_url
curl -s "http://localhost:8000/api/reports/$REPORT_ID/" \
  -H "Authorization: Bearer <TOKEN>" | jq '{status, download_url, error_message}'
```

---

## 9. Manejo de errores

### Códigos HTTP

| Código | Situación |
|--------|-----------|
| `202` | Reporte creado, tarea en cola |
| `200` | Consulta exitosa |
| `400` | Validación fallida (ver `detail` o campos con error) |
| `401` | Token ausente o inválido |
| `403` | El reporte pertenece a otro usuario |
| `404` | Recurso no encontrado |

### Errores de validación típicos

```json
// file_ids fuera del contexto
{
  "file_ids": ["Los siguientes IDs no pertenecen al contexto 2: [99, 100]"]
}

// report_type inválido
{
  "report_type": ["\"presentation\" is not a valid choice."]
}
```

### Error en la generación del reporte

Cuando `status = "error"`, el campo `error_message` contiene la causa:

```json
{
  "status": "error",
  "error_message": "Ollama HTTP 503: model not found",
  "download_url": null
}
```

---

## 10. Notas de implementación para frontend

### Patrón de polling recomendado

```javascript
async function waitForReport(reportId, token, onProgress) {
  const INTERVAL_MS = 4000;
  const MAX_WAIT_MS = 10 * 60 * 1000; // 10 minutos
  const start = Date.now();

  while (Date.now() - start < MAX_WAIT_MS) {
    const res = await fetch(`/api/reports/${reportId}/`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    const data = await res.json();

    onProgress(data.status);

    if (data.status === 'done') return data.download_url;
    if (data.status === 'error') throw new Error(data.error_message);

    await new Promise(r => setTimeout(r, INTERVAL_MS));
  }
  throw new Error('Timeout esperando el reporte');
}
```

### Tiempos de generación aproximados

| Tipo | Archivos (aprox.) | Tiempo estimado |
|------|------------------|-----------------|
| `summary` | 1-3 PDFs | 30-90 segundos |
| `descriptive` | 3-5 PDFs | 1-3 minutos |
| `institutional` | 3-5 PDFs | 2-4 minutos |
| `evaluation` | 5-10 PDFs | 3-6 minutos |

> Los tiempos dependen del servidor Ollama y la longitud de los documentos.

### Relación entre campos

```
output_format=markdown  →  contenido rico (tablas, listas, negritas)
output_format=plain_text →  texto limpio sin símbolos

file_format=pdf   →  el markdown/texto se convierte en PDF
file_format=word  →  el markdown/texto se convierte en DOCX
file_format=csv   →  ignora output_format; el LLM genera CSV directamente
```

### Notas sobre `file_ids`

- Los `file_ids` deben pertenecer al `context_id` enviado; si no, el servidor devuelve 400.
- El usuario puede seleccionar un subconjunto de los archivos del contexto para el reporte.
- El RAG busca los 30 fragmentos más relevantes entre todos los archivos seleccionados.

---

---

## 11. Datos de demostración precargados

El entorno de desarrollo incluye datos reales de SECIHTI listos para pruebas.
Todos los reportes ya están generados (`status=done`) con archivos descargables.

### Contexto A — Convocatorias SECIHTI 2025

| Recurso | ID | Nombre |
|---------|-----|--------|
| Workspace | `5` | Programas y Convocatorias SECIHTI |
| Context | `5` | Convocatorias SECIHTI 2025 |
| File | `13` | Convocatoria Ciencia Básica y de Frontera 2025 |
| File | `14` | Convocatoria Investigación Humanística 2025 |
| File | `15` | Convocatoria SNII 2025 |
| File | `16` | Términos de Referencia Investigación Humanística 2025 |
| Report | `7` | Resumen Ejecutivo — Convocatorias SECIHTI 2025 (`summary/pdf/done`) |
| Report | `8` | Análisis Institucional — Ciencia Básica y SNII 2025 (`institutional/pdf/done`) |
| Report | `9` | Tabla de Requisitos y Criterios SECIHTI 2025 (`descriptive/csv/done`) |

### Contexto B — Becas, SNP y Política Científica

| Recurso | ID | Nombre |
|---------|-----|--------|
| Workspace | `6` | Políticas y Programas de Ciencia 2024-2025 |
| Context | `6` | Becas, SNP y Política Científica |
| File | `17` | Términos de Referencia — Investigación Humanística 2025 |
| File | `18` | Sistema Nacional de Posgrados — Convocatoria 2025 |
| File | `19` | Programa Especial de CTI 2021-2024 (PECITI) |
| Report | `10` | Reporte Institucional — SNP y PECITI 2025 (`institutional/word/done`) |
| Report | `11` | Análisis Descriptivo — Política CTI 2021-2024 (`descriptive/pdf/done`) |
| Report | `12` | Evaluación — Términos de Referencia IH 2025 (`evaluation/pdf/done`) |
| Report | `13` | Resumen Ejecutivo — SNP 2025 (`summary/word/done`) |
| Report | `14` | Tabla Comparativa — Programas de Apoyo SECIHTI 2025 (`descriptive/csv/done`) |

**Total en BD:** 2 workspaces, 2 contextos, 7 archivos, 8 reportes, ~1160 embeddings.

Fuente de los PDFs: [SECIHTI](https://secihti.mx) — documentos públicos oficiales.

### Pruebas rápidas con los datos demo

```bash
BASE="http://localhost:8000"

# Ver reporte #10 (institutional/word, ya generado)
curl -s "$BASE/api/reports/10/" | jq '{id, report_name, status, download_url}'

# Listar todos los reportes
curl -s "$BASE/api/reports/" | jq '[.[] | {id, report_name, status}]'

# Solicitar un nuevo reporte con los archivos del contexto B (requiere token)
curl -s -X POST "$BASE/api/reports/generate/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "context_id": 6,
    "file_ids": [17, 18, 19],
    "report_name": "Análisis de Becas Nacionales",
    "report_type": "evaluation",
    "output_format": "markdown",
    "file_format": "pdf",
    "instructions": "Evaluar los programas de becas disponibles para posgrado nacional."
  }' | jq .
```

### Recargar / resetear datos demo

```bash
# Contexto A (convocatorias SECIHTI)
docker exec ia-engine python manage.py load_secihti_demo --reset

# Contexto B (SNP, PECITI, TDR)
docker exec ia-engine python manage.py load_diverse_demo --reset

# Solo estructura sin embeddings (tests rápidos)
docker exec ia-engine python manage.py load_diverse_demo --reset --skip-embeddings
```

---

*Actualizado el 2026-02-25 — SIGIC IA Engine v1.x*
