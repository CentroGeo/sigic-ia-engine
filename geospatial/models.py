from django.db import models
from fileuploads.models import Context, Files

# Create your models here.
class Geospatial(models.Model):
    REPORT_TYPE_CHOICES = [
        ("buffer", "Buffer"),
        ("interseccion", "Intersección"),
        ("densidad", "Densidad"),
        ("avanzado", "Avanzado")
    ]
    
    STATUS_CHOICES = [
        ("pending", "Pendiente"),
        ("processing", "Procesando"),
        ("done", "Listo"),
        ("error", "Error"),
    ]

    context = models.ForeignKey(Context, on_delete=models.CASCADE, related_name="geospatial_spatializations")
    files_used = models.ManyToManyField(Files, related_name="geospatial_files", blank=True)

    report_name = models.CharField(max_length=255)
    report_type = models.CharField(max_length=50, choices=REPORT_TYPE_CHOICES)
    
    export_format = models.CharField(max_length=20, default="geojson")
    instructions = models.TextField(blank=True, default="")
    
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    progress = models.IntegerField(default=0)
    task_id = models.CharField(max_length=255, null=True, blank=True)
    geonode_url = models.URLField(max_length=512, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)
    user_id = models.EmailField(null=True, blank=True)
    file_path = models.CharField(max_length=512, null=True, blank=True)


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
