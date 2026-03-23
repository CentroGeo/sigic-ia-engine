# serializers.py

from rest_framework import serializers
from .models import Geospatial

class GeoSpatializationListSerializer(serializers.ModelSerializer):
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Geospatial
        fields = [
            "id",
            "report_name",
            "export_format",
            "status",
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
