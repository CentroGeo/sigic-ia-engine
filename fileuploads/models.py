from django.db import models
from pgvector.django import VectorField

class Workspace(models.Model):
    title           = models.TextField()
    description     = models.TextField(null=True, blank=True)
    user_id         = models.UUIDField(null=True, blank=True)
    
    active          = models.BooleanField(default=True)
    public          = models.BooleanField(default=False)
    created_date    = models.DateTimeField(auto_now_add=True)
    
    is_delete       = models.BooleanField(default=False)
    image_type     = models.TextField(default='', blank=True)

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

class Files(models.Model):
    context         = models.ForeignKey(Context, on_delete=models.CASCADE, null=True)
    workspace       = models.ForeignKey(Workspace, on_delete=models.CASCADE, null=True)
    document_id     = models.UUIDField(null=True, blank=True)
    document_type   = models.TextField()
    user_id         = models.UUIDField(null=True, blank=True)
    filename        = models.TextField(default='')
    path            = models.TextField(default='')

class DocumentEmbedding(models.Model):
    file = models.ForeignKey('Files', on_delete=models.CASCADE, related_name='embeddings')
    text = models.TextField()
    embedding = VectorField(dimensions=384)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Embedding for File ID {self.file.id}"

