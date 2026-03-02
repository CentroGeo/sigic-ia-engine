"""
Management command para cargar datos de demostración con PDFs diversos de SECIHTI.

Crea:
  - 1 Workspace: "Políticas y Programas de Ciencia 2024-2025"
  - 1 Context:   "Becas, SNP y Política Científica"
  - 3 Files con embeddings generados desde PDFs reales
  - 5 Reports de ejemplo cubriendo todas las combinaciones de formatos y tipos

PDFs usados:
  - terminos_ref_investigacion_humanistica_2025.pdf  (términos de referencia, 19 pág.)
  - snp_convocatoria_2025.pdf                        (Sistema Nacional de Posgrados, 14 pág.)
  - peciti_2021_2024.pdf                             (Programa Especial CTI 2021-2024, 148 pág.)

Uso:
  docker exec ia-engine python manage.py load_diverse_demo
  docker exec ia-engine python manage.py load_diverse_demo --pdf-dir /ruta/custom
  docker exec ia-engine python manage.py load_diverse_demo --reset
  docker exec ia-engine python manage.py load_diverse_demo --skip-embeddings
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


PDF_DIR_DEFAULT = "/app/demo_pdfs2"

PDFS = [
    {
        "filename": "terminos_ref_investigacion_humanistica_2025.pdf",
        "title": "Términos de Referencia — Investigación Humanística 2025",
        "document_type": "pdf",
    },
    {
        "filename": "snp_convocatoria_2025.pdf",
        "title": "Sistema Nacional de Posgrados — Convocatoria 2025",
        "document_type": "pdf",
    },
    {
        "filename": "peciti_2021_2024.pdf",
        "title": "Programa Especial de Ciencia, Tecnología e Innovación 2021-2024",
        "document_type": "pdf",
    },
]

DEMO_USER = "demo@secihti.mx"
WORKSPACE_TITLE = "Políticas y Programas de Ciencia 2024-2025"
CONTEXT_TITLE = "Becas, SNP y Política Científica"


class Command(BaseCommand):
    help = "Carga PDFs diversos (SNP, PECITI, TDR) y genera reportes de demostración en todos los formatos"

    def add_arguments(self, parser):
        parser.add_argument(
            "--pdf-dir",
            default=PDF_DIR_DEFAULT,
            help=f"Directorio con los PDFs (default: {PDF_DIR_DEFAULT})",
        )
        parser.add_argument(
            "--reset",
            action="store_true",
            help="Elimina el workspace demo existente antes de crear uno nuevo",
        )
        parser.add_argument(
            "--skip-embeddings",
            action="store_true",
            help="Crear registros en DB pero omitir generación de embeddings",
        )

    def handle(self, *args, **options):
        pdf_dir = options["pdf_dir"]
        do_reset = options["reset"]
        skip_embeddings = options["skip_embeddings"]

        self.stdout.write(self.style.MIGRATE_HEADING("=== SIGIC Demo Loader — Diverso ==="))

        # ── Reset opcional ────────────────────────────────────────────────────
        if do_reset:
            deleted, _ = Workspace.objects.filter(
                title=WORKSPACE_TITLE, user_id=DEMO_USER
            ).delete()
            self.stdout.write(f"  ♻  Reset: {deleted} workspace(s) eliminado(s)")

        # ── Verificar PDFs disponibles ────────────────────────────────────────
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
                    f"No se encontró ningún PDF en {pdf_dir}."
                )
            )
            return

        # ── Workspace ─────────────────────────────────────────────────────────
        workspace, created = Workspace.objects.get_or_create(
            title=WORKSPACE_TITLE,
            user_id=DEMO_USER,
            defaults={
                "description": (
                    "Políticas públicas de ciencia y tecnología, programas de posgrado "
                    "y términos de referencia SECIHTI 2024-2025"
                ),
                "public": True,
                "active": True,
            },
        )
        tag = "creado" if created else "ya existía"
        self.stdout.write(f"  ✅ Workspace #{workspace.pk} '{workspace.title}' ({tag})")

        # ── Context ───────────────────────────────────────────────────────────
        context, created = Context.objects.get_or_create(
            title=CONTEXT_TITLE,
            workspace=workspace,
            defaults={
                "description": (
                    "Convocatoria del Sistema Nacional de Posgrados, Programa Especial "
                    "de CTI 2021-2024 y Términos de Referencia de Investigación Humanística"
                ),
                "user_id": DEMO_USER,
                "active": True,
                "public": True,
            },
        )
        tag = "creado" if created else "ya existía"
        self.stdout.write(f"  ✅ Context #{context.pk} '{context.title}' ({tag})")

        # ── Copiar PDFs a media/uploads/ ──────────────────────────────────────
        upload_base = os.path.join(settings.MEDIA_ROOT, "uploads", "demo_diverse")
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
            self.stdout.write(f"  📄 File #{file_obj.pk} '{pdf_info['title']}' ({tag})")
            file_ids.append((file_obj, dest_path, pdf_info))

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
                            self.style.WARNING("       ⚠ Sin texto extraído — saltando")
                        )
                        continue

                    chunks, embeddings_list, meta = embedder.embed_document_smart(
                        text, filename=pdf_info["filename"]
                    )
                    self.stdout.write(
                        f"       → {len(chunks)} chunks | idioma: {meta.get('language', '?')}"
                    )

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
                        self.stdout.write(f"       ✅ {len(chunks)} embeddings guardados")
                    else:
                        self.stdout.write(f"       ℹ  Ya tenía {existing} chunks — saltando insert")

                    file_obj.processed = True
                    file_obj.save(update_fields=["processed"])

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"       ❌ Error: {e}"))

        # ── Reports de ejemplo — todas las combinaciones ──────────────────────
        self.stdout.write("\n  📊 Creando reportes de ejemplo (todas las combinaciones)...")
        demo_reports = [
            # 1. Word / institutional / markdown
            {
                "report_name": "Reporte Institucional — SNP y PECITI 2025",
                "report_type": "institutional",
                "output_format": "markdown",
                "file_format": "word",
                "instructions": (
                    "Elaborar un reporte institucional que analice las políticas del "
                    "Sistema Nacional de Posgrados y su alineación con el Programa "
                    "Especial de CTI 2021-2024. Incluir estructura de becas, criterios "
                    "de evaluación y recomendaciones de política pública."
                ),
            },
            # 2. PDF / descriptive / markdown
            {
                "report_name": "Análisis Descriptivo — Política CTI 2021-2024",
                "report_type": "descriptive",
                "output_format": "markdown",
                "file_format": "pdf",
                "instructions": (
                    "Describir con detalle los objetivos, estrategias y líneas de acción "
                    "del Programa Especial de Ciencia, Tecnología e Innovación 2021-2024. "
                    "Destacar indicadores, presupuesto y avances documentados."
                ),
            },
            # 3. PDF / evaluation / plain_text
            {
                "report_name": "Evaluación — Términos de Referencia IH 2025",
                "report_type": "evaluation",
                "output_format": "plain_text",
                "file_format": "pdf",
                "instructions": (
                    "Evaluar los términos de referencia de la convocatoria de "
                    "Investigación Humanística 2025. Identificar fortalezas, debilidades, "
                    "criterios de elegibilidad y recomendaciones para solicitantes."
                ),
            },
            # 4. Word / summary / plain_text
            {
                "report_name": "Resumen Ejecutivo — Sistema Nacional de Posgrados 2025",
                "report_type": "summary",
                "output_format": "plain_text",
                "file_format": "word",
                "instructions": (
                    "Generar un resumen ejecutivo conciso de la convocatoria del Sistema "
                    "Nacional de Posgrados 2025. Máximo 1 página. Destacar requisitos "
                    "de admisión, montos de apoyo y fechas clave."
                ),
            },
            # 5. CSV / descriptive / markdown (tabla extraída)
            {
                "report_name": "Tabla Comparativa — Programas de Apoyo SECIHTI 2025",
                "report_type": "descriptive",
                "output_format": "markdown",
                "file_format": "csv",
                "instructions": (
                    "Extraer en formato tabular CSV los programas de apoyo mencionados "
                    "en los documentos. Columnas: Programa, Tipo de Apoyo, Requisitos, "
                    "Monto o Beneficio, Fecha Límite, Destinatarios."
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
                report.files_used.set(Files.objects.filter(pk__in=all_file_ids))
            self.stdout.write(
                f"  📋 Report #{report.pk} '{report.report_name}' "
                f"[{report.report_type}/{report.file_format}] ({tag})"
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
            "\n  Para generar los reportes vía API:\n"
            "  POST /api/reports/generate/  con el context_id y file_ids de arriba"
        )
        self.stdout.write("=" * 60)

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
