import json
from pathlib import Path

from django.core.management.base import BaseCommand

from reports.renderers.pptx_renderer import render_pptx_from_spec


class Command(BaseCommand):
    help = "Genera un PPTX de prueba desde reports/tests/sample_presentation.json"

    def handle(self, *args, **options):
        base_dir = Path(__file__).resolve().parents[3]  # raíz del repo (donde está manage.py)
        json_path = base_dir / "reports" / "tests" / "sample_presentation.json"

        if not json_path.exists():
            raise FileNotFoundError(f"No existe el JSON de prueba: {json_path}")

        spec = json.loads(json_path.read_text(encoding="utf-8"))
        pptx_bytes = render_pptx_from_spec(spec)

        out_dir = base_dir / "media" / "reports"
        out_dir.mkdir(parents=True, exist_ok=True)

        out_path = out_dir / "sample_report.pptx"
        out_path.write_bytes(pptx_bytes)

        self.stdout.write(self.style.SUCCESS(f"PPTX generado: {out_path}"))
