from django.urls import path
from . import views

urlpatterns = [
    path('v1/', views.chat, name='chat-api'),
]