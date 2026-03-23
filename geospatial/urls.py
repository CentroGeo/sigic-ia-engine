from django.urls import path
from . import views

urlpatterns = [
    # Geospatial APIs
    path('', views.list_geospatial, name='list_geospatial'),
    path('<int:pk>', views.get_geospatial, name='get_geospatial'),
    path('discover', views.geospatial_discover_layers, name='geospatial-discover'),
    path('execute', views.geospatial_execute, name='geospatial-execute'),
    path('suggestions', views.discover_and_suggest_analysis, name='spatial_analysis_discover'),
    path('execute_async', views.geospatial_execute_async, name='geospatial-execute-async'),
]
