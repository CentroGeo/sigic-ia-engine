import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("fileuploads", "0008_documentembedding_metadata_json"),
    ]

    operations = [
        migrations.CreateModel(
            name="Report",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "context",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reports",
                        to="fileuploads.context",
                    ),
                ),
                (
                    "files_used",
                    models.ManyToManyField(
                        blank=True,
                        related_name="reports",
                        to="fileuploads.files",
                    ),
                ),
                ("report_name", models.CharField(max_length=255)),
                (
                    "report_type",
                    models.CharField(
                        choices=[
                            ("institutional", "Institucional"),
                            ("descriptive", "Descriptivo"),
                            ("summary", "Resumen"),
                            ("evaluation", "Evaluación"),
                        ],
                        max_length=50,
                    ),
                ),
                (
                    "output_format",
                    models.CharField(
                        choices=[
                            ("markdown", "Markdown"),
                            ("plain_text", "Texto plano"),
                        ],
                        default="markdown",
                        max_length=50,
                    ),
                ),
                (
                    "file_format",
                    models.CharField(
                        choices=[
                            ("pdf", "PDF"),
                            ("word", "Word (.docx)"),
                            ("csv", "CSV"),
                        ],
                        default="pdf",
                        max_length=20,
                    ),
                ),
                ("text_format", models.JSONField(blank=True, null=True)),
                ("instructions", models.TextField(blank=True, default="")),
                ("use_letterhead", models.BooleanField(default=False)),
                ("user_id", models.EmailField(blank=True, max_length=254, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pendiente"),
                            ("processing", "Procesando"),
                            ("done", "Listo"),
                            ("error", "Error"),
                        ],
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("task_id", models.CharField(blank=True, max_length=255, null=True)),
                ("file_path", models.CharField(blank=True, max_length=512, null=True)),
                ("error_message", models.TextField(blank=True, null=True)),
                ("created_date", models.DateTimeField(auto_now_add=True)),
                ("updated_date", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name="report",
            index=models.Index(fields=["context"], name="reports_rep_context_idx"),
        ),
        migrations.AddIndex(
            model_name="report",
            index=models.Index(fields=["report_type"], name="reports_rep_report_type_idx"),
        ),
        migrations.AddIndex(
            model_name="report",
            index=models.Index(fields=["status"], name="reports_rep_status_idx"),
        ),
        migrations.AddIndex(
            model_name="report",
            index=models.Index(fields=["user_id"], name="reports_rep_user_id_idx"),
        ),
    ]
