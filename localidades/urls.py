from django.urls import path
from .views import detect_localidades, list_spatializations, get_spatialization, delete_spatialization

urlpatterns = [
    path('', list_spatializations, name='list_spatializations'),
    path('<int:pk>/', get_spatialization, name='get_spatialization'),
    path('<int:pk>/delete/', delete_spatialization, name='delete_spatialization'),
    path('detect/', detect_localidades, name='detect_localidades'),
]
