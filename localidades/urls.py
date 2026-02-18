from django.urls import path
from .views import detect_localidades

urlpatterns = [
    path('detect/', detect_localidades, name='detect_localidades'),
]
