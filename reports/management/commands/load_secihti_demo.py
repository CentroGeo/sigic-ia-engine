"""
Management command para cargar datos de demostración de SECIHTI en el RAG.

Crea:
  - 1 Workspace: "Programas y Convocatorias SECIHTI"
  - 1 Context:   "Convocatorias SECIHTI 2025"
  - 4 Files con embeddings generados desde PDFs reales de SECIHTI
  - 3 Reports de ejemplo (pending) listos para ser generados

Uso:
  docker exec ia-engine python manage.py load_secihti_demo
  docker exec ia-engine python manage.py load_secihti_demo --pdf-dir /ruta/custom
  docker exec ia-engine python manage.py load_secihti_demo --reset
"""
import os
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from PyPDF2 import PdfReader

from fileuploads.models import Workspace, Context, Files, DocumentEmbedding
from fileuploads.embeddings_service import embedder
from reports.models import Report


PDF_DIR_DEFAULT = "/tmp/secihti_pdfs"

PDFS = [
    {
        "filename": "convocatoria_ciencia_basica_frontera_2025.pdf",
        "title": "Convocatoria Ciencia Básica y de Frontera 2025",
        "document_type": "pdf",
    },
    {
        "filename": "convocatoria_investigacion_humanistica_2025.pdf",
        "title": "Convocatoria Investigación Humanística 2025",
        "document_type": "pdf",
    },
    {
        "filename": "convocatoria_SNII_2025.pdf",
        "title": "Convocatoria SNII 2025",
        "document_type": "pdf",
    },
    {
        "filename": "terminos_ref_IH_2025.pdf",
        "title": "Términos de Referencia Investigación Humanística 2025",
        "document_type": "pdf",
    },
]

DEMO_USER = "demo@secihti.mx"
WORKSPACE_TITLE = "Programas y Convocatorias SECIHTI"
CONTEXT_TITLE = "Convocatorias SECIHTI 2025"


class Command(BaseCommand):
    help = "Carga datos de demostración de SECIHTI en el RAG"

    def add_arguments(self, parser):
        parser.add_argument(
            "--pdf-dir",
            default=PDF_DIR_DEFAULT,
            help=f"Directorio con los PDFs de SECIHTI (default: {PDF_DIR_DEFAULT})",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Elimina el workspace demo existente antes de crear uno nuevo",
        )
        parser.add_argument(
            "--skip-embeddings",
            action="store_true",
            help="Crear registros en DB pero omitir generación de embeddings (para tests rápidos)",
        )

    def handle(self, *args, **options):
        pdf_dir = options["pdf_dir"]
        do_reset = options["reset"]
        skip_embeddings = options["skip_embeddings"]

        self.stdout.write(self.style.MIGRATE_HEADING("=== SIGIC Demo Loader — SECIHTI ==="))

        # ── Reset opcional ──────────────────────────────────────────────────
        if do_reset:
            deleted, _ = Workspace.objects.filter(
                title=WORKSPACE_TITLE, user_id=DEMO_USER
            ).delete()
            self.stdout.write(f"  ♻  Reset: {deleted} workspace(s) eliminado(s)")

        # ── Verificar PDFs disponibles ──────────────────────────────────────
        available = []
        for pdf_info in PDFS:
            path = os.path.join(pdf_dir, pdf_info["filename"])
            if os.path.isfile(path):
                available.append((path, pdf_info))
            else:
                self.stdout.write(
                    self.style.WARNING(f"  ⚠  No encontrado: {path} — se omite")
                )

        if not available:
            self.stdout.write(
                self.style.ERROR(
                    f"No se encontró ningún PDF en {pdf_dir}. "
                    "Descárgalos con: make download-demo-pdfs"
                )
            )
            return

        # ── Workspace ────────────────────────────────────────────────────────
        workspace, created = Workspace.objects.get_or_create(
            title=WORKSPACE_TITLE,
            user_id=DEMO_USER,
            defaults={
                "description": "Análisis de convocatorias, términos de referencia "
                               "e informes oficiales de SECIHTI 2024-2025",
                "public": True,
                "active": True,
            },
        )
        tag = "creado" if created else "ya existía"
        self.stdout.write(f"  ✅ Workspace #{workspace.pk} '{workspace.title}' ({tag})")

        # ── Context ──────────────────────────────────────────────────────────
        context, created = Context.objects.get_or_create(
            title=CONTEXT_TITLE,
            workspace=workspace,
            defaults={
                "description": "Convocatorias vigentes de ciencia básica, "
                               "investigación humanística y SNII 2025",
                "user_id": DEMO_USER,
                "active": True,
                "public": True,
            },
        )
        tag = "creado" if created else "ya existía"
        self.stdout.write(f"  ✅ Context #{context.pk} '{context.title}' ({tag})")

        # ── Copiar PDFs a media/uploads/ ─────────────────────────────────────
        upload_base = os.path.join(settings.MEDIA_ROOT, "uploads", "demo_secihti")
        os.makedirs(upload_base, exist_ok=True)

        file_ids = []
        for src_path, pdf_info in available:
            dest_path = os.path.join(upload_base, pdf_info["filename"])
            if not os.path.isfile(dest_path):
                shutil.copy2(src_path, dest_path)

            rel_path = os.path.relpath(dest_path, settings.MEDIA_ROOT)

            file_obj, created = Files.objects.get_or_create(
                workspace=workspace,
                filename=pdf_info["filename"],
                defaults={
                    "document_type": pdf_info["document_type"],
                    "user_id": DEMO_USER,
                    "path": rel_path,
                    "processed": False,
                    "language": "es",
                },
            )
            tag = "creado" if created else "ya existía"
            self.stdout.write(
                f"  📄 File #{file_obj.pk} '{pdf_info['title']}' ({tag})"
            )
            file_ids.append((file_obj, dest_path, pdf_info))

            # Asociar al contexto
            context.files.add(file_obj)

        # ── Generar embeddings ────────────────────────────────────────────────
        if skip_embeddings:
            self.stdout.write(self.style.WARNING("  ⏭  Embeddings omitidos (--skip-embeddings)"))
        else:
            self.stdout.write("\n  🤖 Generando embeddings (requiere Ollama)...")
            for file_obj, dest_path, pdf_info in file_ids:
                if file_obj.processed:
                    self.stdout.write(f"     · '{pdf_info['filename']}' ya procesado — saltando")
                    continue

                self.stdout.write(f"     · Procesando '{pdf_info['filename']}'...")
                try:
                    text = self._extract_pdf_text(dest_path)
                    if not text:
                        self.stdout.write(
                            self.style.WARNING(f"       ⚠ Sin texto extraído — saltando")
                        )
                        continue

                    chunks, embeddings_list, meta = embedder.embed_document_smart(
                        text, filename=pdf_info["filename"]
                    )
                    self.stdout.write(
                        f"       → {len(chunks)} chunks | idioma: {meta.get('language', '?')}"
                    )

                    # Guardar en BD (solo si no hay chunks existentes)
                    existing = DocumentEmbedding.objects.filter(file=file_obj).count()
                    if existing == 0:
                        for idx, (chunk, emb) in enumerate(zip(chunks, embeddings_list)):
                            DocumentEmbedding.objects.create(
                                file=file_obj,
                                chunk_index=idx,
                                text=chunk,
                                embedding=emb.tolist(),
                                language=meta.get("language", "es"),
                                metadata_json={"page": None, "source": pdf_info["title"]},
                            )
                        self.stdout.write(
                            f"       ✅ {len(chunks)} embeddings guardados"
                        )
                    else:
                        self.stdout.write(
                            f"       ℹ  Ya tenía {existing} chunks — saltando insert"
                        )

                    file_obj.processed = True
                    file_obj.save(update_fields=["processed"])

                except Exception as e:
                    self.stdout.write(
                        self.style.ERROR(f"       ❌ Error: {e}")
                    )

        # ── Reports de ejemplo ────────────────────────────────────────────────
        self.stdout.write("\n  📊 Creando reportes de ejemplo...")
        demo_reports = [
            {
                "report_name": "Resumen Ejecutivo — Convocatorias SECIHTI 2025",
                "report_type": "summary",
                "output_format": "markdown",
                "file_format": "pdf",
                "instructions": (
                    "Elaborar un resumen ejecutivo de las convocatorias vigentes de SECIHTI 2025. "
                    "Destacar montos, requisitos generales y fechas límite."
                ),
            },
            {
                "report_name": "Análisis Institucional — Ciencia Básica y SNII 2025",
                "report_type": "institutional",
                "output_format": "markdown",
                "file_format": "pdf",
                "instructions": (
                    "Generar un reporte institucional sobre las convocatorias de Ciencia Básica "
                    "y Frontera y el SNII 2025. Incluir estructura, criterios de evaluación "
                    "y comparación de requisitos de elegibilidad."
                ),
            },
            {
                "report_name": "Tabla de Requisitos y Criterios SECIHTI 2025",
                "report_type": "descriptive",
                "output_format": "markdown",
                "file_format": "csv",
                "instructions": (
                    "Extraer en formato tabular los requisitos, montos y criterios de evaluación "
                    "de cada convocatoria. Columnas: Convocatoria, Requisitos, Monto, Fecha Límite."
                ),
            },
        ]

        all_file_ids = [fo.pk for fo, _, _ in file_ids]

        for rdata in demo_reports:
            report, created = Report.objects.get_or_create(
                report_name=rdata["report_name"],
                context=context,
                defaults={
                    **rdata,
                    "user_id": DEMO_USER,
                    "status": "pending",
                    "use_letterhead": False,
                },
            )
            tag = "creado" if created else "ya existía"
            if created:
                report.files_used.set(
                    Files.objects.filter(pk__in=all_file_ids)
                )
            self.stdout.write(
                f"  📋 Report #{report.pk} '{report.report_name}' ({tag})"
            )

        # ── Resumen final ─────────────────────────────────────────────────────
        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(self.style.SUCCESS("  DEMO cargado exitosamente"))
        self.stdout.write(f"  Workspace ID : {workspace.pk}")
        self.stdout.write(f"  Context  ID  : {context.pk}")
        self.stdout.write(f"  Files        : {[fo.pk for fo, _, _ in file_ids]}")
        total_chunks = DocumentEmbedding.objects.filter(
            file__workspace=workspace
        ).count()
        self.stdout.write(f"  Chunks en BD : {total_chunks}")
        self.stdout.write(
            "\n  Para generar los reportes:\n"
            "  POST /api/reports/generate/  con context_id y file_ids de arriba"
        )
        self.stdout.write("=" * 60)

    # ── helpers ────────────────────────────────────────────────────────────────
    def _extract_pdf_text(self, path: str) -> str:
        try:
            reader = PdfReader(path)
            return "\n".join(
                page.extract_text()
                for page in reader.pages
                if page.extract_text()
            ).strip()
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Error leyendo PDF {path}: {e}"))
            return ""
