import os
from fileuploads.utils import delete_image_to_geonode
from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response
from rest_framework import status
from drf_spectacular.utils import extend_schema
from shared.authentication import KeycloakAuthentication
from fileuploads.models import Context, Files
from .models import Spatialization
from .serializers import SpatializationCreateSerializer, SpatializationListSerializer, SpatializationSerializer
from .tasks import generate_spatialization_task
import logging

logger = logging.getLogger(__name__)

@extend_schema(
    methods=["POST"],
    request=SpatializationCreateSerializer,
    responses={202: "Accepted"},
    summary="Detectar localidades asíncronamente",
    description="Programa una tarea para procesar espacialización y devuelve un ID temporal para polling.",
    tags=["Localidades"],
)
@api_view(["POST"])
@authentication_classes([KeycloakAuthentication])
def detect_localidades(request):
    ser = SpatializationCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    context = Context.objects.get(pk=data["context_id"])
    
    user_id = None
    user_id = None
    if hasattr(request, "user") and request.user and hasattr(request.user, "payload"):
        user_id = request.user.payload.get("preferred_username") or request.user.payload.get("email")

    report_name = data.get("report_name", "Espacialización")
    
    sp = Spatialization.objects.create(
        context=context,
        report_name=report_name,
        entity_types=data.get("entity_types"),
        export_format=data.get("export_format", "geojson"),
        geometry_type=data.get("geometry_type", "point"),
        focus=data.get("focus", "auto"),
        custom_instructions=data.get("custom_instructions", ""),
        user_id=user_id,
        status="pending",
    )

    file_ids = data.get("file_ids", [])
    if file_ids:
        files = Files.objects.filter(id__in=file_ids)
        sp.files_used.set(files)

    authorization = request.headers.get("Authorization", "")
    refresh_token = data.get("refresh_token", "")
    task = generate_spatialization_task.delay(sp.id, authorization, refresh_token=refresh_token)

    sp.task_id = task.id
    sp.save(update_fields=["task_id", "updated_date"])

    return Response(
        {
            "id": sp.id,
            "task_id": task.id,
            "status": sp.status,
            "type": "espacializacion",
        },
        status=status.HTTP_202_ACCEPTED,
    )

@api_view(["GET"])
@authentication_classes([KeycloakAuthentication])
def list_spatializations(request):
    user_id = None
    user_id = None
    if hasattr(request, "user") and request.user and hasattr(request.user, "payload"):
        user_id = request.user.payload.get("preferred_username") or request.user.payload.get("email")

    qs = Spatialization.objects.all()

    if user_id:
        qs = qs.filter(user_id=user_id)

    params = request.query_params
    if params.get("context_id"):
        qs = qs.filter(context_id=params["context_id"])

    qs = qs.order_by("-created_date")
    ser = SpatializationListSerializer(qs, many=True, context={"request": request})
    return Response(ser.data)


@api_view(["GET"])
@authentication_classes([KeycloakAuthentication])
def get_spatialization(request, pk: int):
    user_id = None
    user_id = None
    if hasattr(request, "user") and request.user and hasattr(request.user, "payload"):
        user_id = request.user.payload.get("preferred_username") or request.user.payload.get("email")

    try:
        sp = Spatialization.objects.get(pk=pk)
    except Spatialization.DoesNotExist:
        return Response({"detail": "No encontrado."}, status=status.HTTP_404_NOT_FOUND)

    print(f"DEBUG get_spatialization: user_id={user_id}, sp.user_id={sp.user_id}", flush=True)

    if user_id and sp.user_id and sp.user_id.lower() != user_id.lower():
        print("DEBUG get_spatialization: FORBIDDEN!", flush=True)
        return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

    ser = SpatializationSerializer(sp, context={"request": request})
    return Response(ser.data)


@api_view(["DELETE"])
@authentication_classes([KeycloakAuthentication])
def delete_spatialization(request, pk: int):
    user_id = None
    authorization = request.headers.get("Authorization", "")
    refresh_token = request.data.get("refresh_token", "")
    
    if hasattr(request, "user") and request.user and hasattr(request.user, "payload"):
        user_id = request.user.payload.get("preferred_username") or request.user.payload.get("email")

    try:
        sp = Spatialization.objects.get(pk=pk)
        if(sp.geonode_url):
            filename = os.path.basename(sp.geonode_url)
            delete_image_to_geonode(filename, authorization, refresh_token)
        
    except Spatialization.DoesNotExist:
        return Response({"detail": "No encontrado."}, status=status.HTTP_404_NOT_FOUND)

    if user_id and sp.user_id and sp.user_id.lower() != user_id.lower():
        return Response({"detail": "No autorizado."}, status=status.HTTP_403_FORBIDDEN)

    sp.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)
