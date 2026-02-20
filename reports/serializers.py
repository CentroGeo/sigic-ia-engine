from rest_framework import serializers


class PptxReportRequestSerializer(serializers.Serializer):
    report_name = serializers.CharField()
    report_type = serializers.CharField()
    guided_prompt = serializers.CharField(required=False, allow_blank=True, default="")
    file_ids = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False
    )
    top_k = serializers.IntegerField(required=False, default=20, min_value=1, max_value=50)
