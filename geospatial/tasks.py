from celery import shared_task
from django.conf import settings
import time
import json
import os
import requests
import logging
import re
import io
import shutil
import geopandas as gpd
from datetime import datetime
from django.utils.text import slugify

from reports.models import Report
from fileuploads.models import Context, Files, DocumentEmbedding
from geospatial.geospatial_utils import (
    load_geojson_to_gdf,
    validate_plan,
    execute_geospatial_plan,
    gdf_to_geojson_dict,
    get_geometry_type
)
from geospatial.prompt_geospacial import BASE_SYSTEM_PROMPT_GEOSPACIAL

logger = logging.getLogger(__name__)

@shared_task(
    bind=True,
    name="geospatial.generate_operational",
    time_limit=1800,
    soft_time_limit=1500,
)
def generate_operational(self, report_id: int, base_url: str, authorization: str = "") -> dict:
    """
    Tarea Celery que genera el plan geoespacial, lo ejecuta y guarda el resultado.
    """
    try:
        logger.info(f"Iniciando generate_operational para report_id={report_id}")
        report = Report.objects.select_related("context").get(pk=report_id)
        report.status = "processing"
        report.task_id = self.request.id
        report.save(update_fields=["status", "task_id", "updated_date"])

        context_id = report.context_id
        prompt = report.instructions
        report_name = report.report_name
        # Usamos un valor por defecto para el modelo si no se especifica, 
        # o podríamos pasarlo como argumento si fuera necesario.
        model = "deepseek-r1:32b" 
        output_format = report.file_format if report.file_format in ['geojson', 'shp', 'gpkg'] else 'geojson'

        # 1. Descubrir capas
        logger.info(f"Descubriendo capas para el contexto {context_id}")
        available_layers = DocumentEmbedding.discover_geojson_layers(context_id)
        if not available_layers:
            logger.error(f"No se encontraron capas GeoJSON en el contexto {context_id}")
            raise ValueError(f"No se encontraron capas GeoJSON en el contexto {context_id}")

        # 2. Identificar archivos usados
        logger.info(f"Identificando archivos utilizados para el reporte {report_id}")
        file_ids = list(report.files_used.values_list("id", flat=True))
        if not file_ids:
            file_ids = [layer['file_id'] for layer in available_layers]

        layers_dict = {layer['file_id']: layer for layer in available_layers if layer['file_id'] in file_ids}
        if not layers_dict:
            raise ValueError("Ninguna de las capas seleccionadas está disponible")

        # 3. Llamada al LLM para generar el plan
        logger.info(f"Generando plan con LLM para el reporte {report_id} usando modelo {model}")
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
        logger.info(f"Respuesta del LLM recibida para el reporte {report_id}")
        
        # 4. Parsear plan
        logger.info(f"Parseando plan JSON para el reporte {report_id}")
        plan_str_cleaned = re.sub(r'<think>.*?</think>', '', plan_str, flags=re.DOTALL).strip()
        json_match = re.search(r'(\{.*\})', plan_str_cleaned, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group(1))
        else:
            plan = json.loads(plan_str_cleaned)

        # 5. Planificación y Validación
        plan_input_ids = set()
        step_outputs = set()
        for step in plan.get('steps', []):
            outputs = step.get('output_name')
            if outputs:
                step_outputs.add(outputs)
            for input_layer in step.get('input_layers', []):
                if input_layer not in step_outputs:
                    if isinstance(input_layer, int):
                        plan_input_ids.add(input_layer)
                    elif isinstance(input_layer, str):
                        for avail in available_layers:
                            if avail['filename'] == input_layer:
                                plan_input_ids.add(avail['file_id'])
                                break
        
        final_file_ids = list(set(file_ids) | plan_input_ids)
        all_context_layers_dict = {l['file_id']: l for l in available_layers}
        validation_dict = {fid: all_context_layers_dict[fid] for fid in final_file_ids if fid in all_context_layers_dict}
        
        is_valid, error_msg = validate_plan(plan, validation_dict)
        if not is_valid:
            logger.error(f"Validación fallida para el reporte {report_id}: {error_msg}")
            raise ValueError(f"Plan inválido: {error_msg}")

        # 6. Carga y Ejecución
        logger.info(f"Cargando capas e iniciando ejecución del plan para el reporte {report_id}")
        initial_gdfs = {}
        for f_id in final_file_ids:
            gdf = load_geojson_to_gdf([f_id])
            if not gdf.empty:
                initial_gdfs[f_id] = gdf
                if f_id in all_context_layers_dict:
                    fname = all_context_layers_dict[f_id]['filename']
                    initial_gdfs[fname] = gdf
        
        if not initial_gdfs:
            raise ValueError("No se pudieron cargar los datos GeoJSON")

        result_gdf = execute_geospatial_plan(plan, initial_gdfs)
        if result_gdf.empty:
            logger.error(f"El resultado del plan está vacío para el reporte {report_id}")
            raise ValueError(f"El resultado del plan está vacío")

        # 7. Exportación
        logger.info(f"Exportando resultados para el reporte {report_id} en formato {output_format}")
        geojson_data = gdf_to_geojson_dict(result_gdf)
        timestamp = int(time.time())
        prefix = f"ctx_{context_id}"
        base_filename = f"{slugify(report_name)}_{prefix}_{timestamp}"
        geojsons_dir = os.path.join(settings.MEDIA_ROOT, "geojsons")
        os.makedirs(geojsons_dir, exist_ok=True)

        file_url = ""
        filename = ""

        if output_format == "geojson":
            filename = f"{base_filename}.geojson"
            file_path = os.path.join(geojsons_dir, filename)
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)
            file_url = f"{settings.MEDIA_URL}geojsons/{filename}"
        elif output_format in ["shp", "gpkg"]:
            gdf = gpd.GeoDataFrame.from_features(geojson_data["features"])
            gdf.set_crs(epsg=4326, inplace=True)
            
            if output_format == "gpkg":
                filename = f"{base_filename}.gpkg"
                file_path = os.path.join(geojsons_dir, filename)
                gdf.to_file(file_path, driver="GPKG", layer="localidades")
                file_url = f"{settings.MEDIA_URL}geojsons/{filename}"
            elif output_format == "shp":
                temp_shp_dir = os.path.join(geojsons_dir, f"shp_{base_filename}")
                os.makedirs(temp_shp_dir, exist_ok=True)
                shp_path = os.path.join(temp_shp_dir, f"{base_filename}.shp")
                gdf.to_file(shp_path, driver="ESRI Shapefile")
                zip_filename = f"{base_filename}.zip"
                shutil.make_archive(os.path.join(geojsons_dir, base_filename), 'zip', temp_shp_dir)
                shutil.rmtree(temp_shp_dir)
                filename = zip_filename
                file_url = f"{settings.MEDIA_URL}geojsons/{zip_filename}"

        # 8. Actualizar Reporte
        report.status = "done"
        report.file_path = os.path.join("geojsons", filename).replace("\\", "/")
        report.geonode_url = base_url.rstrip("/") + file_url
        report.save(update_fields=["status", "file_path", "geonode_url", "updated_date"])

        logger.info(f"Tarea generate_operational completada exitosamente para report_id={report_id}")
        return {"report_id": report_id, "file_url": report.geonode_url}

    except Exception as exc:
        logger.error(f"Error en generate_operational report_id={report_id}: {exc}")
        if 'report' in locals():
            report.status = "error"
            report.error_message = str(exc)
            report.save(update_fields=["status", "error_message", "updated_date"])
        raise
