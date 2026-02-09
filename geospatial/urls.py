from django.urls import path
from . import views

urlpatterns = [
    # Geospatial APIs
    path('discover', views.geospatial_discover_layers, name='geospatial-discover'),
    path('execute', views.geospatial_execute, name='geospatial-execute'),
]
