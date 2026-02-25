import os
from datetime import datetime

from celery import shared_task
from django.conf import settings
from django.utils.text import slugify

from reports.models import Report
from reports.prompts.base_prompt import build_prompt
from reports.services.ollama_client import ollama_chat
from fileuploads.views import optimized_rag_search_files


@shared_task(
    bind=True,
    name="reports.generate_report",
    time_limit=1800,
    soft_time_limit=1500,
)
def generate_report_task(self, report_id: int, base_url: str) -> dict:
    """
    Tarea Celery que genera el contenido del reporte con el LLM
    y lo persiste en disco.
    """
    report = Report.objects.select_related("context").get(pk=report_id)
    report.status = "processing"
    report.task_id = self.request.id
    report.save(update_fields=["status", "task_id", "updated_date"])

    try:
        # 1. Archivos seleccionados
        file_ids = list(report.files_used.values_list("id", flat=True))
        print(f"[REPORT] report_id={report_id} file_ids={file_ids}")

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
        # CSV usa su propia regla de output: pasamos "csv" como output_format al prompt
        prompt_output_format = (
            "csv" if report.file_format == "csv" else report.output_format
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

        # 6. Renderizar al formato de archivo elegido
        file_bytes = _render(content, report.file_format, report.output_format)

        # 7. Guardar en disco
        ext_map = {"pdf": "pdf", "word": "docx", "csv": "csv"}
        ext = ext_map.get(report.file_format, "bin")

        safe_name = slugify(report.report_name)[:60] or "report"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{safe_name}_{timestamp}.{ext}"

        context_id = report.context_id
        rel_dir = os.path.join("reports", str(context_id), str(report_id))
        abs_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)

        abs_path = os.path.join(abs_dir, filename)
        with open(abs_path, "wb") as f:
            f.write(file_bytes)

        rel_path = os.path.join(rel_dir, filename).replace("\\", "/")

        # 8. Actualizar el reporte
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
# Helpers
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

    else:
        raise ValueError(f"file_format no soportado: {file_format}")
