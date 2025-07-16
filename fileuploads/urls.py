from django.urls import path
from . import views

urlpatterns = [
    path('workspaces/user', views.list_workspaces, name='workspace-list'),
    
    
    path('workspaces/admin', views.list_admin_workspaces, name='admin-workspace-list'),
    path('workspaces/admin/create', views.create_admin_workspaces, name='admin-workspace-create'),
]