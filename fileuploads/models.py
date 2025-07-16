from django.db import models
import uuid


class Workspace(models.Model):
    title           = models.TextField()
    description     = models.TextField(null=True, blank=True)
    user_id         = models.UUIDField(null=True, blank=True)
    
    active          = models.BooleanField(default=True)
    public          = models.BooleanField(default=False)
    created_date    = models.DateTimeField(auto_now_add=True)
    
    is_delete       = models.BooleanField(default=False)
    image_uuid      = models.TextField(default='', blank=True)

class Context(models.Model):
    workspace       = models.ForeignKey(Workspace, on_delete=models.CASCADE, null=True)
    
    title           = models.TextField()
    description     = models.TextField(null=True, blank=True)
    
    user_id         = models.UUIDField(null=True, blank=True)
    active          = models.BooleanField(default=True)
    public          = models.BooleanField(default=True)
    created_date    = models.DateTimeField(auto_now_add=True)
    
    is_delete       = models.BooleanField(default=False)
    type_image      = models.TextField(default='')

class Indexado(models.Model):
    indexado_id     = models.UUIDField(null=True, blank=True)
    document_id     = models.UUIDField(null=True, blank=True)
    type_document   = models.TextField()
    user_id         = models.UUIDField(null=True, blank=True)
    
    active          = models.BooleanField(default=True)
    public          = models.BooleanField(default=True)
    created_date    = models.DateTimeField(auto_now_add=True)
    is_delete       = models.BooleanField(default=False)

class Indexado_Context(models.Model):
    context        = models.ForeignKey(Context, on_delete=models.CASCADE, null=True)
    indexado       = models.ForeignKey(Indexado, on_delete=models.CASCADE, null=True)

