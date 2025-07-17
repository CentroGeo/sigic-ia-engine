from django.urls import path
from . import views

urlpatterns = [
    #apis para usuarios
    path('workspaces/user', views.list_workspaces, name='workspace-list'),
    path('workspaces/user/<int:workspace_id>/contexts', views.list_workspaces_contexts, name='contexts-list'),
    
    # apis para workspaces admin
    path('workspaces/admin', views.list_admin_workspaces, name='admin-workspace-list'),
    path('workspaces/admin/create', views.create_admin_workspaces, name='admin-workspace-create'),
    
    path('workspaces/admin/<int:workspace_id>/contexts', views.list_admin_workspaces_contexts, name='admin-contexts-list'),   
    path('workspaces/admin/contexts/create', views.create_admin_workspaces_contexts, name='contexts-create'),
    
    path('workspaces/admin/<int:workspace_id>/contexts/<int:context_id>/files', views.list_admin_workspaces_contexts_files, name='admin-contexts-files-list'),   
    path('workspaces/admin/contexts/files/create', views.create_admin_workspaces_contexts_files, name='contexts-files-create'),   
]