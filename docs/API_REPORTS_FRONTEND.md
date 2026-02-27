# API de Reportes — Guía para Frontend

Versión: 2.0 | Fecha: 2026-02-26

---

## 1. Cambios respecto a la versión anterior

| Campo / Comportamiento | v1 | v2 |
|---|---|---|
| `download_url` | Siempre apuntaba a `/media/reports/…` | Apunta a GeoNode si el archivo fue subido exitosamente; fallback a `/media/…` si no |
| `geonode_id` | No existía | Nuevo. ID numérico del documento en GeoNode (null si guardado local) |
| `geonode_url` | No existía | Nuevo. URL de descarga directa desde GeoNode (null si guardado local) |
| `file_format = "pptx"` | Reservado, sin soporte async | Totalmente soportado en flujo asíncrono Celery |
| `file_path` | Siempre presente al completar | Puede ser null cuando el archivo fue subido a GeoNode |

> **Regla de oro:** usa siempre `download_url`. Ese campo ya encapsula la lógica de prioridad GeoNode / local. No construyas URLs manuales desde `file_path`.

---

## 2. Autenticación

Todos los endpoints requieren el header:

```
Authorization: Bearer <JWT>
```

El token JWT (emitido por Keycloak) sirve para dos propósitos:
1. Autenticarse en la API de reportes.
2. Subir el reporte generado a GeoNode en nombre del usuario.

Si el token está ausente o es inválido, el reporte igualmente se genera y se guarda en el almacenamiento local del servidor (fallback). El status será `done`, no `error`.

---

## 3. Campos nuevos en la respuesta

Los siguientes campos están disponibles en `GET /api/reports/{id}/` y `GET /api/reports/`:

| Campo | Tipo | Descripción | Cuándo es null |
|---|---|---|---|
| `geonode_id` | integer | ID numérico del documento en GeoNode | Token inválido/ausente, GeoNode no disponible, o reporte creado antes de v2 |
| `geonode_url` | string (URL) | URL de descarga directa desde GeoNode | Igual que `geonode_id` |
| `download_url` | string (URL) | **URL recomendada para descargar.** GeoNode si disponible, local si no | null solo mientras `status = "pending"` o `"processing"` |

---

## 4. Lógica de `download_url`

```
si geonode_url != null  →  download_url = geonode_url   (GeoNode, prioridad alta)
sino si file_path != null  →  download_url = <base>/media/<file_path>  (local)
sino  →  download_url = null  (reporte aún no completado)
```

No es necesario que el frontend implemente esta lógica: `download_url` ya viene calculado desde el backend.

---

## 5. Flujo de generación — estados del reporte

```
POST /api/reports/generate/
        │
        ▼
  status = "pending"       ← respuesta inmediata (HTTP 202)
        │
        ▼  (Celery task inicia)
  status = "processing"
        │
        ├─── LLM + render OK ──► intento GeoNode upload
        │                              │
        │                  ┌──── OK ───┴─── fallo/sin token ────┐
        │                  ▼                                     ▼
        │           geonode_id ≠ null                   file_path ≠ null
        │           geonode_url ≠ null                  geonode_url = null
        │                  │                                     │
        └── excepción ─────┴─────────────────────────────────────┤
                  │                                              ▼
                  ▼                                       status = "done"
           status = "error"
           error_message = "..."
```

El frontend debe hacer **polling** sobre `GET /api/reports/{id}/` hasta que `status` sea `"done"` o `"error"`.

**Intervalo recomendado:** cada 5 s durante los primeros 2 min; luego cada 15 s hasta máximo 30 min.

---

## 6. Formatos de archivo soportados

Todos los formatos son procesados de forma **asíncrona** (Celery):

| `file_format` | Extensión | Content-Type | Descripción |
|---|---|---|---|
| `"pdf"` | `.pdf` | `application/pdf` | PDF con estilos institucionales opcionales |
| `"word"` | `.docx` | `application/vnd.openxmlformats-officedocument.wordprocessingml.document` | Documento Word |
| `"csv"` | `.csv` | `text/csv` | Datos tabulares |
| `"pptx"` | `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` | Presentación PowerPoint generada con IA |

---

## 7. Referencia de endpoints

### 7.1 Crear reporte

```
POST /api/reports/generate/
Authorization: Bearer <JWT>
Content-Type: application/json
```

**Body:**

```json
{
  "context_id": 5,
  "file_ids": [13, 14, 15],
  "report_name": "Análisis Convocatoria SNII 2025",
  "report_type": "summary",
  "file_format": "pdf",
  "output_format": "markdown",
  "instructions": "Enfocarse en requisitos de elegibilidad.",
  "use_letterhead": false
}
```

| Campo | Tipo | Obligatorio | Valores válidos |
|---|---|---|---|
| `context_id` | integer | Sí | ID de contexto existente |
| `file_ids` | array[int] | Sí (mín. 1) | IDs de archivos del contexto |
| `report_name` | string | Sí | Max 255 chars |
| `report_type` | string | Sí | `institutional`, `descriptive`, `summary`, `evaluation` |
| `file_format` | string | No (def. `pdf`) | `pdf`, `word`, `csv`, `pptx` |
| `output_format` | string | No (def. `markdown`) | `markdown`, `plain_text` |
| `instructions` | string | No | Instrucciones adicionales para el LLM |
| `use_letterhead` | boolean | No (def. `false`) | Aplicar membrete institucional (solo PDF) |

**Respuesta HTTP 202:**

```json
{
  "report_id": 42,
  "task_id": "abc123-...",
  "status": "pending"
}
```

### 7.2 Consultar reporte (polling)

```
GET /api/reports/{id}/
Authorization: Bearer <JWT>
```

**Respuesta HTTP 200 (procesando):**

```json
{
  "id": 42,
  "status": "processing",
  "download_url": null,
  "geonode_id": null,
  "geonode_url": null,
  "file_path": null,
  ...
}
```

**Respuesta HTTP 200 (listo — subido a GeoNode):**

```json
{
  "id": 42,
  "context": 5,
  "report_name": "Análisis Convocatoria SNII 2025",
  "report_type": "summary",
  "file_format": "pdf",
  "output_format": "markdown",
  "status": "done",
  "task_id": "abc123-...",
  "file_path": null,
  "geonode_id": 317,
  "geonode_url": "https://geonode.dev.geoint.mx/documents/317/download",
  "download_url": "https://geonode.dev.geoint.mx/documents/317/download",
  "error_message": null,
  "created_date": "2026-02-26T10:00:00Z",
  "updated_date": "2026-02-26T10:02:30Z"
}
```

**Respuesta HTTP 200 (listo — fallback local):**

```json
{
  "id": 42,
  "status": "done",
  "file_path": "reports/5/42/analisis-2026_0226_100230.pdf",
  "geonode_id": null,
  "geonode_url": null,
  "download_url": "http://localhost:8000/media/reports/5/42/analisis-2026_0226_100230.pdf",
  ...
}
```

**Respuesta HTTP 200 (error):**

```json
{
  "id": 42,
  "status": "error",
  "error_message": "Timeout al llamar al LLM",
  "download_url": null,
  ...
}
```

### 7.3 Listar reportes

```
GET /api/reports/
Authorization: Bearer <JWT>
```

Filtros opcionales (query params): `context_id`, `report_type`, `file_format`, `output_format`, `status`, `date_from` (YYYY-MM-DD), `date_to` (YYYY-MM-DD).

**Respuesta HTTP 200:**

```json
[
  {
    "id": 42,
    "report_name": "Análisis Convocatoria SNII 2025",
    "report_type": "summary",
    "file_format": "pdf",
    "status": "done",
    "geonode_id": 317,
    "geonode_url": "https://geonode.dev.geoint.mx/documents/317/download",
    "created_date": "2026-02-26T10:00:00Z",
    "download_url": "https://geonode.dev.geoint.mx/documents/317/download"
  }
]
```

---

## 8. Ejemplo completo — Reporte PPTX

### Paso 1: Solicitar la presentación

```bash
curl -X POST http://localhost:8000/api/reports/generate/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "context_id": 5,
    "file_ids": [13, 14],
    "report_name": "Resultados PECITI 2025",
    "report_type": "institutional",
    "file_format": "pptx",
    "instructions": "Destacar indicadores de impacto y cobertura nacional."
  }'
```

**Respuesta:**

```json
{"report_id": 43, "task_id": "xyz789-...", "status": "pending"}
```

### Paso 2: Polling hasta completar

```bash
# Repetir hasta status != "pending" y != "processing"
curl http://localhost:8000/api/reports/43/ \
  -H "Authorization: Bearer $TOKEN"
```

### Paso 3: Descargar

Una vez `status = "done"`, usar `download_url` del response.

```bash
curl -L "$DOWNLOAD_URL" -o resultados-peciti-2025.pptx
```

### Ejemplo en JavaScript (polling con async/await)

```js
async function waitForReport(reportId, token, maxWaitMs = 1_800_000) {
  const headers = { Authorization: `Bearer ${token}` };
  const start = Date.now();
  let interval = 5_000;

  while (Date.now() - start < maxWaitMs) {
    await new Promise(r => setTimeout(r, interval));
    const res = await fetch(`/api/reports/${reportId}/`, { headers });
    const data = await res.json();

    if (data.status === "done") return data.download_url;
    if (data.status === "error") throw new Error(data.error_message);

    // Aumentar intervalo después de 2 min
    if (Date.now() - start > 120_000) interval = 15_000;
  }
  throw new Error("Timeout esperando el reporte");
}
```

---

## 9. Manejo de errores

| HTTP | Causa | Acción recomendada |
|---|---|---|
| 400 | Body inválido (campo faltante, file_ids no pertenecen al contexto) | Mostrar `detail` al usuario |
| 401 | Token ausente o expirado | Redirigir a login |
| 403 | Intentar acceder a reporte de otro usuario | Mostrar error de permisos |
| 404 | `report_id` no existe | Mostrar "Reporte no encontrado" |
| 202 | Reporte aceptado y en cola | Comenzar polling |

### Fallback GeoNode (no es un error del reporte)

Si GeoNode no está disponible o el token no tiene permisos para subir, el reporte igualmente se completa con `status = "done"` y el archivo se sirve desde el servidor de la IA. El frontend **no necesita manejar este caso de forma especial**: `download_url` siempre tendrá una URL válida cuando `status = "done"`.

Para distinguir si el reporte está en GeoNode o local:

```js
const isInGeoNode = data.geonode_id !== null;
```

---

## 10. Notas adicionales

- Los reportes están **filtrados por usuario**: cada usuario solo ve los propios.
- Los reportes existentes creados antes de v2 tendrán `geonode_id = null` y `geonode_url = null`; su `download_url` seguirá funcionando vía ruta local.
- El tiempo de generación varía entre **30 s y 10 min** según el tamaño de los documentos y la carga del LLM.
- El `task_id` (Celery) puede usarse para diagnóstico interno; el frontend no necesita usarlo.
