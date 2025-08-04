from rest_framework.response import Response
from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from fileuploads.models import Workspace, Context, Files
from django.db import transaction
from rest_framework import status
import  json 
import os
import shutil
import requests

"""
    Secciones de apis para workspaces
"""
@api_view(["GET", "POST"])
def list_workspaces(request):
    user_id = request.GET.get("user_id")
    
    list_workspaces = list(Workspace.objects.filter(
        Q(user_id=user_id) | Q(public=True),
        active=True
    ).values(
        'id', 'title', 'description', 'user_id', 'active', 'public', 'created_date', 'image_type'
    ))
    
    
    return JsonResponse(list(list_workspaces), safe=False)


@api_view(["GET", "POST"])
def list_admin_workspaces(request):
    user_id = request.GET.get("user_id")
    
    list_workspaces = list(Workspace.objects.filter(
        user_id=user_id,
    ).values(
        'id', 'title', 'description', 'user_id', 'active', 'public', 'created_date', 'image_type'
    ))
    
    
    return JsonResponse(list(list_workspaces), safe=False)

@api_view(["GET", "POST"])
@csrf_exempt
def create_admin_workspaces(request):
    user_id = request.GET.get("user_id")
    workspace_data = request.POST.copy()
    answer = {
        "id": None,
        "saved": False
    }
    
    file = request.FILES.get('file', None)
    if file:
        filename = file.name
        file_extension = filename.split('.')[-1].lower()
        
        if file_extension not in ['jpg', 'jpeg', 'png']:
            return JsonResponse(
                {"error": "El archivo debe ser una imagen (jpg, jpeg, png)"},
                status=400
            )
    
    try:
        with transaction.atomic():
            
            new_workspace               = Workspace()
            new_workspace.user_id       = user_id
            new_workspace.title         = workspace_data.get("title")
            new_workspace.description   = workspace_data.get("description")
            new_workspace.public        = workspace_data.get("public")
            
            if file:   
                new_workspace.image_type = file_extension
                
            new_workspace.save()
            
            if file:   
                try:
                    # Crear el directorio si no existe
                    upload_dir = "uploaded_images"
                    if not os.path.exists(upload_dir):
                        os.makedirs(upload_dir)

                    workspaces_dir = os.path.join(upload_dir, "workspaces")
                    if not os.path.exists(workspaces_dir):
                        os.makedirs(workspaces_dir)
                    
                    # Guardar la imagen en el directorio
                    file_path = os.path.join(workspaces_dir, '{}.{}'.format(new_workspace.id, file_extension))
                    with open(file_path, "wb") as f:
                        shutil.copyfileobj(file.file, f)
                except:
                    l = 0
                    
            answer["id"] = new_workspace.id
            answer["saved"] = True
        return JsonResponse(answer, status=200)
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


@api_view(["GET", "POST"])
@csrf_exempt
def edit_admin_workspaces(request, workspace_id):
    user_id = request.GET.get("user_id")
    workspace_data = request.POST.copy()
    answer = {
        "id": None,
        "saved": False
    }
    
    file = request.FILES.get('file', None)
    if file:
        filename = file.name
        file_extension = filename.split('.')[-1].lower()
        
        if file_extension not in ['jpg', 'jpeg', 'png']:
            return JsonResponse(
                {"error": "El archivo debe ser una imagen (jpg, jpeg, png)"},
                status=400
            )
    
    try:
        with transaction.atomic():
            
            get_workspace               = Workspace.objects.get(id=workspace_id)
            get_workspace.user_id       = user_id
            get_workspace.title         = workspace_data.get("title")
            get_workspace.description   = workspace_data.get("description")
            get_workspace.public        = workspace_data.get("public")
            
            if file:   
                get_workspace.image_type = file_extension
                
            get_workspace.save()
            
            if file:   
                try:
                    # Crear el directorio si no existe
                    upload_dir = "uploaded_images"
                    if not os.path.exists(upload_dir):
                        os.makedirs(upload_dir)

                    workspaces_dir = os.path.join(upload_dir, "workspaces")
                    if not os.path.exists(workspaces_dir):
                        os.makedirs(workspaces_dir)
                    
                    # Guardar la imagen en el directorio
                    file_path = os.path.join(workspaces_dir, '{}.{}'.format(workspace_id, file_extension))
                    with open(file_path, "wb") as f:
                        shutil.copyfileobj(file.file, f)
                except:
                    l = 0
                    
            answer["id"] = get_workspace.id
            answer["saved"] = True
        return JsonResponse(answer, status=200)
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)



"""
    Secciones de apis para contexts
"""
@api_view(["GET", "POST"])
def list_workspaces_contexts(request, workspace_id):
    user_id = request.GET.get("user_id")
    
    list_workspaces = list(Context.objects.filter(
        Q(user_id=user_id) | Q(public=True),
        active=True,
        workspace_id=workspace_id
    ).values(
        'id', 'title', 'description', 'user_id', 'active', 'public', 'created_date', 'image_type'
    ))
    
    
    return JsonResponse(list(list_workspaces), safe=False)


@api_view(["GET", "POST"])
def list_admin_workspaces_contexts(request, workspace_id):
    user_id = request.GET.get("user_id")
    
    list_workspaces_contexts = list(Context.objects.filter(
        user_id=user_id,
        workspace_id=workspace_id
    ).values(
        'id', 'title', 'description', 'user_id', 'active', 'public', 'created_date', 'image_type'
    ))
    
    
    return JsonResponse(list(list_workspaces_contexts), safe=False)

@api_view(["GET", "POST"])
def create_admin_workspaces_contexts(request):
    user_id = request.GET.get("user_id")
    context_data = request.POST.copy()
    
    answer = {
        "id": None,
        "saved": False
    }
    
    file = request.FILES.get('file', None)
    if file:
        filename = file.name
        file_extension = filename.split('.')[-1].lower()
        
        if file_extension not in ['jpg', 'jpeg', 'png']:
            return JsonResponse(
                {"error": "El archivo debe ser una imagen (jpg, jpeg, png)"},
                status=400
            )
    
    try:
        with transaction.atomic():
            getWorkspace = Workspace.objects.get(id=context_data.get("workspace_id"))
            
            new_context               = Context()
            new_context.workspace     = getWorkspace
            new_context.user_id       = user_id
            new_context.title         = context_data.get("title")
            new_context.description   = context_data.get("description")
            new_context.public        = context_data.get("public")
            
            if file:   
                new_context.image_type = file_extension
                
            new_context.save()
            
            if file:   
                try:
                    # Crear el directorio si no existe
                    upload_dir = "uploaded_images"
                    if not os.path.exists(upload_dir):
                        os.makedirs(upload_dir)

                    contexts_dir = os.path.join(upload_dir, "contexts")
                    if not os.path.exists(contexts_dir):
                        os.makedirs(contexts_dir)
                    
                    # Guardar la imagen en el directorio
                    file_path = os.path.join(contexts_dir, '{}.{}'.format(new_context.id, file_extension))
                    with open(file_path, "wb") as f:
                        shutil.copyfileobj(file.file, f)
                except:
                    l = 0
                    
            answer["id"] = new_context.id
            answer["saved"] = True
        
        return JsonResponse(answer, status=200)    
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)

@api_view(["GET", "POST"])
def edit_admin_workspaces_contexts(request, context_id):
    user_id = request.GET.get("user_id")
    context_data = request.POST.copy()
    
    answer = {
        "id": None,
        "saved": False
    }
    
    file = request.FILES.get('file', None)
    if file:
        filename = file.name
        file_extension = filename.split('.')[-1].lower()
        
        if file_extension not in ['jpg', 'jpeg', 'png']:
            return JsonResponse(
                {"error": "El archivo debe ser una imagen (jpg, jpeg, png)"},
                status=400
            )
    
    try:
        with transaction.atomic():
            get_context               = Context.objects.get(id=context_id)
            get_context.user_id       = user_id
            get_context.title         = context_data.get("title")
            get_context.description   = context_data.get("description")
            get_context.public        = context_data.get("public")
            
            if file:   
                get_context.image_type = file_extension
                
            get_context.save()
            
            if file:   
                try:
                    # Crear el directorio si no existe
                    upload_dir = "uploaded_images"
                    if not os.path.exists(upload_dir):
                        os.makedirs(upload_dir)

                    contexts_dir = os.path.join(upload_dir, "contexts")
                    if not os.path.exists(contexts_dir):
                        os.makedirs(contexts_dir)
                    
                    # Guardar la imagen en el directorio
                    file_path = os.path.join(contexts_dir, '{}.{}'.format(context_id, file_extension))
                    with open(file_path, "wb") as f:
                        shutil.copyfileobj(file.file, f)
                except:
                    l = 0
                    
            answer["id"] = get_context.id
            answer["saved"] = True
        
        return JsonResponse(answer, status=200)    
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)

"""
    Secciones de apis para files
"""
@api_view(["GET", "POST"])
def list_admin_workspaces_contexts_files(request, workspace_id, context_id):
    user_id = request.GET.get("user_id")
    
    list_files = list(Files.objects.filter(
        context=context_id
    ).values(
        'id', 'document_id', 'document_type', 'user_id'
    ))
    
    return JsonResponse(list(list_files), safe=False)

@api_view(["GET", "POST"])
def create_admin_workspaces_contexts_files(request):
    user_id = request.GET.get("user_id")

    file = request.FILES.get('file', None)
    if file:
        filename = file.name
        file_extension = filename.split('.')[-1].lower()
        
        if file_extension not in ['']:
            return JsonResponse(
                {"error": "El archivo debe ser "},
                status=400
            )
    
    """
        Guarado a geonode
    """

    try:
        from .utils import extract_text_from_file, vectorize_and_store_text

        # 1. Extraer texto
        extracted_text = extract_text_from_file(file)

        # 2. Guardar archivo en Files model (si no se hace antes)
        saved_file = Files.objects.create(
            context_id=request.POST.get('context_id'),
            user_id=user_id,
            document_id=file.name,
            document_type=file.content_type
        )

        # 3. Vectorizar y guardar en pgvector
        vectorize_and_store_text(extracted_text, saved_file.id)

    except Exception as e:
        return JsonResponse({"error": f"Vectorización fallida: {str(e)}"}, status=500)


            
    return JsonResponse( {"status": "ok"}, safe=False)

@api_view(["POST"])
@csrf_exempt
def upload_to_geonode(request):
    token = request.headers.get("Authorization")
    cookie = request.headers.get("Cookie")
    if not token:
        return JsonResponse({"error": "Authorization header missing"}, status=400)
    file = request.FILES.get("file")
    title = request.POST.get("title", "Sin título")
    if not file:
        return JsonResponse({"error": "File not received"}, status=400)
    try:
        files = {
            "doc_file": (file.name, file, file.content_type)
        }
        data = {
            "title": title
        }
        headers = {
            "Authorization": token
        }
        if cookie:
            headers["Cookie"] = cookie
        response = requests.post(
            # "https://geonode.dev.geoint.mx/documents/upload?no__redirect=true",
            "http://10.2.102.99/documents/upload?no__redirect=true",
            files=files,
            data=data,
            headers=headers,
            timeout=30
        )
        return JsonResponse(response.json(), status=response.status_code)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)