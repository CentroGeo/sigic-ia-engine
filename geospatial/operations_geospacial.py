OPERATIONS = {

    # =========================
    # 🟢 OPERACIONES 1 CAPA
    # =========================

    "heatmap": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["POINT"],
        "needs_property": True,
        "label": "Mapa de calor (densidad)"
    },

    "choropleth": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["POLYGON"],
        "needs_property": True,
        "label": "Mapa coroplético"
    },

    "buffer": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["ANY"],
        "needs_distance": True,
        "label": "Buffer"
    },

    "centroid": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["POLYGON", "LINESTRING"],
        "label": "Centroides"
    },

    "dissolve": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["POLYGON"],
        "needs_property": True,
        "label": "Disolver polígonos"
    },

    "simplify": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["ANY"],
        "needs_tolerance": True,
        "label": "Simplificar geometría"
    },

    "clip": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["ANY"],
        "needs_geometry": True,
        "label": "Recortar por geometría"
    },

    "area_length": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["POLYGON", "LINESTRING"],
        "label": "Calcular área / longitud"
    },

    "filter": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["ANY"],
        "needs_property": True,
        "needs_operator": True,
        "needs_value": True,
        "label": "Filtrar por atributo"
    },

    "filter_by_bbox": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["ANY"],
        "needs_bbox": True,
        "label": "Filtrar por cuadro delimitador (BBOX)"
    },

    # =========================
    # 🔵 OPERACIONES 2 CAPAS
    # =========================

    "spatial_join": {
        "min_layers": 2,
        "max_layers": 10,
        "requires_geom": ["ANY", "ANY"],
        "needs_predicate": True,
        "label": "Unión espacial"
    },

    "intersection": {
        "min_layers": 2,
        "max_layers": 10,
        "requires_geom": ["ANY", "ANY"],
        "label": "Intersección geométrica"
    },

    "union": {
        "min_layers": 1,
        "max_layers": 10,
        "requires_geom": ["ANY"],
        "label": "Unión geométrica / Agrupación de capas"
    },

    "difference": {
        "min_layers": 2,
        "max_layers": 2,
        "requires_geom": ["ANY", "ANY"],
        "label": "Diferencia geométrica"
    },

    "symmetric_difference": {
        "min_layers": 2,
        "max_layers": 2,
        "requires_geom": ["ANY", "ANY"],
        "label": "Diferencia simétrica"
    },

    "overlay_clip": {
        "min_layers": 2,
        "max_layers": 2,
        "requires_geom": ["ANY", "ANY"],
        "label": "Recorte por capa"
    },

    "nearest_join": {
        "min_layers": 2,
        "max_layers": 2,
        "requires_geom": ["ANY", "ANY"],
        "needs_distance": True,
        "label": "Unión por vecino más cercano"
    },

    "spatial_overlay_count": {
        "min_layers": 2,
        "max_layers": 10,
        "requires_geom": ["POLYGON"],
        "bbox": True,
        "label": "Intersección múltiple con conteo de capas"
    },

    "snap": {
        "min_layers": 2,
        "max_layers": 2,
        "requires_geom": ["POINT", "LINESTRING"],
        "label": "Ajustar puntos a la línea más cercana (snapping)"
    },

    "update_attributes": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["ANY"],
        "label": "Actualizar atributos de la capa"
    },

    "update_attribute": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["ANY"],
        "label": "Actualizar atributos de la capa (alias)"
    },
    "clustering": {
        "min_layers": 1,
        "max_layers": 1,
        "requires_geom": ["POINT"],
        "needs_distance": False,
        "label": "Clustering Espacial (DBSCAN)"
    }
}


"""
SELECT
    id,
    name,
    jsonb_typeof(data->'geometry') AS geometry_block,
    data->'geometry'->>'type'      AS geom_type,
    jsonb_object_keys(data->'properties') AS property_keys
    FROM geojson_table;
    
    
    
    ┌─────────────────────────────┐
    │ PostgreSQL (jsonb GeoJSON)  │
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ Descubrimiento de capas     │  ⚙️ Sistema
    │ (id, name, geom, props)     │
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ Inventario visible          │  👤 Usuario
    │ (tabla de capas)            │
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ Selección de capas          │  👤 Usuario
    │ (explícita o "todas")       │
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ Prompt en lenguaje natural  │  👤 Usuario
    │ ("quiero unir y hacer...")  │
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ Planner / Intérprete        │  🤖 IA
    │ (prompt → plan estruct.)    │
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ Validación de plan          │  ⚙️ Sistema
    │ (reglas, capas, geometría)  │
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ Carga de datos (lazy)       │  ⚙️ Sistema
    │ PostgreSQL → GeoDataFrame   │
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ execute_plan()              │  ⚙️ Sistema
    │ (paso a paso)               │
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ GeoDataFrame final          │  ⚙️ Sistema
    └──────────────┬──────────────┘
                │
                ▼
    ┌─────────────────────────────┐
    │ Exportación GeoJSON         │  ⚙️ Sistema
    │ (archivo / API / descarga)  │
    └─────────────────────────────┘

"""
