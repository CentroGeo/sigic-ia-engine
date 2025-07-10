from django.urls import path
from . import views

urlpatterns = [
    path('home/', views.homeUpload, name='home-api-upload'),
]