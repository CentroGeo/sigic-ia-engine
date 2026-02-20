import os
from django.conf import settings
from django.http import JsonResponse
from django.utils.text import slugify
from django.utils.crypto import get_random_string
from rest_framework.decorators import api_view

from reports.serializers import PptxReportRequestSerializer
from reports.services.pptx_spec_generator import generate_presentation_spec
from reports.renderers.pptx_renderer import render_pptx_from_spec


@api_view(["POST"])
def generate_pptx_report(request):
    ser = PptxReportRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    spec = generate_presentation_spec(
        report_name=data["report_name"],
        report_type=data["report_type"],
        guided_prompt=data.get("guided_prompt", ""),
        file_ids=data["file_ids"],
        top_k=data.get("top_k", 20),
    )

    pptx_bytes = render_pptx_from_spec(spec)

    # nombre  del file
    base_name = data["report_name"] or "report"
    # quitar .pptx si viene incluido
    if base_name.lower().endswith(".pptx"):
        base_name = base_name[:-5]
    base_name = slugify(base_name)[:60] or "report"
    filename = f"{base_name}-{get_random_string(8)}.pptx"

    # guardado en MEDIA_ROOT/reports/
    rel_path = os.path.join("reports", filename)
    abs_path = os.path.join(settings.MEDIA_ROOT, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(abs_path, "wb") as f:
        f.write(pptx_bytes)

    # construcción de la  URL absoluta
    download_url = request.build_absolute_uri(settings.MEDIA_URL + rel_path.replace("\\", "/"))

    return JsonResponse(
        {
            "download_url": download_url,
            "filename": filename,
        }
    )
