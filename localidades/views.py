from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from .utils import extract_localities_from_text
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
                }
            },
        }
    },
    summary="Detectar localidades en un texto",
    description="Analiza un texto y extrae entidades geográficas (localidades, municipios, estados) de México.",
    tags=["Localidades"],
)
@api_view(["POST"])
def detect_localidades(request):
    """
    Endpoint para detectar localidades en un texto.
    Recibe: {"text": "...", "model": "..."}
    """
    data = request.data
    text = data.get("text")
    model = data.get("model", "deepseek-r1") # Fallback a deepseek-r1 si no viene
    focus = data.get("focus", "México")

    if not text:
        return Response({"error": "Se requiere el parámetro 'text'"}, status=status.HTTP_400_BAD_REQUEST)

    logger.info(f"Detectando localidades en texto (longitud: {len(text)}), focus: {focus}")
    
    result = extract_localities_from_text(text, model, focus)
    
    return Response(result, status=status.HTTP_200_OK)
