from rest_framework.decorators import api_view, authentication_classes
from rest_framework.response import Response
from django.core.serializers import serialize
from rest_framework import status
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.http import StreamingHttpResponse
from django.core.serializers.json import DjangoJSONEncoder
from fileuploads.models import Workspace, Context, Files, DocumentEmbedding
from reports.models import Report
from shared.authentication import KeycloakAuthentication
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
        ##Payload
        payload = request.data
        context_id = payload.get('context_id')
        selected_layers = payload.get('file_ids')
        report_name = payload.get('report_name')
        prompt = payload.get('instructions')
        model = payload.get('model', 'deepseek-r1:32b')
        output_format = payload.get('export_format', 'geojson')
        
        print(f"payload: {payload}", flush=True)
        #validaciones de payload y context
        if not context_id:
            return JsonResponse({"error": "Se requiere context_id"}, status=400)
        if not prompt:
            return JsonResponse({"error": "Se requiere prompt"}, status=400)
        if not model:
            return JsonResponse({"error": "Se requiere model"}, status=400)
        if output_format not in ['geojson', 'url', 'file']:
            return JsonResponse({"error": "output_format debe ser 'geojson', 'url' o 'file'"}, status=400)
        
        #se obtiene el contexto
        try:
            context = Context.objects.get(id=context_id)
        except Context.DoesNotExist:
            return JsonResponse({"error": f"Contexto {context_id} no encontrado"}, status=404)
        
        # se obtiene las capas del contexto
        available_layers = DocumentEmbedding.discover_geojson_layers(context_id)
        
        if not available_layers:
            return JsonResponse({"error": "No se encontraron capas GeoJSON en el contexto"}, status=404)
        
        
        #print(f"available_layers: {available_layers}", flush=True)
        
        # filtro de capas seleccionadas
        if not selected_layers:
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
        
        # manejo de bloques <think> y texto extra
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
        with open("plan_layers.txt", "w", encoding="utf-8") as f:
            f.write(json.dumps(plan, indent=2))
            
        # planificación del plan
        # Antes de validar, identificamos qué capas se están usando realmente en el plan
        # Esto permite cargar automáticamente capas mencionadas por nombre o IDs fuera de la selección inicial
        plan_input_ids = set()
        step_outputs = set()
        for step in plan.get('steps', []):
            outputs = step.get('output_name')
            if outputs:
                step_outputs.add(outputs)
            
            for input_layer in step.get('input_layers', []):
                # Si no es un resultado de un paso previo, es una capa inicial
                if input_layer not in step_outputs:
                    # Si es ID numérico
                    if isinstance(input_layer, int):
                        plan_input_ids.add(input_layer)
                    # Si es nombre de archivo (string)
                    elif isinstance(input_layer, str):
                        # Buscar el ID correspondiente al nombre de archivo en las capas disponibles
                        for avail in available_layers:
                            if avail['filename'] == input_layer:
                                plan_input_ids.add(avail['file_id'])
                                break
        
        # Combinamos las capas seleccionadas inicialmente con las que el plan realmente necesita
        final_file_ids = list(set(file_ids) | plan_input_ids)
        
        # Actualizamos layers_dict para la validación con todas las capas disponibles en el contexto
        # que están siendo referenciadas en el plan
        all_context_layers_dict = {l['file_id']: l for l in available_layers}
        validation_dict = {fid: all_context_layers_dict[fid] for fid in final_file_ids if fid in all_context_layers_dict}
        
        is_valid, error_msg = validate_plan(plan, validation_dict)
        if not is_valid:
            logger.error(f"Plan inválido: {error_msg}")
            return JsonResponse({"error": f"Plan inválido: {error_msg}", "plan": plan}, status=400)
        
        # carga de capas
        logger.info("Cargando datos GeoJSON a GeoDataFrame...")
        initial_gdfs = {}
        for f_id in final_file_ids:
            gdf = load_geojson_to_gdf([f_id])
            if not gdf.empty:
                initial_gdfs[f_id] = gdf
                # mapeo por nombre de archivo para que el motor lo encuentre si el LLM usó el string
                if f_id in all_context_layers_dict:
                    fname = all_context_layers_dict[f_id]['filename']
                    initial_gdfs[fname] = gdf
        
        if not initial_gdfs:
            return JsonResponse({"error": "No se pudieron cargar los datos GeoJSON para las capas requeridas"}, status=500)
        
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
            base_filename = f"{report_name}_{prefix}_{timestamp}"
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
        print(f"Error en geospatial_execute: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return JsonResponse({"error": str(e)}, status=500)


@extend_schema(
    methods=["POST"],
    responses={
        202: {
            "type": "object",
            "properties": {
                "report_id": {"type": "integer"},
                "task_id": {"type": "string"},
                "status": {"type": "string"},
            },
        }
    },
    summary="Ejecutar operaciones geoespaciales asíncronas (POST)",
    description="Crea un Report y dispara la tarea Celery para ejecución geoespacial.",
    tags=["Geospatial"],
)
@api_view(["POST"])
@authentication_classes([KeycloakAuthentication])
def geospatial_execute_async(request):
    """
    Crea un Report y dispara la tarea Celery.
    """
    try:
        payload = request.data
        context_id = payload.get('context_id')
        selected_layers = payload.get('file_ids')
        report_name = payload.get('report_name', 'Reporte Geoespacial')
        prompt = payload.get('instructions')
        model = payload.get('model', 'deepseek-r1:32b')
        output_format = payload.get('export_format', 'geojson')
        
        print(f"{payload}", flush=True)
        if not context_id or not prompt:
            return JsonResponse({"error": "context_id e instructions son requeridos"}, status=400)

        context = Context.objects.get(id=context_id)
        
        # Obtener user_id desde el token JWT
        user_id = None
        if hasattr(request, "user") and request.user and hasattr(request.user, "payload"):
            user_id = request.user.payload.get("email")

        # Crear el objeto Report
        report = Report.objects.create(
            context=context,
            report_name=report_name,
            report_type="geospatial", # Tipo fijo para este flujo
            file_format=output_format, # Usamos export_format aquí
            instructions=prompt,
            user_id=user_id,
            status="pending",
        )

        # Asociar archivos si se proporcionaron
        if selected_layers and isinstance(selected_layers, list):
            files = Files.objects.filter(id__in=selected_layers)
            report.files_used.set(files)

        # Disparar tarea Celery
        from geospatial.tasks import generate_operational
        base_url = request.build_absolute_uri("/").rstrip("/")
        authorization = request.headers.get("Authorization", "")
        task = generate_operational.delay(report.id, base_url, authorization)

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

    except Context.DoesNotExist:
        return JsonResponse({"error": f"Contexto {context_id} no encontrado"}, status=404)
    except Exception as e:
        logger.error(f"Error en geospatial_execute_async: {str(e)}")
        return JsonResponse({"error": str(e)}, status=500)
