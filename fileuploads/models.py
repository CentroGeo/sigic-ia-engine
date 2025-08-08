from django.db import models
from pgvector.django import VectorField
import uuid
import os

def user_file_path(instance, filename):
    return os.path.join('uploads', 'workspaces', str(instance.workspace.id), 'files', filename)

class Workspace(models.Model):
    title           = models.TextField()
    description     = models.TextField(null=True, blank=True)
    user_id         = models.UUIDField(null=True, blank=True)

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

    user_id         = models.UUIDField(null=True, blank=True)
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
    workspace       = models.ForeignKey(Workspace, on_delete=models.CASCADE, null=True)
    document_id     = models.UUIDField(null=True, blank=True)
    document_type   = models.TextField()
    user_id         = models.UUIDField(null=True, blank=True)
    filename        = models.TextField(default='')
    path            = models.TextField(default='')   
    processed       = models.BooleanField(default=False)
    language        = models.CharField(max_length=10, default='es')
    created_date    = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['workspace']),
            models.Index(fields=['language']),
        ]

class DocumentEmbedding(models.Model):
    file            = models.ForeignKey(Files, on_delete=models.CASCADE, related_name='chunks')
    chunk_index     = models.IntegerField()
    text            = models.TextField()
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

