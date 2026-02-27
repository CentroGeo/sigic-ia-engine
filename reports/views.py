import os
from django.conf import settings
from django.http import JsonResponse
from django.utils.text import slugify
from django.utils.crypto import get_random_string
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework import status

from reports.serializers import (
    PptxReportRequestSerializer,
    ReportCreateSerializer,
    ReportSerializer,
    ReportListSerializer,
)
from reports.services.pptx_spec_generator import generate_presentation_spec
from reports.renderers.pptx_renderer import render_pptx_from_spec
from reports.models import Report
from fileuploads.models import Context, Files
from shared.authentication import KeycloakAuthentication


# ---------------------------------------------------------------------------
# Vista existente (PPTX)
# ---------------------------------------------------------------------------

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

    # nombre del file
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

    # construcción de la URL absoluta
    download_url = request.build_absolute_uri(settings.MEDIA_URL + rel_path.replace("\\", "/"))

    return JsonResponse(
        {
            "download_url": download_url,
            "filename": filename,
        }
    )


# ---------------------------------------------------------------------------
# Nuevas vistas
# ---------------------------------------------------------------------------

@api_view(["POST"])
@authentication_classes([KeycloakAuthentication])
def generate_report(request: Request):
    """
    POST /api/reports/generate/

    Crea un Report con status=pending y dispara la tarea Celery.
    """
    ser = ReportCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    context = Context.objects.get(pk=data["context_id"])

    # Obtener user_id desde el token JWT
    user_id = None
    if hasattr(request, "user") and request.user and hasattr(request.user, "payload"):
        user_id = request.user.payload.get("email")

    file_format = data.get("file_format", "pdf")

    report = Report.objects.create(
        context=context,
        report_name=data["report_name"],
        report_type=data["report_type"],
        output_format=data.get("output_format", "markdown"),
        file_format=file_format,
        text_format=data.get("text_format"),
        instructions=data.get("instructions", ""),
        use_letterhead=data.get("use_letterhead", False),
        user_id=user_id,
        status="pending",
    )

    # Asociar archivos seleccionados
    file_ids = data["file_ids"]
    files = Files.objects.filter(id__in=file_ids)
    report.files_used.set(files)

    # Disparar tarea Celery
    from reports.tasks import generate_report_task
    base_url = request.build_absolute_uri("/").rstrip("/")
    authorization = request.headers.get("Authorization", "")
    task = generate_report_task.delay(report.id, base_url, authorization)

    report.task_id = task.id
    report.save(update_fields=["task_id", "updated_date"])

    return Response(
        {
            "report_id": report.id,
            "task_id": task.id,
            "status": report.status,
        },
        status=status.HTTP_202_ACCEPTED,
    )


@api_view(["GET"])
@authentication_classes([KeycloakAuthentication])
def list_reports(request: Request):
    """
    GET /api/reports/

    Filtros: context_id, report_type, file_format, output_format, status,
             date_from, date_to
    El user_id se filtra automáticamente desde el token.
    """
    user_id = None
    if hasattr(request, "user") and request.user and hasattr(request.user, "payload"):
        user_id = request.user.payload.get("email")

    qs = Report.objects.all()

    if user_id:
        qs = qs.filter(user_id=user_id)

    params = request.query_params

    if params.get("context_id"):
        qs = qs.filter(context_id=params["context_id"])
    if params.get("report_type"):
        qs = qs.filter(report_type=params["report_type"])
    if params.get("file_format"):
        qs = qs.filter(file_format=params["file_format"])
    if params.get("output_format"):
        qs = qs.filter(output_format=params["output_format"])
    if params.get("status"):
        qs = qs.filter(status=params["status"])
    if params.get("date_from"):
        qs = qs.filter(created_date__date__gte=params["date_from"])
    if params.get("date_to"):
        qs = qs.filter(created_date__date__lte=params["date_to"])

    qs = qs.order_by("-created_date")
    ser = ReportListSerializer(qs, many=True, context={"request": request})
    return Response(ser.data)


@api_view(["GET"])
@authentication_classes([KeycloakAuthentication])
def get_report(request: Request, pk: int):
    """
    GET /api/reports/{id}/

    Solo acceso al propio user_id.
    """
    user_id = None
    if hasattr(request, "user") and request.user and hasattr(request.user, "payload"):
        user_id = request.user.payload.get("email")

    try:
        report = Report.objects.get(pk=pk)
    except Report.DoesNotExist:
        return Response({"detail": "No encontrado."}, status=status.HTTP_404_NOT_FOUND)

    if user_id and report.user_id and report.user_id != user_id:
        return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

    ser = ReportSerializer(report, context={"request": request})
    return Response(ser.data)
