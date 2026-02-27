from rest_framework import serializers

from fileuploads.models import Files
from reports.models import Report


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

class ReportCreateSerializer(serializers.Serializer):
    context_id = serializers.IntegerField()
    file_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
    )
    report_name = serializers.CharField(max_length=255)
    report_type = serializers.ChoiceField(
        choices=["institutional", "descriptive", "summary", "evaluation"],
    )
    output_format = serializers.ChoiceField(
        choices=["markdown", "plain_text"],
        default="markdown",
        required=False,
    )
    file_format = serializers.ChoiceField(
        choices=["pdf", "word", "csv", "pptx", "txt"],
        default="pdf",
        required=False,
    )
    text_format = serializers.DictField(required=False, allow_null=True, default=None)
    instructions = serializers.CharField(required=False, allow_blank=True, default="")
    use_letterhead = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        context_id = data.get("context_id")
        file_ids = data.get("file_ids", [])

        # Verificar que todos los file_ids pertenecen al context_id
        valid_ids = set(
            Files.objects.filter(
                contexts__id=context_id,
                id__in=file_ids,
            ).values_list("id", flat=True)
        )
        invalid = set(file_ids) - valid_ids
        if invalid:
            raise serializers.ValidationError(
                {"file_ids": f"Los siguientes IDs no pertenecen al contexto {context_id}: {sorted(invalid)}"}
            )
        return data


class ReportSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Report
        fields = [
            "id",
            "context",
            "report_name",
            "report_type",
            "output_format",
            "file_format",
            "text_format",
            "instructions",
            "use_letterhead",
            "user_id",
            "status",
            "task_id",
            "file_path",
            "geonode_id",
            "geonode_url",
            "error_message",
            "created_date",
            "updated_date",
            "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        if obj.geonode_url:
            return obj.geonode_url
        if not obj.file_path:
            return None
        request = self.context.get("request")
        if request is None:
            return None
        from django.conf import settings
        media_url = getattr(settings, "MEDIA_URL", "/media/")
        return request.build_absolute_uri(media_url + obj.file_path)


class ReportListSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Report
        fields = [
            "id",
            "report_name",
            "report_type",
            "file_format",
            "status",
            "geonode_id",
            "geonode_url",
            "created_date",
            "download_url",
        ]

    def get_download_url(self, obj):
        if obj.geonode_url:
            return obj.geonode_url
        if not obj.file_path:
            return None
        request = self.context.get("request")
        if request is None:
            return None
        from django.conf import settings
        media_url = getattr(settings, "MEDIA_URL", "/media/")
        return request.build_absolute_uri(media_url + obj.file_path)
