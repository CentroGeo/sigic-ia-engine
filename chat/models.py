from django.db import models
from fileuploads.models import Context

# Create your models here.
class History(models.Model):
    context       = models.ManyToManyField(Context, blank=True)
    user_id       = models.UUIDField()
    chat          = models.JSONField(null=True, blank=True)
    history_array = models.JSONField(null=True, blank=True)
    credate_date  = models.DateTimeField(auto_now_add=True)
    is_delete     = models.BooleanField(default=False)
    job_id        = models.UUIDField(null=True, blank=True)
    job_status    = models.TextField(null=True, blank=True)