# serializers.py

from rest_framework import serializers
from .models import History
from fileuploads.models import Context, Workspace

class WorkspaceMiniSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ['id', 'title']

class ContextMiniSerializer(serializers.ModelSerializer):
    workspace = WorkspaceMiniSerializer()

    class Meta:
        model = Context
        fields = ['id', 'title', 'workspace']

class HistoryMiniSerializer(serializers.ModelSerializer):
    context = ContextMiniSerializer(many=True)

    class Meta:
        model = History
        fields = ['id', 'credate_date', 'context']
