from django.db import models
from fileuploads.models import Context, Files

class Spatialization(models.Model):
    STATUS_CHOICES = [
        ("pending", "Pendiente"),
        ("processing", "Procesando"),
        ("done", "Listo"),
        ("error", "Error"),
    ]

    context = models.ForeignKey(Context, on_delete=models.CASCADE, related_name="spatializations")
    files_used = models.ManyToManyField(Files, related_name="spatializations", blank=True)

    report_name = models.CharField(max_length=255)
    entity_types = models.JSONField(null=True, blank=True)
    export_format = models.CharField(max_length=20, default="geojson")
    geometry_type = models.CharField(max_length=20, default="point")
    focus = models.CharField(max_length=255, null=True, blank=True)
    custom_instructions = models.TextField(null=True, blank=True)
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    progress = models.IntegerField(default=0)
    task_id = models.CharField(max_length=255, null=True, blank=True)
    geonode_url = models.URLField(max_length=512, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    user_id = models.EmailField(null=True, blank=True)

    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["context"]),
            models.Index(fields=["status"]),
            models.Index(fields=["user_id"]),
        ]

    def __str__(self):
        return f"{self.report_name} [{self.status}]"
