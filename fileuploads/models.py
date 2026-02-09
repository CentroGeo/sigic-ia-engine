from django.db import connection
from django.db import models
from pgvector.django import VectorField
import uuid
import os
import json

def user_file_path(instance, filename):
    return os.path.join('uploads', 'workspaces', str(instance.workspace.id), 'files', filename)

class Workspace(models.Model):
    title           = models.TextField()
    description     = models.TextField(null=True, blank=True)
    user_id         = models.EmailField(null=True, blank=True)

    active          = models.BooleanField(default=True)
    public          = models.BooleanField(default=False)
    created_date    = models.DateTimeField(auto_now_add=True)

    is_delete       = models.BooleanField(default=False)
    image_type      = models.TextField(default='', blank=True)
    
    #indices
    class Meta:
        indexes = [
            models.Index(fields=['user_id']),
            models.Index(fields=['public']),
        ]

class Context(models.Model):
    workspace       = models.ForeignKey(Workspace, on_delete=models.CASCADE, null=True, related_name="contextos")
    
    title           = models.TextField()
    description     = models.TextField(null=True, blank=True)

    user_id         = models.EmailField(null=True, blank=True)
    active          = models.BooleanField(default=True)
    public          = models.BooleanField(default=True)
    created_date    = models.DateTimeField(auto_now_add=True)

    is_delete       = models.BooleanField(default=False)
    image_type      = models.TextField(default='')

    files           = models.ManyToManyField("Files", related_name="contexts", blank=True)
    
    #indices
    class Meta:
        indexes = [
            models.Index(fields=['workspace']),
        ]

class Files(models.Model):
    workspace        = models.ForeignKey(Workspace, on_delete=models.CASCADE, null=True)
    geonode_id       = models.IntegerField(null=True, blank=True)
    geonode_uuid     = models.UUIDField(null=True, blank=True)
    geonode_type     = models.TextField(default='')
    geonode_category = models.TextField(default='')
    document_type    = models.TextField()
    user_id          = models.EmailField(null=True, blank=True)
    filename         = models.TextField(default='')
    path             = models.TextField(default='')   
    processed        = models.BooleanField(default=False)
    language         = models.CharField(max_length=10, default='es')
    created_date     = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['workspace']),
            models.Index(fields=['language']),
        ]

class DocumentEmbedding(models.Model):
    file            = models.ForeignKey(Files, on_delete=models.CASCADE, related_name='chunks')
    chunk_index     = models.IntegerField()
    text            = models.TextField()
    text_json       = models.JSONField(default=dict, blank=True, null=True)
    metadata_json   = models.JSONField(default=dict, blank=True, null=True)
    embedding       = VectorField(dimensions=768)  # nomic-embed-text-v2 usa 768 dimensiones
    language        = models.CharField(max_length=10, default='es')
    metadata        = models.JSONField(default=dict)
    created_date    = models.DateTimeField(auto_now_add=True)
    
    #indices
    class Meta:
        indexes = [
            models.Index(fields=['file']),
            models.Index(fields=['language']),
        ]

    @staticmethod
    def get_json_keys_with_types(list_files_json_ids):
        query = """
            SELECT 
                j.key,
                jsonb_typeof(j.value) AS value_type,
                COUNT(*) AS count_rows
            FROM fileuploads_documentembedding f,
                 LATERAL jsonb_each(f.metadata_json::jsonb) AS j(key, value)
            where file_id = ANY(%s)
            GROUP BY j.key, jsonb_typeof(j.value)
            ORDER BY j.key;
        """
        if not list_files_json_ids:
            return []
                
        with connection.cursor() as cursor:
            cursor.execute(query, [list_files_json_ids])
            return [
                {"key": k, "type": t, "count": c}
                for k, t, c in cursor.fetchall()
            ]
    
    @staticmethod
    def discover_geojson_layers(context_id):
        """
        Descubre capas GeoJSON disponibles en un contexto.
        
        Args:
            context_id: ID del contexto
            
        Returns:
            Lista de diccionarios con metadata de cada capa:
            [
                {
                    "file_id": int,
                    "filename": str,
                    "geometry_type": str,
                    "properties": [str],
                    "feature_count": int,
                    "bbox": [minx, miny, maxx, maxy] | None
                }
            ]
        """
        query = """
            SELECT 
                f.file_id,
                fi.filename,
                f.text_json
            FROM fileuploads_documentembedding f
            JOIN fileuploads_files fi ON f.file_id = fi.id
            JOIN fileuploads_context_files cf ON fi.id = cf.files_id
            WHERE cf.context_id = %s
            AND f.text_json IS NOT NULL
            AND fi.document_type = 'application/json'
        """
        
        layers = []
        
        with connection.cursor() as cursor:
            cursor.execute(query, [context_id])
            rows = cursor.fetchall()
            
            for file_id, filename, geojson_data in rows:
                if isinstance(geojson_data, str):
                    try:
                        geojson_data = json.loads(geojson_data)
                    except json.JSONDecodeError:
                        continue

                if not isinstance(geojson_data, dict):
                    continue

                
                layer_info = {
                    "file_id": file_id,
                    "filename": filename,
                    "geometry_type": None,
                    "properties": [],
                    "feature_count": 0,
                    "bbox": None
                }
                
                # Procesar según tipo de GeoJSON
                features = []
                if geojson_data.get('type') == 'Feature':
                    features = [geojson_data]
                elif geojson_data.get('type') == 'FeatureCollection':
                    features = geojson_data.get('features', [])
                
                if features:
                    layer_info['feature_count'] = len(features)
                    
                    # Obtener tipo de geometría del primer feature
                    first_geom = features[0].get('geometry', {})
                    layer_info['geometry_type'] = first_geom.get('type', 'Unknown')
                    
                    # Obtener propiedades del primer feature
                    first_props = features[0].get('properties', {})
                    layer_info['properties'] = list(first_props.keys())
                    
                    # Calcular bbox si está disponible
                    if 'bbox' in geojson_data:
                        layer_info['bbox'] = geojson_data['bbox']
                
                layers.append(layer_info)
        
        return layers

# from django.db import models
# from pgvector.django import VectorField

# class Workspace(models.Model):
#     title           = models.TextField()
#     description     = models.TextField(null=True, blank=True)
#     user_id         = models.UUIDField(null=True, blank=True)
    
#     active          = models.BooleanField(default=True)
#     public          = models.BooleanField(default=False)
#     created_date    = models.DateTimeField(auto_now_add=True)
    
#     is_delete       = models.BooleanField(default=False)
#     image_type     = models.TextField(default='', blank=True)

# class Context(models.Model):
#     workspace       = models.ForeignKey(Workspace, on_delete=models.CASCADE, null=True, related_name="contextos")
    
#     title           = models.TextField()
#     description     = models.TextField(null=True, blank=True)
    
#     user_id         = models.UUIDField(null=True, blank=True)
#     active          = models.BooleanField(default=True)
#     public          = models.BooleanField(default=True)
#     created_date    = models.DateTimeField(auto_now_add=True)
    
#     is_delete       = models.BooleanField(default=False)
#     image_type      = models.TextField(default='')

# class Files(models.Model):
#     context         = models.ForeignKey(Context, on_delete=models.CASCADE, null=True)
#     workspace       = models.ForeignKey(Workspace, on_delete=models.CASCADE, null=True)
#     document_id     = models.UUIDField(null=True, blank=True)
#     document_type   = models.TextField()
#     user_id         = models.UUIDField(null=True, blank=True)
#     filename        = models.TextField(default='')
#     path            = models.TextField(default='')

# class DocumentEmbedding(models.Model):
#     file = models.ForeignKey('Files', on_delete=models.CASCADE, related_name='embeddings')
#     text = models.TextField()
#     embedding = VectorField(dimensions=384)
#     #embedding = VectorField(dimensions=1536)

#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return f"Embedding for File ID {self.file.id}"

