from rest_framework import serializers

from fileuploads.models import Files
from localidades.models import Spatialization

class SpatializationCreateSerializer(serializers.Serializer):
    context_id = serializers.IntegerField()
    file_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=True, # Pueden no enviar files y usar el context entero
        required=False,
    )
    report_name = serializers.CharField(max_length=255)
    entity_types = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=True,
        required=False,
    )
    export_format = serializers.ChoiceField(
        choices=["geojson", "shp", "gpkg"],
        default="geojson",
        required=False,
    )
    geometry_type = serializers.ChoiceField(
        choices=["point", "centroid", "polygon"],
        default="point",
        required=False,
    )
    focus = serializers.CharField(max_length=255, required=False, allow_blank=True, default="auto")
    custom_instructions = serializers.CharField(required=False, allow_blank=True, default="")
    refresh_token = serializers.CharField(required=False, allow_blank=True, default="")

    def validate(self, data):
        context_id = data.get("context_id")
        file_ids = data.get("file_ids", [])
        
        if file_ids:
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


class SpatializationSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Spatialization
        fields = [
            "id",
            "context",
            "report_name",
            "entity_types",
            "export_format",
            "geometry_type",
            "focus",
            "custom_instructions",
            "user_id",
            "status",
            "progress",
            "task_id",
            "geonode_url",
            "error_message",
            "created_date",
            "updated_date",
            "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        if not obj.geonode_url:
            return None
        if obj.geonode_url.startswith("http"):
            return obj.geonode_url
        request = self.context.get("request")
        if request is None:
            return obj.geonode_url
        return request.build_absolute_uri(obj.geonode_url)

class SpatializationListSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Spatialization
        fields = [
            "id",
            "report_name",
            "export_format",
            "status",
            "progress",
            "geonode_url",
            "created_date",
            "download_url",
        ]

    def get_download_url(self, obj):
        if not obj.geonode_url:
            return None
        if obj.geonode_url.startswith("http"):
            return obj.geonode_url
        request = self.context.get("request")
        if request is None:
            return obj.geonode_url
        return request.build_absolute_uri(obj.geonode_url)
