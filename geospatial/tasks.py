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

from geospatial.models import Geospatial
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
def generate_operational(self, report_id: int, base_url: str, authorization: str = "", refresh_token: str = "", operation: str = "") -> dict:
    """
    Tarea Celery que genera el plan geoespacial, lo ejecuta y guarda el resultado.
    """
    try:
        logger.info(f"Iniciando generate_operational para report_id={report_id}")
        report = Geospatial.objects.select_related("context").get(pk=report_id)
        report.status = "processing"
        report.task_id = self.request.id
        report.save(update_fields=["status", "task_id", "updated_date"])

        context_id = report.context_id
        prompt = report.instructions
        report_name = report.report_name
        # Usamos un valor por defecto para el modelo si no se especifica, 
        # o podríamos pasarlo como argumento si fuera necesario.
        model = "deepseek-r1:32b" 
        output_format = report.export_format if report.export_format in ['geojson', 'shp', 'gpkg'] else 'geojson'


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

        # 3. Generación del plan
        if operation == "interseccion":
            logger.info(f"Operación 'interseccion' detectada. Generando plan optimizado multipaso.")
            
            # Clasifica las capas por geometría
            point_ids = []
            polygon_ids = []
            for fid, layer in layers_dict.items():
                geom_type = layer.get('geometry_type', '').upper()
                if 'POINT' in geom_type or 'LINE' in geom_type:
                    point_ids.append(fid)
                elif 'POLYGON' in geom_type:
                    polygon_ids.append(fid)
                else:
                    point_ids.append(fid)

            steps = []
            current_points = None
            current_polys = None

            # 1. Agrupa los puntos
            if point_ids:
                if len(point_ids) > 1:
                    steps.append({"operation": "union", "input_layers": point_ids, "output_name": "merged_points"})
                    current_points = "merged_points"
                else:
                    current_points = point_ids[0]

            # 2. Intersecta los polígonos
            if polygon_ids:
                if len(polygon_ids) > 1:
                    steps.append({"operation": "intersection", "input_layers": polygon_ids, "output_name": "intersected_polys"})
                    current_polys = "intersected_polys"
                else:
                    current_polys = polygon_ids[0]

            # 3. Cruce final
            if current_points and current_polys:
                steps.append({"operation": "intersection", "input_layers": [current_points, current_polys], "output_name": "final_intersection"})
                final_output = "final_intersection"
            elif current_points:
                final_output = current_points
            elif current_polys:
                final_output = current_polys
            else:
                final_output = "empty_result"

            plan = {
                "steps": steps if steps else [{"operation": "union", "input_layers": [current_points or current_polys or file_ids[0]], "output_name": "final_output"}],
                "final_output": final_output if steps else "final_output"
            }
        elif operation == "buffer":
            logger.info(f"Operación 'buffer' detectada. Extrayendo distancia con LLM.")
            
            llm_prompt = f"""
                SOLICITUD DEL USUARIO:
                {prompt}
                
                Extrae la distancia mencionada para hacer un buffer y conviértela a metros.
                Si no menciona distancia explícitamente, usa 1000 por defecto.
                Responde ÚNICAMENTE con un JSON válido con la siguiente estructura y NADA MÁS:
                {{"distance": numero_en_metros}}
            """
            
            server = settings.OLLAMA_API_URL
            url = f"{server}/api/chat"
            
            plan_payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": llm_prompt},
                ],
                "stream": False,
                "format": "json",
                "options": {
                    "temperature": 0
                }
            }
            
            try:
                resp = requests.post(url, json=plan_payload, headers={"Content-Type": "application/json"}, timeout=60)
                resp.raise_for_status()
                data = resp.json()
                dist_str = data["message"]["content"].strip()
                dist_str_cleaned = re.sub(r'<think>.*?</think>', '', dist_str, flags=re.DOTALL).strip()
                json_match = re.search(r'(\{.*\})', dist_str_cleaned, re.DOTALL)
                if json_match:
                    dist_json = json.loads(json_match.group(1))
                else:
                    dist_json = json.loads(dist_str_cleaned)
                distance = float(dist_json.get("distance", 1000))
            except Exception as e:
                logger.error(f"Error extrayendo distancia del LLM, usando 1000m por defecto: {e}")
                distance = 1000.0
                
            steps = []
            if len(file_ids) > 1:
                steps.append({"operation": "union", "input_layers": file_ids, "output_name": "merged_for_buffer"})
                input_for_buffer = "merged_for_buffer"
            else:
                input_for_buffer = file_ids[0]
                
            steps.append({
                "operation": "buffer", 
                "input_layers": [input_for_buffer], 
                "parameters": {"distance": distance}, 
                "output_name": "final_buffer"
            })
            
            plan = {"steps": steps, "final_output": "final_buffer"}
        elif operation.lower() in ["densidad", "density"]:
            logger.info("Operación 'densidad' detectada. Generando plan automático multipaso.")
            point_ids = []
            polygon_ids = []
            for fid, layer in layers_dict.items():
                geom_type = layer.get('geometry_type', '').upper()
                if 'POINT' in geom_type or 'LINE' in geom_type:
                    point_ids.append(fid)
                elif 'POLYGON' in geom_type:
                    polygon_ids.append(fid)
                    
            if not point_ids or not polygon_ids:
                raise ValueError("La operación Densidad requiere al menos una capa de puntos y una de polígonos.")
                
            steps = []
            current_points = point_ids[0]
            if len(point_ids) > 1:
                steps.append({"operation": "union", "input_layers": point_ids, "output_name": "merged_points"})
                current_points = "merged_points"
                
            current_polys = polygon_ids[0]
            if len(polygon_ids) > 1:
                steps.append({"operation": "union", "input_layers": polygon_ids, "output_name": "merged_polys"})
                current_polys = "merged_polys"
                
            steps.append({
                "operation": "density",
                "input_layers": [current_points, current_polys],
                "output_name": "final_density"
            })
            
            plan = {"steps": steps, "final_output": "final_density"}
        else:
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
            logger.info(f"Respuesta del LLM recibida para el reporte {report_id}:\n{plan_str}")
            
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
        is_empty = result_gdf.empty
        if is_empty:
            logger.warning(f"El resultado del plan está vacío para el reporte {report_id}. Se devolverá una capa vacía.")

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
        content_type = ""   
        
        if output_format in ["shp", "gpkg"] and is_empty:
            logger.info("El resultado está vacío; forzando formato GeoJSON para evitar errores de esquema.")
            output_format = "geojson"

        if output_format == "geojson":
            filename = f"{base_filename}.geojson"
            file_path = os.path.join(geojsons_dir, filename)
            file_url = f"{settings.MEDIA_URL}geojsons/{filename}"
            content_type = "application/geo+json"
            
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(geojson_data, f, ensure_ascii=False, indent=2)
        elif output_format in ["shp", "gpkg"]:
            gdf = gpd.GeoDataFrame.from_features(geojson_data["features"])
            gdf.set_crs(epsg=4326, inplace=True)
            
            if output_format == "gpkg":
                filename = f"{base_filename}.gpkg"
                file_path = os.path.join(geojsons_dir, filename)
                gdf.to_file(file_path, driver="GPKG", layer="localidades")
                file_url = f"{settings.MEDIA_URL}geojsons/{filename}"
                content_type = "application/geopackage+sqlite3"
            elif output_format == "shp":
                temp_shp_dir = os.path.join(geojsons_dir, f"shp_{base_filename}")
                os.makedirs(temp_shp_dir, exist_ok=True)
                shp_path = os.path.join(temp_shp_dir, f"{base_filename}.shp")
                gdf.to_file(shp_path, driver="ESRI Shapefile")
                shutil.make_archive(os.path.join(geojsons_dir, base_filename), 'zip', temp_shp_dir)
                shutil.rmtree(temp_shp_dir)
                
                filename = f"{base_filename}.zip"
                file_path = os.path.join(geojsons_dir, filename)
                file_url = f"{settings.MEDIA_URL}geojsons/{filename}"
                content_type = "application/zip"
        
        if not is_empty:
            try:
                import io
                from fileuploads.utils import upload_image_to_geonode
                
                with open(file_path, "rb") as f:
                    file_bytes = f.read()
                    
                file_obj = io.BytesIO(file_bytes)
                file_obj.name = os.path.basename(file_path)
                file_obj.content_type = content_type
                
                response = upload_image_to_geonode(file_obj, os.path.basename(file_path), token=authorization, refresh_token=refresh_token)
                if response is not None:
                    print(f"[Análisis espacial-DEBUG] GeoNode Upload HTTP Status: {response.status_code}", flush=True)
                    if response.status_code < 400:
                        try:
                            response_data = response.json()
                            print(f"[Análisis espacial-DEBUG] GeoNode Upload JSON: {response_data}", flush=True)
                            relative_url = response_data.get("url", "")
                            if relative_url:
                                geonode_base = os.environ.get("GEONODE_SERVER", "").rstrip("/")
                                file_url = f"{geonode_base}{relative_url}"
                                print(f"[Análisis espacial-DEBUG] Archivo subido a geonode correctamente: {file_url}", flush=True)
                            else:
                                print("[Análisis espacial-DEBUG] Respuesta exitosa pero NO contiene clave 'url'", flush=True)
                        except Exception as json_exc:
                            print(f"[Análisis espacial-DEBUG] Error decodificando JSON de GeoNode. Content: {response.text[:200]}...", flush=True)
                    else:
                        print(f"[Análisis espacial-DEBUG] Fallo al subir archivo a geonode HTTP {response.status_code}: {response.text[:200]}", flush=True)
                else:
                    print(f"[Análisis espacial-DEBUG] Fallo crítico: response was None", flush=True)
            except Exception as geo_e:
                import traceback
                traceback.print_exc()
                print(f"[Análisis espacial-DEBUG] Excepción subiendo archivo a geonode: {str(geo_e)}", flush=True)
        else:
            logger.info("Omitiendo subida a GeoNode debido a que la capa está vacía.")

        # 8. Actualizar Reporte
        report.status = "done"
        report.file_path = os.path.join("geojsons", filename).replace("\\", "/")
        report.geonode_url = file_url
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
