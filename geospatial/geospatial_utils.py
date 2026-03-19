"""
Utilidades para operaciones geoespaciales.

Este módulo proporciona funciones para:
- Cargar GeoJSON desde PostgreSQL a GeoDataFrame
- Validar planes de operaciones geoespaciales
- Ejecutar operaciones geoespaciales paso a paso
- Exportar resultados a diferentes formatos
"""

import geopandas as gpd
import json
import logging
from typing import List, Dict, Any, Optional, Tuple
from shapely.geometry import shape, mapping
from django.db import connection
from fileuploads.models import DocumentEmbedding
from .operations_geospacial import OPERATIONS
import os
from django.conf import settings

logger = logging.getLogger(__name__)


def load_geojson_to_gdf(file_ids: List[int]) -> gpd.GeoDataFrame:
    """
    Carga datos GeoJSON desde PostgreSQL a GeoDataFrame.
    
    Args:
        file_ids: Lista de IDs de archivos a cargar
        
    Returns:
        GeoDataFrame con todos los features combinados
    """
    try:
        # Query para obtener los GeoJSON
        query = """
            SELECT 
                f.file_id,
                f.text_json
            FROM fileuploads_documentembedding f
            WHERE f.file_id = ANY(ARRAY[%s])
            AND f.text_json IS NOT NULL
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, [file_ids])
            rows = cursor.fetchall()
        
        if not rows:
            logger.warning(f"No se encontraron datos GeoJSON para file_ids: {file_ids}")
            return gpd.GeoDataFrame()
        
        # Combinación de todos los features
        all_features = []
        for file_id, geojson_data in rows:
            if isinstance(geojson_data, str):
                try:
                    geojson_data = json.loads(geojson_data)
                except json.JSONDecodeError:
                    continue
            
            if not isinstance(geojson_data, dict):
                continue
            
            # Si es un Feature
            if geojson_data.get('type') == 'Feature':
                feature = geojson_data.copy()
                feature['properties'] = feature.get('properties', {})
                feature['properties']['_file_id'] = file_id
                all_features.append(feature)
            # Si es un FeatureCollection
            elif geojson_data.get('type') == 'FeatureCollection':
                for feat in geojson_data.get('features', []):
                    feature = feat.copy()
                    feature['properties'] = feature.get('properties', {})
                    feature['properties']['_file_id'] = file_id
                    all_features.append(feature)
        
        if not all_features:
            logger.warning("No se encontraron features válidos en los GeoJSON")
            return gpd.GeoDataFrame()
        
        # Creación de GeoDataFrame
        gdf = gpd.GeoDataFrame.from_features(all_features)
        
        # Asegurar CRS (WGS84 por defecto)
        if gdf.crs is None:
            gdf.set_crs('EPSG:4326', inplace=True)
        
        logger.info(f"Cargados {len(gdf)} features desde {len(file_ids)} archivos")
        return gdf
        
    except Exception as e:
        logger.error(f"Error cargando GeoJSON a GeoDataFrame: {str(e)}")
        raise


def validate_plan(plan: Dict[str, Any], available_layers: Dict[int, Dict]) -> Tuple[bool, Optional[str]]:
    """
    Valida un plan de operaciones geoespaciales.
    
    Args:
        plan: Plan generado por la IA
        available_layers: Diccionario de capas disponibles {file_id: metadata}
        
    Returns:
        Tupla (es_valido, mensaje_error)
    """
    try:
        if 'steps' not in plan:
            return False, "El plan debe contener una lista de 'steps'"
        
        if not isinstance(plan['steps'], list):
            return False, "'steps' debe ser una lista"
        
        if len(plan['steps']) == 0:
            return False, "El plan debe contener al menos un paso"
        
        for i, step in enumerate(plan['steps']):
            # Validación de estructura del paso
            if 'operation' not in step:
                return False, f"Paso {i+1}: falta el campo 'operation'"
            
            operation_name = step['operation']
            
            # Validación de operación existente
            if operation_name not in OPERATIONS:
                return False, f"Paso {i+1}: operación '{operation_name}' no existe"
            
            operation_def = OPERATIONS[operation_name]
            
            # Validación de input_layers
            if 'input_layers' not in step:
                return False, f"Paso {i+1}: falta el campo 'input_layers'"
            
            input_layers = step['input_layers']
            if not isinstance(input_layers, list):
                return False, f"Paso {i+1}: 'input_layers' debe ser una lista"
            
            # Validación de número de capas
            num_layers = len(input_layers)
            if num_layers < operation_def['min_layers']:
                return False, f"Paso {i+1}: '{operation_name}' requiere al menos {operation_def['min_layers']} capas"
            
            if num_layers > operation_def['max_layers']:
                return False, f"Paso {i+1}: '{operation_name}' acepta máximo {operation_def['max_layers']} capas"
            
            # Validación de las las capas existen
            for layer_id in input_layers:
                if isinstance(layer_id, int) and layer_id not in available_layers:
                    return False, f"Paso {i+1}: capa {layer_id} no está disponible"
            
            # Validación de parámetros requeridos
            if operation_def.get('needs_property') and 'property' not in step.get('parameters', {}):
                return False, f"Paso {i+1}: '{operation_name}' requiere parámetro 'property'"
            
            if operation_def.get('needs_distance') and 'distance' not in step.get('parameters', {}):
                return False, f"Paso {i+1}: '{operation_name}' requiere parámetro 'distance'"
            
            if operation_def.get('needs_tolerance') and 'tolerance' not in step.get('parameters', {}):
                return False, f"Paso {i+1}: '{operation_name}' requiere parámetro 'tolerance'"
            
            if operation_def.get('needs_predicate') and 'predicate' not in step.get('parameters', {}):
                return False, f"Paso {i+1}: '{operation_name}' requiere parámetro 'predicate'"
            
            if operation_def.get('needs_operator') and 'operator' not in step.get('parameters', {}):
                return False, f"Paso {i+1}: '{operation_name}' requiere parámetro 'operator'"
            
            if operation_def.get('needs_value') and 'value' not in step.get('parameters', {}):
                return False, f"Paso {i+1}: '{operation_name}' requiere parámetro 'value'"
        
        return True, None
        
    except Exception as e:
        logger.error(f"Error validando plan: {str(e)}")
        return False, f"Error en validación: {str(e)}"


def execute_geospatial_plan(plan: Dict[str, Any], initial_layers: Dict[int, gpd.GeoDataFrame]) -> gpd.GeoDataFrame:
    """
    Ejecuta un plan de operaciones geoespaciales paso a paso.
    
    Args:
        plan: Plan validado
        initial_layers: Diccionario de GeoDataFrames iniciales {file_id: gdf}
        
    Returns:
        GeoDataFrame final
    """
    try:
        results = initial_layers.copy()
        
        for i, step in enumerate(plan['steps']):
            operation_name = step['operation']
            input_layer_ids = step['input_layers']
            parameters = step.get('parameters', {})
            output_name = step.get('output_name', f'step_{i+1}_result')
            
            logger.info(f"Ejecutando paso {i+1}: {operation_name}")
            
            # Obteneción de capas de entrada
            input_gdfs = []
            for layer_id in input_layer_ids:
                if isinstance(layer_id, int):
                    input_gdfs.append(results[layer_id])
                else:
                    input_gdfs.append(results[layer_id])
            
            # Ejecutación de operación
            result_gdf = execute_operation(operation_name, input_gdfs, parameters)
            
            # Limpiar columnas duplicadas que pueden generarse en sjoins / concats
            if not result_gdf.empty and result_gdf.columns.duplicated().any():
                result_gdf = result_gdf.loc[:, ~result_gdf.columns.duplicated(keep='first')]
            
            results[output_name] = result_gdf
        
        final_output = plan.get('final_output', f'step_{len(plan["steps"])}_result')
        return results[final_output]
        
    except Exception as e:
        logger.error(f"Error ejecutando plan: {str(e)}")
        raise


def execute_operation(operation: str, gdfs: List[gpd.GeoDataFrame], params: Dict) -> gpd.GeoDataFrame:
    """
    Ejecuta una operación geoespacial específica sobre uno o más GeoDataFrames.
    """
    try:
        # Normalización (ej: spatialOverlayCount -> spatialoverlaycount)
        op_norm = operation.lower().replace("_", "")
        
        # Operaciones de 1 capa
        if op_norm == 'buffer':
            distance = params.get('distance', 100)
            gdf = gdfs[0].copy()
            
            if gdf.empty:
                return gdf
                
            original_crs = gdf.crs if gdf.crs else 'EPSG:4326'
            
            if gdf.crs is None:
                gdf.set_crs('EPSG:4326', inplace=True)
            
            result_gdf = gdf.to_crs(gdf.estimate_utm_crs())
            result_gdf.geometry = result_gdf.buffer(distance)
            return result_gdf.to_crs(original_crs)
        
        elif op_norm == 'centroid':
            result = gdfs[0].copy()
            result['geometry'] = result.geometry.centroid
            return result
        
        elif op_norm == 'dissolve':
            by_column = params.get('property')
            return gdfs[0].dissolve(by=by_column)
        
        elif op_norm == 'simplify':
            tolerance = params.get('tolerance', 0.001)
            result = gdfs[0].copy()
            result['geometry'] = result.geometry.simplify(tolerance)
            return result

        elif op_norm == 'heatmap' or op_norm == 'choropleth':
            return gdfs[0].copy()

        elif op_norm == 'clip':
            if len(gdfs) > 1:
                mask = gdfs[1]
            else:
                raise ValueError("La operación 'clip' requiere una capa de máscara.")
            return gpd.clip(gdfs[0], mask)

        elif op_norm == 'arealength':
            result = gdfs[0].copy()
            if result.empty:
                return result
            if result.geometry.type.iloc[0] in ['Polygon', 'MultiPolygon']:
                result['area'] = result.geometry.area
            elif result.geometry.type.iloc[0] in ['LineString', 'MultiLineString']:
                result['length'] = result.geometry.length
            return result
        
        elif op_norm == 'filter':
            prop = params.get('property')
            filter_op = params.get('operator')
            val = params.get('value')
            
            gdf = gdfs[0].copy()
            
            if prop not in gdf.columns:
                logger.warning(f"La propiedad '{prop}' no existe en la capa. Omitiendo filtro.")
                return gdf
            
            if filter_op == '==' or filter_op == '=':
                return gdf[gdf[prop] == val]
            elif filter_op == '!=':
                return gdf[gdf[prop] != val]
            elif filter_op == '>':
                return gdf[gdf[prop] > val]
            elif filter_op == '<':
                return gdf[gdf[prop] < val]
            elif filter_op == '>=':
                return gdf[gdf[prop] >= val]
            elif filter_op == '<=':
                return gdf[gdf[prop] <= val]
            elif filter_op == 'contains':
                return gdf[gdf[prop].astype(str).str.contains(str(val), na=False)]
            elif filter_op == 'in':
                if isinstance(val, list):
                    return gdf[gdf[prop].isin(val)]
                else:
                    return gdf[gdf[prop].isin([val])]
            else:
                raise ValueError(f"Operador '{filter_op}' no soportado")
        
        elif op_norm == 'filterbybbox':
            bbox = params.get('bbox')
            if not bbox or len(bbox) != 4:
                raise ValueError("La operación 'filter_by_bbox' requiere un parámetro 'bbox'")
            
            gdf = gdfs[0].copy()
            if gdf.crs is None:
                gdf.set_crs('EPSG:4326', inplace=True)
            
            from shapely.geometry import box
            mask = box(*bbox)
            return gdf[gdf.geometry.intersects(mask)]
        
        elif op_norm == 'spatialjoin':
            predicate = params.get('predicate', 'intersects')
            if len(gdfs) < 2:
                raise ValueError("La operación 'spatial_join' requiere al menos 2 capas")
            
            res = gdfs[0].copy()
            for i in range(1, len(gdfs)):
                other = gdfs[i].copy()
                
                # Manejo de colisiones de nombres de columnas
                conflicting_cols = [c for c in other.columns if c in res.columns and c != 'geometry']
                if conflicting_cols:
                    rename_dict = {c: f"{c}_{i}" for c in conflicting_cols}
                    other = other.rename(columns=rename_dict)
                
                # Limpieza de índices de joins previos
                if 'index_right' in res.columns: res = res.drop(columns=['index_right'])
                if 'index_left' in res.columns: res = res.drop(columns=['index_left'])
                if 'index_right' in other.columns: other = other.drop(columns=['index_right'])
                if 'index_left' in other.columns: other = other.drop(columns=['index_left'])
                
                res = gpd.sjoin(res, other, how='inner', predicate=predicate)
            return res
        
        elif op_norm == 'intersection':
            if len(gdfs) < 2:
                raise ValueError("La operación 'intersection' requiere al menos 2 capas")
            
            res = gdfs[0].copy()
            for i in range(1, len(gdfs)):
                other = gdfs[i].copy()
                
                # Para evitar conflictos de columnas repetidas en uniones sucesivas, 
                # se limpia o renombra las columnas que ya existen en 'res'
                conflicting_cols = [c for c in other.columns if c in res.columns and c != 'geometry']
                if conflicting_cols:
                    # Renombrar columnas conflictivas con el índice de la capa
                    rename_dict = {c: f"{c}_{i}" for c in conflicting_cols}
                    other = other.rename(columns=rename_dict)

                res_type = get_geometry_type(res)
                other_type = get_geometry_type(other)
                
                if (res_type in ['Point', 'MultiPoint', 'LineString', 'MultiLineString'] and 
                    other_type in ['Polygon', 'MultiPolygon']):
                    if 'index_right' in res.columns: res = res.drop(columns=['index_right'])
                    res = gpd.sjoin(res, other, how='inner', predicate='intersects')
                elif (other_type in ['Point', 'MultiPoint', 'LineString', 'MultiLineString'] and 
                      res_type in ['Polygon', 'MultiPolygon']):
                    if 'index_right' in other.columns: other = other.drop(columns=['index_right'])
                    # En este caso 'other' tiene los puntos, el resultado debe conservar esa geometría
                    res = gpd.sjoin(other, res, how='inner', predicate='intersects')
                else:
                    res = gpd.overlay(res, other, how='intersection')
            return res
        
        elif op_norm == 'union':
            import pandas as pd
            if not gdfs:
                return gpd.GeoDataFrame()
            
            # Si todas tienen la misma geometría, concat es más seguro y rápido que overlay union
            all_types = [get_geometry_type(g) for g in gdfs]
            if len(set(all_types)) == 1:
                # Todas iguales: concatenar
                return gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)
            else:
                # Tipos mezclados: concatenar permitiendo mixed geometries
                return gpd.GeoDataFrame(pd.concat(gdfs, ignore_index=True), crs=gdfs[0].crs)
        
        elif op_norm == 'difference':
            return gpd.overlay(gdfs[0], gdfs[1], how='difference')
        
        elif op_norm == 'symmetricdifference':
            return gpd.overlay(gdfs[0], gdfs[1], how='symmetric_difference')
        
        elif op_norm == 'spatialoverlaycount':
            if len(gdfs) < 2:
                raise ValueError("La operación 'spatial_overlay_count' requiere al menos 2 capas")
            
            bbox = params.get('bbox')
            from shapely.geometry import box
            mask = box(*bbox) if bbox and len(bbox) == 4 else None

            base_crs = gdfs[0].crs if gdfs[0].crs else 'EPSG:4326'
            
            processed_gdfs = []
            for i, gdf in enumerate(gdfs):
                curr = gdf.copy()
                if curr.crs is None:
                    curr.set_crs('EPSG:4326', inplace=True)
                elif curr.crs != base_crs:
                    curr = curr.to_crs(base_crs)
                
                # Aplicar filtro espacial si hay BBOX
                if mask:
                    curr = curr[curr.geometry.intersects(mask)]
                
                if not curr.empty:
                    processed_gdfs.append(curr[['geometry']])
            
            if not processed_gdfs:
                return gpd.GeoDataFrame(columns=['geometry', 'overlap_count'], crs=base_crs)
            if len(processed_gdfs) == 1:
                res = processed_gdfs[0].copy()
                res['overlap_count'] = 1
                return res

            res = processed_gdfs[0].copy()
            res['_layer_idx_0'] = 1
            
            for idx, next_layer in enumerate(processed_gdfs[1:], 1):
                next_layer = next_layer.copy()
                tag = f'_layer_idx_{idx}'
                next_layer[tag] = 1
                res = gpd.overlay(res, next_layer, how='union')
            
            tag_cols = [c for c in res.columns if c.startswith('_layer_idx_')]
            res['overlap_count'] = res[tag_cols].notnull().sum(axis=1)
            res = res.drop(columns=tag_cols)
            return res[res['overlap_count'] > 0]

        elif op_norm == 'nearestjoin':
            distance = params.get('distance')
            # Compara que ambos GDFs tengan el mismo CRS
            if gdfs[0].crs != gdfs[1].crs:
                gdfs[1] = gdfs[1].to_crs(gdfs[0].crs)
            return gpd.sjoin_nearest(gdfs[0], gdfs[1], max_distance=distance, how='inner')

        elif op_norm == 'snap':
            if len(gdfs) < 2:
                raise ValueError("La operación 'snap' requiere 2 capas (puntos y líneas)")
            
            points = gdfs[0].copy()
            lines = gdfs[1].copy()
            
            # Asegura que ambos GDFs tengan el mismo CRS
            if points.crs != lines.crs:
                lines = lines.to_crs(points.crs)
            
            # Limpia las columnas de índice que puedan causar conflicto en sjoin_nearest
            if 'index_right' in points.columns:
                points = points.drop(columns=['index_right'])
            if 'index_left' in points.columns:
                points = points.drop(columns=['index_left'])
            
            # 1. Encontrar la línea más cercana para cada punto
            # sjoin_nearest devuelve un GDF con los atributos de la línea más cercana
            snapped = gpd.sjoin_nearest(points, lines, distance_col="_snap_dist", how='inner')
            
            # 2. Calcula la posición exacta sobre la línea
            def snap_point(row):
                point_geom = row.geometry
                # Obtiene la geometría de la línea usando el índice original
                line_idx = row['index_right']
                line_geom = lines.loc[line_idx].geometry
                # Proyecta el punto sobre la línea y obtener el punto más cercano
                return line_geom.interpolate(line_geom.project(point_geom))
            
            snapped.geometry = snapped.apply(snap_point, axis=1)
            # Limpia las columnas auxiliares
            if 'index_right' in snapped.columns:
                snapped = snapped.drop(columns=['index_right', '_snap_dist'])
            
            return snapped
        
        elif op_norm == 'updateattributes' or op_norm == 'updateattribute':
            gdf = gdfs[0].copy()
            new_props = params.get('new_properties', {})
            if not new_props and 'attribute' in params:
                new_props = {params['attribute']: params.get('value')}
            
            for key, val in new_props.items():
                gdf[key] = val
            return gdf

        elif op_norm == 'clustering':
            from sklearn.cluster import DBSCAN
            import numpy as np
            
            gdf = gdfs[0].copy()
            if gdf.empty:
                return gdf

            # Proyecta a UTM para usar metros reales en la distancia (eps)
            if gdf.crs is None:
                gdf.set_crs('EPSG:4326', inplace=True)
                
            # Convierte a UTM (metros)
            utm_crs = gdf.estimate_utm_crs()
            gdf_utm = gdf.to_crs(utm_crs)
            
            # Extrae coordenadas
            coords = np.column_stack((gdf_utm.geometry.x, gdf_utm.geometry.y))

            # Parámetros del clustering
            eps = params.get('distance', 500)      # distancia en metros
            min_samples = params.get('min_samples', 5)

            # Ejecuta DBSCAN
            db = DBSCAN(eps=eps, min_samples=min_samples)
            labels = db.fit_predict(coords)

            # Guardar resultado
            gdf['cluster_id'] = labels
            
            return gdf

        elif op_norm == 'spatialcorrelation':
            # Análisis de co-ocurrencia espacial entre dos capas
            if len(gdfs) < 2:
                raise ValueError("La operación 'spatial_correlation' requiere 2 capas")
            
            layer1 = gdfs[0].copy()
            layer2 = gdfs[1].copy()
            
            dist = params.get('distance', 500)
            
            # 1. Asegurar CRS
            if layer1.crs is None:
                layer1 = layer1.set_crs("EPSG:4326")
            if layer2.crs is None:
                layer2 = layer2.set_crs("EPSG:4326")

            # 2. Guardar CRS original para el retorno
            original_crs = layer1.crs

            # 3. Proyectar ambas capas a UTM para trabajar en metros si es necesario
            utm_crs = layer1.estimate_utm_crs()
            layer1_utm = layer1.to_crs(utm_crs)
            layer2_utm = layer2.to_crs(utm_crs)

            # 4. Preparar fuente de correlación (layer1)
            # Si es Punto, creamos buffer. Si es Polígono, usamos la geometría directamente 
            # (o agregamos el buffer de buffer si se desea extender el área)
            if all(layer1_utm.geometry.type == 'Point') or all(layer1_utm.geometry.type == 'MultiPoint'):
                layer1_source = layer1_utm.copy()
                layer1_source.geometry = layer1_source.buffer(dist)
            else:
                # Para polígonos, usamos el área misma. Si dist > 0, expandimos.
                layer1_source = layer1_utm.copy()
                if dist > 0:
                    layer1_source.geometry = layer1_source.buffer(dist)

            # 5. Join espacial: objetos de layer2 dentro de la influencia de layer1
            # Importante: sjoin devuelve un GeoDataFrame con la geometría de la IZQUIERDA (layer2)
            correlation = gpd.sjoin(
                layer2_utm,
                layer1_source,
                how="inner",
                predicate="intersects"
            )
            
            # Volver al CRS original
            correlation = correlation.to_crs(original_crs)
            
            # Limpiar columnas de sistema que podrían causar problemas en exportaciones sucesivas
            if 'index_right' in correlation.columns:
                correlation = correlation.drop(columns=['index_right'])

            # NOTA: En lugar de devolver un .groupby().size() (que es un DataFrame normal y rompe el flujo GIS),
            # devolvemos el GeoDataFrame de los puntos/objetos que están correlacionados.
            return correlation

        elif op_norm == 'density' or op_norm == 'countpointsinpolygon':
            # 1. Identificar capas: asume gdfs[0] son puntos y gdfs[1] son polígonos
            points = gdfs[0].copy()
            polygons = gdfs[1].copy()

            # Asegurar CRS
            if points.crs is None: points.set_crs("EPSG:4326", inplace=True)
            if polygons.crs is None: polygons.set_crs("EPSG:4326", inplace=True)
            
            if points.crs != polygons.crs:
                points = points.to_crs(polygons.crs)

            # 2. Join espacial para contar puntos por polígono
            # Limpiar índices previos
            if 'index_right' in points.columns: points = points.drop(columns=['index_right'])
            
            # Realizar el join
            joined = gpd.sjoin(points, polygons, how="inner", predicate="within")
            
            # 3. Contar por el índice del polígono
            counts = joined.index_right.value_counts().rename("point_count")
            
            # 4. Unir conteos al GeoDataFrame de polígonos original
            result = polygons.merge(counts, left_index=True, right_index=True, how="left")
            result["point_count"] = result["point_count"].fillna(0).astype(int)

            # 5. Calcular densidad (puntos / área_km2)
            # Proyectar a UTM para calcular área precisa en metros cuadrados
            utm_crs = polygons.estimate_utm_crs()
            polygons_utm = polygons.to_crs(utm_crs)
            area_m2 = polygons_utm.geometry.area
            area_km2 = area_m2 / 1_000_000.0
            
            # Evitar división por cero
            result["area_km2"] = area_km2
            result["point_density"] = result["point_count"] / result["area_km2"].replace(0, float('inf'))
            
            return result
        else:
            raise ValueError(f"Operación '{operation}' no implementada")
        
    except Exception as e:
        logger.error(f"Error ejecutando operación '{operation}': {str(e)}")
        raise


def gdf_to_geojson_dict(gdf: gpd.GeoDataFrame) -> Dict:
    """
    Convierte un GeoDataFrame a diccionario GeoJSON.
    
    Args:
        gdf: GeoDataFrame a convertir
        
    Returns:
        Diccionario en formato GeoJSON
    """
    try:
        # Limpiar columnas duplicadas antes de exportar
        if not gdf.empty and gdf.columns.duplicated().any():
            gdf = gdf.loc[:, ~gdf.columns.duplicated(keep='first')]
            
        # Converte a GeoJSON string y luego a dict
        geojson_str = gdf.to_json()
        return json.loads(geojson_str)
    except Exception as e:
        logger.error(f"Error convirtiendo GeoDataFrame a GeoJSON: {str(e)}")
        raise


def save_geojson_file(gdf: gpd.GeoDataFrame, filename: str) -> str:
    """
    Guarda un GeoDataFrame como archivo GeoJSON.
    
    Args:
        gdf: GeoDataFrame a guardar
        filename: Nombre del archivo (sin extensión)
        
    Returns:
        Path completo del archivo guardado
    """
    try:
        # Crear un directorio de salida si no existe
        output_dir = os.path.join(settings.MEDIA_ROOT, 'geospatial_results')
        os.makedirs(output_dir, exist_ok=True)
        
        # Path completo
        filepath = os.path.join(output_dir, f"{filename}.geojson")
        
        # Guardar el archivo
        gdf.to_file(filepath, driver='GeoJSON')
        
        logger.info(f"Archivo GeoJSON guardado en: {filepath}")
        return filepath
        
    except Exception as e:
        logger.error(f"Error guardando archivo GeoJSON: {str(e)}")
        raise


def get_geometry_type(gdf: gpd.GeoDataFrame) -> str:
    """
    Obtiene el tipo de geometría predominante en un GeoDataFrame.
    
    Args:
        gdf: GeoDataFrame
        
    Returns:
        Tipo de geometría como string
    """
    try:
        if len(gdf) == 0:
            return "Empty"
        
        # Obtener los tipos únicos
        geom_types = gdf.geometry.type.unique()
        
        if len(geom_types) == 1:
            return geom_types[0]
        else:
            return "Mixed"
            
    except Exception as e:
        logger.error(f"Error obteniendo tipo de geometría: {str(e)}")
        return "Unknown"


def suggest_spatial_analyses(layers):
    """
    Sugerir posibles análisis espaciales basados en la geometría y metadata de las capas.
    """
    suggestions = []
    
    # Clasificar capas por geometría
    points = [l for l in layers if l.get('geometry_type') in ['Point', 'MultiPoint']]
    polygons = [l for l in layers if l.get('geometry_type') in ['Polygon', 'MultiPolygon']]
    lines = [l for l in layers if l.get('geometry_type') in ['LineString', 'MultiLineString']]
    # Point in Polygon / Densidad
    if points and polygons:
        for p in points:
            for poly in polygons:
                suggestions.append({
                    "analysis": "Densidad",
                    "description": f"Calcular la densidad de puntos de '{p['filename']}' en cada área de '{poly['filename']}'",
                    "operation": "density",
                    "layers": [p['file_id'], poly['file_id']]
                })

    # Intersection/Union entre Polígonos
    if len(polygons) >= 2:
        for i in range(len(polygons)):
            for j in range(i + 1, len(polygons)):
                suggestions.append({
                    "analysis": "Intersection",
                    "description": f"Encontrar el área común entre '{polygons[i]['filename']}' y '{polygons[j]['filename']}'",
                    "operation": "intersection",
                    "layers": [polygons[i]['file_id'], polygons[j]['file_id']]
                })

    # Proximity (Buffer)
    for l in layers:
        if l.get('geometry_type') != 'Empty':
            suggestions.append({
                "analysis": "Buffer",
                "description": f"Crear un área de influencia alrededor de '{l['filename']}'",
                "operation": "buffer",
                "layers": [l['file_id']],
                "suggested_params": {"distance": 500}
            })

    # Snap (Puntos a Líneas)
    if points and lines:
        for p in points:
            for line in lines:
                suggestions.append({
                    "analysis": "Snapping",
                    "description": f"Ajustar los puntos de '{p['filename']}' a la línea más cercana de '{line['filename']}'",
                    "operation": "snap",
                    "layers": [p['file_id'], line['file_id']]
                })

    # Clustering Espacial (Agrupamiento)
    for p in points:
        suggestions.append({
            "analysis": "Clustering Espacial",
            "description": f"Identificar agrupamientos (Hotspots) de puntos en '{p['filename']}' usando técnicas de densidad",
            "operation": "clustering",
            "layers": [p['file_id']],
            "suggested_params": {"method": "dbscan", "radius": 500}
        })

    return suggestions
