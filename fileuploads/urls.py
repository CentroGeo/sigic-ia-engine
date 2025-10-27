from django.urls import path
from . import views

urlpatterns = [
    # path("upload-to-geonode/", views.upload_to_geonode, name="upload_to_geonode"),
    #apis para usuarios
    path('workspaces/user', views.list_workspaces, name='workspace-list'),
    path('workspaces/user/<int:workspace_id>/contexts', views.list_workspaces_contexts, name='contexts-list'),
    
    # apis para workspaces admin
    path('workspaces/admin', views.list_admin_workspaces, name='admin-workspace-list'),
    path('workspaces/admin/create', views.create_admin_workspaces, name='admin-workspace-create'),
    path('workspaces/admin/register/<int:workspace_id>', views.register_admin_workspaces, name='admin-workspace-register'),
    path('workspaces/admin/edit/<int:workspace_id>', views.edit_admin_workspaces, name='admin-workspace-edit'),
    path('workspaces/admin/delete/<int:workspace_id>', views.delete_admin_workspaces, name='admin-workspace-delete'),
    
    path('workspaces/admin/<int:workspace_id>/contexts', views.list_admin_workspaces_contexts, name='admin-contexts-list'),   
    path('workspaces/admin/contexts/create', views.create_admin_workspaces_contexts, name='contexts-create'),
    path('workspaces/admin/contexts/register/<int:context_id>', views.register_admin_workspaces_contexts, name='admin-contexts-register'),
    path('workspaces/admin/contexts/edit/<int:context_id>', views.edit_admin_workspaces_contexts, name='admin-contexts-edit'),
    path('workspaces/admin/contexts/delete/<int:context_id>', views.delete_admin_workspaces_contexts, name='admin-contexts-delete'),
    path('workspaces/admin/<int:workspace_id>/files', views.list_admin_workspaces_files, name='admin-workspaces-files-list'),  
    
    path('workspaces/admin/<int:workspace_id>/contexts/<int:context_id>/files', views.list_admin_workspaces_contexts_files, name='admin-contexts-files-list'),   
    path('workspaces/admin/contexts/files/create', views.create_admin_workspaces_contexts_files, name='contexts-files-create'),

    path('cache/status', views.cache_status, name='cache-status'),
    path('cache/cleanup', views.force_cache_cleanup, name='cache-cleanup'),

    # Endpoint para actualizar permisos de documentos en GeoNode
    path('documents/update-permissions', views.update_document_permissions, name='update-document-permissions'),
]