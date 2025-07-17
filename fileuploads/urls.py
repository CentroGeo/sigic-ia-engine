from django.urls import path
from . import views

urlpatterns = [
    path('workspaces/user', views.list_workspaces, name='workspace-list'),
    
    
    path('workspaces/admin', views.list_admin_workspaces, name='admin-workspace-list'),
    path('workspaces/admin/create', views.create_admin_workspaces, name='admin-workspace-create'),
    
    
    path('workspaces/user/<int:workspace_id>/contexts', views.list_workspaces_contexts, name='contexts-list'),
    path('workspaces/admin/<int:workspace_id>/contexts', views.list_admin_workspaces_contexts, name='admin-workspace-list'),
    
    path('workspaces/admin/contexts/create', views.create_admin_workspaces_contexts, name='contexts-create'),
]