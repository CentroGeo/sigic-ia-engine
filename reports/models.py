from django.db import models

from fileuploads.models import Context, Files


class Report(models.Model):
    REPORT_TYPE_CHOICES = [
        ("institutional", "Institucional"),
        ("descriptive", "Descriptivo"),
        ("summary", "Resumen"),
        ("evaluation", "Evaluación"),
    ]
    OUTPUT_FORMAT_CHOICES = [
        ("markdown", "Markdown"),
        ("plain_text", "Texto plano"),
    ]
    FILE_FORMAT_CHOICES = [
        ("pdf", "PDF"),
        ("word", "Word (.docx)"),
        ("csv", "CSV"),
    ]
    STATUS_CHOICES = [
        ("pending", "Pendiente"),
        ("processing", "Procesando"),
        ("done", "Listo"),
        ("error", "Error"),
    ]

    context = models.ForeignKey(Context, on_delete=models.CASCADE, related_name="reports")
    files_used = models.ManyToManyField(Files, related_name="reports", blank=True)

    report_name = models.CharField(max_length=255)
    report_type = models.CharField(max_length=50, choices=REPORT_TYPE_CHOICES)
    output_format = models.CharField(max_length=50, choices=OUTPUT_FORMAT_CHOICES, default="markdown")
    file_format = models.CharField(max_length=20, choices=FILE_FORMAT_CHOICES, default="pdf")
    text_format = models.JSONField(null=True, blank=True)
    instructions = models.TextField(blank=True, default="")
    use_letterhead = models.BooleanField(default=False)

    user_id = models.EmailField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    task_id = models.CharField(max_length=255, null=True, blank=True)
    file_path = models.CharField(max_length=512, null=True, blank=True)
    error_message = models.TextField(null=True, blank=True)

    created_date = models.DateTimeField(auto_now_add=True)
    updated_date = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["context"]),
            models.Index(fields=["report_type"]),
            models.Index(fields=["status"]),
            models.Index(fields=["user_id"]),
        ]

    def __str__(self):
        return f"{self.report_name} ({self.report_type}) [{self.status}]"
