import io
import os
import requests
from datetime import datetime

from celery import shared_task
from django.conf import settings
from django.utils.text import slugify

from reports.models import Report
from reports.prompts.base_prompt import build_prompt
from reports.services.ollama_client import ollama_chat
from fileuploads.views import optimized_rag_search_files


# ---------------------------------------------------------------------------
# Helper de subida a GeoNode
# ---------------------------------------------------------------------------

def _upload_to_geonode(
    file_bytes: bytes,
    filename: str,
    content_type: str,
    title: str,
    authorization: str,
) -> dict | None:
    """
    Sube `file_bytes` a GeoNode y devuelve ``{"geonode_id": int, "geonode_url": str}``.
    Retorna ``None`` si no hay token, si GeoNode falla o si ocurre cualquier excepción.
    El llamador debe implementar el fallback local cuando recibe None.

    Requiere un JWT de Keycloak en `authorization` (mismo que el frontend envía en
    Authorization: Bearer <jwt>). GeoNode valida el JWT en /documents/upload.
    Un token de API de solo lectura (GeoNode REST API key) NO es suficiente.
    """

    print("#########_upload_to_geonode#############", flush=True)
    if not authorization:
        return None

    try:
        from fileuploads.utils import upload_file_to_geonode, get_geonode_document_uuid_by_id

        file_obj = io.BytesIO(file_bytes)
        file_obj.name = filename
        file_obj.content_type = content_type

        response = upload_file_to_geonode(file_obj, authorization, title=title)

        if response.status_code in (401, 403):
            print(f"[REPORT] GeoNode auth error ({response.status_code}), fallback local")
            return None
        if response.status_code >= 400:
            print(f"[REPORT] GeoNode upload error ({response.status_code}), fallback local")
            return None

        response_data = response.json()
        doc_url = response_data.get("url", "")
        if not doc_url:
            # La respuesta no tiene 'url' → probablemente devolvió formulario de login
            print("[REPORT] GeoNode upload: respuesta inesperada (sin campo 'url'), fallback local")
            return None

        doc_id = doc_url.strip("/").split("/")[-1]

        # La subida fue exitosa; intentar obtener la URL de descarga via metadata API.
        # Si esa llamada falla (DNS transitorio, timeout), construimos la URL desde el
        # patrón conocido para no perder la subida ya realizada.
        geonode_url = None
        try:
            meta = get_geonode_document_uuid_by_id(doc_id, authorization=authorization)
            geonode_url = meta.get("url_download")
        except Exception as meta_exc:
            print(f"[REPORT] get_geonode_document_uuid_by_id falló ({meta_exc}), usando URL por patrón")

        if not geonode_url:
            geonode_base = os.getenv("GEONODE_SERVER", "").rstrip("/")
            geonode_url = f"{geonode_base}/documents/{doc_id}/download"

        print(f"[REPORT] GeoNode upload OK: doc_id={doc_id}, url={geonode_url}")
        return {"geonode_id": int(doc_id), "geonode_url": geonode_url}

    except Exception as exc:
        print(f"[REPORT] _upload_to_geonode exception: {exc}, fallback local")
        return None


def upload_file_to_geonode(file, filename, token=""):
    print("#########upload_file_to_geonode##############", flush=True)
    file_bytes = file.read()
    files = {"file": (filename, file_bytes, file.content_type)}
    data = {"category": "contextos"}
    headers = {"Authorization": token}

    geonode_base_url = os.environ.get("GEONODE_SERVER")
    upload_url = f"{geonode_base_url}/sigic/ia/mediauploads/upload"

    response = requests.post(
        upload_url, files=files, data=data, headers=headers, timeout=30
    )

    return response



# ---------------------------------------------------------------------------
# Helpers internos de guardado local
# ---------------------------------------------------------------------------

def _save_local(file_bytes: bytes, filename: str, context_id: int, report_id: int) -> str:
    """Guarda bytes en MEDIA_ROOT y devuelve la ruta relativa."""
    rel_dir = os.path.join("reports", str(context_id), str(report_id))
    abs_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
    os.makedirs(abs_dir, exist_ok=True)
    abs_path = os.path.join(abs_dir, filename)
    with open(abs_path, "wb") as f:
        f.write(file_bytes)
    return os.path.join(rel_dir, filename).replace("\\", "/")


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@shared_task(
    bind=True,
    name="reports.generate_report",
    time_limit=1800,
    soft_time_limit=1500,
)
def generate_report_task(self, report_id: int, base_url: str, authorization: str = "") -> dict:
    """
    Tarea Celery que genera el contenido del reporte con el LLM y lo persiste.
    Intenta subir a GeoNode usando `authorization`; si falla guarda en disco local.
    """
    report = Report.objects.select_related("context").get(pk=report_id)
    report.status = "processing"
    report.task_id = self.request.id
    report.save(update_fields=["status", "task_id", "updated_date"])

    try:
        # 1. Archivos seleccionados
        file_ids = list(report.files_used.values_list("id", flat=True))
        print(f"[REPORT] report_id={report_id} file_format={report.file_format} file_ids={file_ids}")

        # -----------------------------------------------------------------------
        # Flujo PPTX
        # -----------------------------------------------------------------------
        if report.file_format == "pptx":
            from reports.services.pptx_spec_generator import generate_presentation_spec
            from reports.renderers.pptx_renderer import render_pptx_from_spec

            spec = generate_presentation_spec(
                report_name=report.report_name,
                report_type=report.report_type,
                guided_prompt=report.instructions,
                file_ids=file_ids,
                top_k=5,
            )
            pptx_bytes = render_pptx_from_spec(spec)

            safe_name = slugify(report.report_name)[:60] or "report"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{safe_name}_{timestamp}.pptx"

            # Mock a file object for the custom file upload
            file_obj = io.BytesIO(pptx_bytes)
            file_obj.name = filename
            file_obj.content_type = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
            response = upload_file_to_geonode(file_obj, filename, token=authorization)
            
            print(f"[REPORT] geonode_result: {response.status_code} - {response.text}", flush=True)

            if response.status_code in [200, 201]:
                resp_json = response.json()
                relative_url = resp_json.get("url", "")
                
                # The response URL is typically relative, e.g. "/uploaded/ia/uploads/..."
                geonode_base = os.environ.get("GEONODE_SERVER", "").rstrip("/")
                report.geonode_url = geonode_base + relative_url
                report.geonode_id = None # Fallback as ID is not extracted
                download_url = report.geonode_url
            else:
                rel_path = _save_local(pptx_bytes, filename, report.context_id, report_id)
                report.file_path = rel_path
                media_url = getattr(settings, "MEDIA_URL", "/media/")
                download_url = base_url.rstrip("/") + media_url + rel_path

            report.status = "done"
            report.save(update_fields=["geonode_id", "geonode_url", "file_path", "status", "updated_date"])
            print(f"[REPORT] done (pptx) → {download_url}")
            return {"report_id": report_id, "download_url": download_url}

        # -----------------------------------------------------------------------
        # Flujo PDF / Word / CSV / TXT
        # -----------------------------------------------------------------------

        # 2. RAG search
        query = f"{report.report_name}. {report.instructions}".strip()
        chunks = optimized_rag_search_files(file_ids=file_ids, query=query, top_k=5)
        print(f"[REPORT] chunks recuperados: {len(chunks)}")

        # 3. Construir evidence
        evidence = []
        for ch in chunks:
            meta = getattr(ch, "metadata_json", None) or {}
            evidence.append({
                "doc_id": getattr(ch, "file_id", None),
                "title": getattr(getattr(ch, "file", None), "filename", "") or "",
                "page": meta.get("page"),
                "chunk_id": f"{getattr(ch, 'file_id', 'x')}-{getattr(ch, 'chunk_index', 'x')}",
                "text": getattr(ch, "text", "") or "",
            })

        # 4. Construir mensajes para el LLM
        prompt_output_format = (
            "csv"        if report.file_format == "csv"
            else "plain_text" if report.file_format == "txt"
            else report.output_format
        )
        messages = build_prompt(
            report_type=report.report_type,
            output_format=prompt_output_format,
            instructions=report.instructions,
            evidence=evidence,
            report_name=report.report_name,
        )

        # 5. Llamar al LLM
        content = ollama_chat(messages, temperature=0.2)
        print(f"[REPORT] LLM respondió {len(content)} chars")

        # 6. Renderizar al formato elegido
        file_bytes = _render(content, report.file_format, report.output_format)

        # 7. Determinar extensión, filename y content-type
        ext_map = {"pdf": "pdf", "word": "docx", "csv": "csv", "txt": "txt"}
        ct_map = {
            "pdf": "application/pdf",
            "word": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "csv": "text/csv",
            "txt": "text/plain",
        }
        ext = ext_map.get(report.file_format, "bin")
        content_type = ct_map.get(report.file_format, "application/octet-stream")

        safe_name = slugify(report.report_name)[:60] or "report"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{timestamp}.{ext}"

        # 8. Intentar subida a GeoNode usando la nueva función
        file_obj = io.BytesIO(file_bytes)
        file_obj.name = filename
        file_obj.content_type = content_type
        response = upload_file_to_geonode(file_obj, filename, token=authorization)

        print(f"[REPORT] geonode_result HTTP {response.status_code}: {response.text}", flush=True)
        print(filename,flush=True)

        if response.status_code in [200, 201]:
            resp_json = response.json()
            relative_url = resp_json.get("url", "")
            
            # The response URL is typically relative
            geonode_base = os.environ.get("GEONODE_SERVER", "").rstrip("/")
            report.geonode_url = geonode_base + relative_url
            report.geonode_id = None
            report.file_path = filename
            download_url = report.geonode_url
            report.status = "done"
            report.save(update_fields=["geonode_id", "geonode_url", "status", "updated_date"])
        else:
            # Fallback: guardar en disco local
            rel_path = _save_local(file_bytes, filename, report.context_id, report_id)
            media_url = getattr(settings, "MEDIA_URL", "/media/")
            download_url = base_url.rstrip("/") + media_url + rel_path
            report.file_path = rel_path
            report.status = "done"
            report.save(update_fields=["file_path", "status", "updated_date"])

        print(f"[REPORT] done → {download_url}")
        return {"report_id": report_id, "download_url": download_url}

    except Exception as exc:
        report.status = "error"
        report.error_message = str(exc)
        report.save(update_fields=["status", "error_message", "updated_date"])
        print(f"[REPORT] error report_id={report_id}: {exc}")
        raise


# ---------------------------------------------------------------------------
# Helpers de renderizado
# ---------------------------------------------------------------------------

def _render(content: str, file_format: str, output_format: str) -> bytes:
    if file_format == "pdf":
        from reports.renderers.pdf_renderer import render_pdf
        return render_pdf(content, output_format)

    elif file_format == "word":
        from reports.renderers.docx_renderer import render_docx
        return render_docx(content, output_format)

    elif file_format == "csv":
        from reports.renderers.csv_renderer import render_csv
        return render_csv(content)

    elif file_format == "txt":
        from reports.renderers.txt_renderer import render_txt
        return render_txt(content)

    else:
        raise ValueError(f"file_format no soportado: {file_format}")
