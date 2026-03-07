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
                            "context": {"type": "string"},
                            "país": {"type": "string"},
                            "estado": {"type": "string"}
                        }
                    }
                },
                "geojson": {
                    "type": "object",
                    "description": "Objeto FeatureCollection válido de GeoJSON georreferenciado."
                },
                "download_url": {
                    "type": "string",
                    "description": "URL pública para descargar el archivo georreferenciado mapeado (.geojson, .shp.zip, .gpkg)."
                },
                "export_format": {
                    "type": "string",
                    "description": "Formato de exportación elegido por el usuario (geojson, shp, gpkg)."
                },
                "detected_focus": {"type": "string"}
            },
        }
    },
    summary="Detectar localidades en documentos",
    description="Analiza los documentos de un contexto o un arreglo de archivos específicos para extraer entidades geográficas y exportarlas temporalmente.",
    tags=["Localidades"],
)
@api_view(["POST"])
def detect_localidades(request):
    """
    Endpoint para detectar localidades.
    Recibe: {"context_id": id, "file_ids": [id1, id2], "model": "...", "focus": "...", "entity_types": ["país", "infraestructura", ...], "export_format": "geojson|shp|gpkg", "geometry_type": "point|polygon|centroid"}
    """
    data = request.data
    context_id = data.get("context_id")
    file_ids = data.get("file_ids")
    entity_types = data.get("entity_types")
    model = data.get("model", "deepseek-r1:32b") # Fallback a deepseek-r1 si no viene
    focus = data.get("focus", "México")
    export_format = data.get("export_format", "geojson")
    geometry_type = data.get("geometry_type", "point")

    authorization = request.META.get("HTTP_AUTHORIZATION")

    if not context_id and not file_ids:
        return Response({"error": "Se requiere el parámetro 'context_id' o un arreglo de 'file_ids'"}, status=status.HTTP_400_BAD_REQUEST)

    logger.info(f"Detectando localidades. context_id: {context_id}, file_ids: {file_ids}, focus: {focus}, entity_types: {entity_types}, format: {export_format}, geometry: {geometry_type}")
    
    result = extract_localities_from_context(
        context_id=context_id, 
        model=model, 
        focus=focus, 
        file_ids=file_ids, 
        entity_types=entity_types, 
        export_format=export_format,
        geometry_type=geometry_type,
        authorization=authorization
    )
    
    if "error" in result:
        return Response(result, status=status.HTTP_400_BAD_REQUEST)
        
    return Response(result, status=status.HTTP_200_OK)
