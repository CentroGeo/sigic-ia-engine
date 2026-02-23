from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .utils import extract_localities_from_context
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
import logging

logger = logging.getLogger(__name__)

@extend_schema(
    methods=["POST"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "entities": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "type": {"type": "string"},
                            "context": {"type": "string"}
                        }
                    }
                },
                "detected_focus": {"type": "string"}
            },
        }
    },
    summary="Detectar localidades en un contexto",
    description="Analiza los documentos de un contexto por id y extrae entidades geográficas (localidades, municipios, estados).",
    tags=["Localidades"],
)
@api_view(["POST"])
def detect_localidades(request):
    """
    Endpoint para detectar localidades en un contexto.
    Recibe: {"context_id": id_del_contexto, "model": "..."}
    """
    data = request.data
    context_id = data.get("context_id")
    model = data.get("model", "deepseek-r1:32b") # Fallback a deepseek-r1 si no viene
    focus = data.get("focus", "México")

    if not context_id:
        return Response({"error": "Se requiere el parámetro 'context_id'"}, status=status.HTTP_400_BAD_REQUEST)

    logger.info(f"Detectando localidades para el context_id: {context_id}, focus: {focus}")
    
    result = extract_localities_from_context(context_id, model, focus)
    
    return Response(result, status=status.HTTP_200_OK)
