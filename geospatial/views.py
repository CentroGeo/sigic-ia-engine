from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.core.serializers import serialize
from rest_framework import status
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.http import StreamingHttpResponse
from django.core.serializers.json import DjangoJSONEncoder
from fileuploads.models import Workspace, Context, Files, DocumentEmbedding
from fileuploads.embeddings_service import embedder
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from pgvector.django import L2Distance
from django.db import transaction
import time
import threading
import requests
import json
import os
from typing import List, Optional, Any
from django.db import connection
from django.conf import settings
import os
import logging
from .geospatial_utils import (
        load_geojson_to_gdf,
        validate_plan,
        execute_geospatial_plan,
        gdf_to_geojson_dict,
        save_geojson_file,
        get_geometry_type
    )
from .prompt_geospacial import BASE_SYSTEM_PROMPT_GEOSPACIAL
from django.http import FileResponse
import uuid

logger = logging.getLogger(__name__)

llm_lock: threading.Lock = threading.Lock()
ollama_server = os.environ.get('ollama_server', 'http://host.docker.internal:11434')




# =================== GEOSPATIAL APIs ===================

@extend_schema(
    methods=["POST"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "layers": {"type": "array"},
            },
        }
    },
    summary="Descubrir capas GeoJSON (POST)",
    description="Descubre capas GeoJSON disponibles en un contexto con metadata.",
    tags=["Geospatial"],
)
@api_view(["POST"])
def geospatial_discover_layers(request):
    """
    Descubre capas GeoJSON disponibles en un contexto.
    
    Input:
    {
      "context_id": int
    }
    
    Output:
    {
      "layers": [
        {
          "file_id": int,
          "filename": str,
          "geometry_type": str,
          "properties": [str],
          "feature_count": int,
          "bbox": [minx, miny, maxx, maxy] | None
        }
      ]
    }
    """
    try:
        payload = request.data
        context_id = payload.get('context_id')
        
        if not context_id:
            return JsonResponse({"error": "Se requiere context_id"}, status=400)
        
        try:
            context = Context.objects.get(id=context_id)
        except Context.DoesNotExist:
            return JsonResponse({"error": f"Contexto {context_id} no encontrado"}, status=404)
        
        # se obtiene las capas
        layers = DocumentEmbedding.discover_geojson_layers(context_id)
        
        logger.info(f"Descubiertas {len(layers)} capas GeoJSON en contexto {context_id}", flush=True)
        
        return JsonResponse({
            "layers": layers,
            "count": len(layers)
        }, status=200)
        
    except Exception as e:
        logger.error(f"Error en geospatial_discover_layers: {str(e)}")
        return JsonResponse({"error": str(e)}, status=500)

# Create your views here.
@extend_schema(
    methods=["POST"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "plan": {"type": "object"},
                "geojson": {"type": "object"},
                "feature_count": {"type": "integer"},
                "geometry_type": {"type": "string"},
            },
        }
    },
    summary="Ejecutar operaciones geoespaciales (POST)",
    description="Genera plan con IA y ejecuta operaciones geoespaciales.",
    tags=["Geospatial"],
)
@api_view(["POST"])
def geospatial_execute(request):
    """
    Genera plan y ejecuta operaciones geoespaciales.
    
    Input:
    {
      "context_id": int,
      "selected_layers": [int] | "all",
      "prompt": str,
      "model": str,
      "output_format": "geojson" | "url" | "file"  # default: "geojson"
    }
    
    Output (output_format="geojson"):
    {
      "plan": {...},
      "geojson": {...},
      "feature_count": int,
      "geometry_type": str
    }
    
    Output (output_format="url"):
    {
      "plan": {...},
      "url": str,
      "feature_count": int,
      "geometry_type": str
    }
    
    Output (output_format="file"):
    - Descarga directa del archivo GeoJSON
    """
    
    try:
        payload = request.data
        context_id = payload.get('context_id')
        selected_layers = payload.get('selected_layers')
        prompt = payload.get('prompt')
        model = payload.get('model')
        output_format = payload.get('output_format', 'geojson')
        
        if not context_id:
            return JsonResponse({"error": "Se requiere context_id"}, status=400)
        if not prompt:
            return JsonResponse({"error": "Se requiere prompt"}, status=400)
        if not model:
            return JsonResponse({"error": "Se requiere model"}, status=400)
        if output_format not in ['geojson', 'url', 'file']:
            return JsonResponse({"error": "output_format debe ser 'geojson', 'url' o 'file'"}, status=400)
        
        try:
            context = Context.objects.get(id=context_id)
        except Context.DoesNotExist:
            return JsonResponse({"error": f"Contexto {context_id} no encontrado"}, status=404)
        
        # se obtiene las capas
        available_layers = DocumentEmbedding.discover_geojson_layers(context_id)
        
        if not available_layers:
            return JsonResponse({"error": "No se encontraron capas GeoJSON en el contexto"}, status=404)
        
        print(f"available_layers: {available_layers}", flush=True)
        
        # Seleccionador de capas
        if selected_layers == "all":
            file_ids = [layer['file_id'] for layer in available_layers]
        elif isinstance(selected_layers, list):
            file_ids = selected_layers
        else:
            return JsonResponse({"error": "selected_layers debe ser 'all' o una lista de IDs"}, status=400)
        
        # Verificación de capas
        layers_dict = {layer['file_id']: layer for layer in available_layers if layer['file_id'] in file_ids}
        
        if not layers_dict:
            return JsonResponse({"error": "Ninguna de las capas seleccionadas está disponible"}, status=404)
        
        print(f"Generando plan geoespacial para {len(file_ids)} capas", flush=True)
        
        # Contexto para el modelo de IA
        layers_context = json.dumps(list(layers_dict.values()), indent=2)
        
        
        llm_prompt = f"""
CAPAS DISPONIBLES:
{layers_context}

SOLICITUD DEL USUARIO:
{prompt}

Genera el plan en JSON.
"""
        
        server = settings.OLLAMA_API_URL
        url = f"{server}/api/chat"
        
        plan_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": BASE_SYSTEM_PROMPT_GEOSPACIAL},
                {"role": "user", "content": f"{llm_prompt}\nRESPONDE ÚNICAMENTE CON EL JSON TÉCNICO SIGUIENDO EL ESQUEMA DE STEPS."},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0
            }
        }
        
        resp = requests.post(
            url, json=plan_payload, headers={"Content-Type": "application/json"}, timeout=500
        )
        resp.raise_for_status()
        data = resp.json()
        plan_str = data["message"]["content"].strip()
        
        logger.info(f"Raw LLM Response: {plan_str}")

        with open("respuesta_geomtry.txt", "w", encoding="utf-8") as f:
            f.write(BASE_SYSTEM_PROMPT_GEOSPACIAL)
            f.write(llm_prompt)
            f.write(plan_str)
        
        # Parsear plan robustamente (manejando bloques <think> y texto extra)
        try:
            # 1. limpieza de bloques <think> si existen
            import re
            plan_str_cleaned = re.sub(r'<think>.*?</think>', '', plan_str, flags=re.DOTALL).strip()
            
            # 2. buscador de bloque JSON (el primer { y el último })
            json_match = re.search(r'(\{.*\})', plan_str_cleaned, re.DOTALL)
            if json_match:
                plan_str_final = json_match.group(1)
                plan = json.loads(plan_str_final)
            else:
                # Si no hay llaves, procede a cargar el string limpio directamente
                plan = json.loads(plan_str_cleaned)
        except json.JSONDecodeError as e:
            logger.error(f"Error parseando plan JSON: {str(e)}\nPlan: {plan_str}")
            return JsonResponse({"error": f"Plan generado no es JSON válido: {str(e)}"}, status=500)
        
        logger.info(f"Plan generado: {json.dumps(plan, indent=2)}")
        
        # planificación del plan
        is_valid, error_msg = validate_plan(plan, layers_dict)
        if not is_valid:
            logger.error(f"Plan inválido: {error_msg}")
            return JsonResponse({"error": f"Plan inválido: {error_msg}", "plan": plan}, status=400)
        
        # carga de capas
        logger.info("Cargando datos GeoJSON a GeoDataFrame...")
        initial_gdfs = {}
        for file_id in file_ids:
            gdf = load_geojson_to_gdf([file_id])
            print(f"file_id: {file_id}, gdf.shape: {gdf}", flush=True)
            if not gdf.empty:
                initial_gdfs[file_id] = gdf
        
        if not initial_gdfs:
            return JsonResponse({"error": "No se pudieron cargar los datos GeoJSON"}, status=500)
        
        # Ejecución del plan
        logger.info("Ejecutando plan geoespacial...")
        result_gdf = execute_geospatial_plan(plan, initial_gdfs)
        
        if result_gdf.empty:
            return JsonResponse({"error": "El resultado del plan está vacío"}, status=500)
        
        # Obtención de metadata
        feature_count = len(result_gdf)
        geometry_type = get_geometry_type(result_gdf)
        
        if output_format == 'geojson':
            geojson_data = gdf_to_geojson_dict(result_gdf)
                        
            with open("respuesta_geomtry.json", "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)
            
            # Guardar archivo descargable
            import geopandas as gpd
            import shutil
            import uuid
            
            timestamp = int(time.time())
            prefix = f"ctx_{context_id}" if context_id else f"files_{len(file_ids)}"
            base_filename = f"localidades_{prefix}_{timestamp}"
            geojsons_dir = os.path.join(settings.MEDIA_ROOT, "geojsons")
            os.makedirs(geojsons_dir, exist_ok=True)
            
            # Validar output_format
            output_format = str(output_format).lower()
            if output_format not in ["geojson", "shp", "gpkg"]:
                output_format = "geojson"
                
            file_url = ""
            
            if output_format == "geojson":
                filename = f"{base_filename}.geojson"
                file_path = os.path.join(geojsons_dir, filename)
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(geojson_data, f, ensure_ascii=False, indent=2)
                file_url = f"{settings.MEDIA_URL}geojsons/{filename}"
                
            else:
                # Requerimos geopandas para shp y gpkg
                # Convertimos el FeatureCollection dict a GeoDataFrame
                if geojson_data:
                    gdf = gpd.GeoDataFrame.from_features(geojson_data["features"])
                    # Forzar CRS WGS84 ya que las coordenadas LLM vienen como lon, lat standard
                    gdf.set_crs(epsg=4326, inplace=True)
                else:
                    # Si está vacío creamos uno en blanco con columnas básicas para evitar fallos
                    import pandas as pd
                    from shapely.geometry import Point
                    gdf = gpd.GeoDataFrame(pd.DataFrame(columns=["name", "type", "context", "país", "estado"]), geometry=[], crs="EPSG:4326")
                
                if output_format == "gpkg":
                    filename = f"{base_filename}.gpkg"
                    file_path = os.path.join(geojsons_dir, filename)
                    gdf.to_file(file_path, driver="GPKG", layer="localidades")
                    file_url = f"{settings.MEDIA_URL}geojsons/{filename}"
                    
                elif output_format == "shp":
                    # Un shapefile son varios archivos, así que creamos un temporal, guardamos y comprimimos en zip
                    temp_shp_dir = os.path.join(geojsons_dir, f"shp_{base_filename}")
                    os.makedirs(temp_shp_dir, exist_ok=True)
                    
                    shp_path = os.path.join(temp_shp_dir, f"{base_filename}.shp")
                    gdf.to_file(shp_path, driver="ESRI Shapefile")
                    
                    zip_filename = f"{base_filename}.zip"
                    zip_path = os.path.join(geojsons_dir, zip_filename)
                    
                    # Crear el zip de todo el directorio
                    shutil.make_archive(zip_path.replace('.zip', ''), 'zip', temp_shp_dir)
                    
                    # Limpiar la carpeta temporal suelta
                    shutil.rmtree(temp_shp_dir)
                    
                    file_url = f"{settings.MEDIA_URL}geojsons/{zip_filename}"

            return JsonResponse({
                "plan": plan,
                "geojson": geojson_data,
                "feature_count": feature_count,
                "geometry_type": geometry_type,
                "file_url": file_url
            }, status=200)
        
        
    except Exception as e:
        logger.error(f"Error en geospatial_execute: {str(e)}")
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)
