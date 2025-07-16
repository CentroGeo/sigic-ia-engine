from rest_framework.response import Response
from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from fileuploads.models import Workspace
from django.db import transaction
from rest_framework import status
import  json 


@api_view(["GET", "POST"])
def list_workspaces(request):
    user_id = request.GET.get("user_id")
    
    list_workspaces = list(Workspace.objects.filter(
        Q(user_id=user_id) | Q(public=True),
        active=True
    ).values(
        'id', 'title', 'description', 'user_id', 'active', 'public', 'created_date', 'type_image'
    ))
    
    
    return JsonResponse(list(list_workspaces), safe=False)


@api_view(["GET", "POST"])
def list_admin_workspaces(request):
    user_id = request.GET.get("user_id")
    
    list_workspaces = list(Workspace.objects.filter(
        user_id=user_id,
    ).values(
        'id', 'title', 'description', 'user_id', 'active', 'public', 'created_date', 'type_image'
    ))
    
    
    return JsonResponse(list(list_workspaces), safe=False)

@api_view(["GET", "POST"])
def create_admin_workspaces(request):
    user_id = request.GET.get("user_id")
    workspace_data = request.POST.copy()
    
    file = request.FILES.get('file', None)
    if file:
        filename = file.name
        file_extension = filename.split('.')[-1].lower()
        
        if file_extension not in ['jpg', 'jpeg', 'png']:
            return Response({"error": "El archivo debe ser una imagen (jpg, jpeg, png)"}, status=status.HTTP_400_BAD_REQUEST)
        
    with transaction.atomic():
        new_workspace = Workspace()
        new_workspace.user_id = user_id
        new_workspace.title = workspace_data.get("title")
        new_workspace.description = workspace_data.get("description")
        new_workspace.type_image = file_extension
        
        if(file is not None):   
            new_workspace.type_image = file_extension
            
        new_workspace.save()
        
        
        """
            section para subir archivo en geonode si se
        """

        """
            section para indexar en postgresql
        """
        
        """
            section para categorizar el archivo tesis, noticias, etc...
        """
        
        
    return JsonResponse(list(list_workspaces), safe=False)
