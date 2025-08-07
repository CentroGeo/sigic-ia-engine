from rest_framework.response import Response
from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from .models import Workspace, Context, Files
from django.db import transaction
from django.core.files.storage import FileSystemStorage
from rest_framework import status
from django.conf import settings
from django.db.models import Count, Q
from .utils import upload_file_to_geonode, extract_text_from_file, vectorize_and_store_text, get_geonode_document_uuid
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

    list_workspaces = list(
        Workspace.objects.filter(
            Q(user_id=user_id) | Q(public=True),
            active=True
        ).annotate(
            numero_fuentes=Count('files', distinct=True),  # 'files' es el nombre del related_name o relación inversa
            numero_contextos=Count('contextos',filter=Q(contextos__active=True), distinct=True)  
        ).values(
            'id', 'title', 'description', 'user_id', 'active', 'public', 'created_date', 'image_type', 'numero_fuentes', 'numero_contextos'
        )
    )    
    

    
    return JsonResponse(list(list_workspaces), safe=False)


@api_view(["GET", "POST"])
def list_admin_workspaces(request):
    user_id = request.GET.get("user_id")
    
    list_workspaces = list(
        Workspace.objects.filter(
            Q(user_id=user_id)
        ).annotate(
            numero_fuentes=Count('files', distinct=True), # 'files' es el nombre del related_name o relación inversa
            numero_contextos=Count('contextos',filter=Q(contextos__active=True), distinct=True)  
        ).values(
            'id', 'title', 'description', 'user_id', 'active', 'public',
            'created_date', 'image_type', 'numero_fuentes', 'numero_contextos'
        )
    )
    
    
    return JsonResponse(list(list_workspaces), safe=False)

@api_view(["GET", "POST"])
@csrf_exempt
def create_admin_workspaces(request):
    print("create_admin_workspaces")
    user_id = request.GET.get("user_id")
    workspace_data = request.POST.copy()
    answer = {
        "id": None,
        "saved": False,
        "files_uploaded": False,
        "uploaded_files": []
    }

    print(workspace_data)
    
   
    try:
        with transaction.atomic():
            # Obtener datos del formulario
            workspace_data = request.POST
            #user_id = workspace_data.get("user_id")  # Asegúrate de enviar esto desde el front
            
            new_workspace               = Workspace()
            new_workspace.user_id       = user_id
            new_workspace.title         = workspace_data.get("title")
            new_workspace.description   = workspace_data.get("description")
            #new_workspace.public        = workspace_data.get("public")
            new_workspace.public        = workspace_data.get("public", "False").lower() == "true"
            
                
            new_workspace.save()


            # 2. Procesar archivos si existen
            # Procesar archivos si existen
            if 'archivos' in request.FILES:
                fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'uploads/proyectos', str(new_workspace.id)))
                os.makedirs(fs.location, exist_ok=True)
                
                for uploaded_file in request.FILES.getlist('archivos'):
                    # Guardar el archivo físicamente
                    filename = fs.save(uploaded_file.name, uploaded_file)


                    
                    #Guardar info en la base de datos
                    upload_file= Files()
                    upload_file.document_type = uploaded_file.content_type
                    upload_file.user_id = user_id
                    upload_file.filename = filename
                    upload_file.path = os.path.join('uploads/proyectos', str(new_workspace.id), filename)
                    upload_file.workspace =new_workspace
                    upload_file.save()

                    #workspace_file = WorkspaceFile(
                    #    workspace=new_workspace,
                    #    file_name=uploaded_file.name,
                    #    file_type=uploaded_file.content_type,
                    #    file_size=uploaded_file.size,
                    #    file_path=os.path.join('workspaces', str(new_workspace.id), filename),
                    #    category="Archivo",
                    #    origin="Propio"
                    #)
                    #workspace_file.save()
                    
                    answer["uploaded_files"].append({
                        "name": uploaded_file.name,
                        "type": uploaded_file.content_type,
                        "size": uploaded_file.size,
                        "path": filename
                    })
                
                answer["files_uploaded"] = True
            
            answer["id"] = new_workspace.id
            answer["saved"] = True

        return JsonResponse(answer, status=200)
    
    except Exception as e:
        print("Error al guardar: ",str(e))
        return JsonResponse({"status": "error", "message": str(e)}, status=400)

        # Limpieza en caso de error
"""         if 'new_workspace' in locals() and new_workspace.id:
            try:
                import shutil
                workspace_dir = os.path.join(settings.MEDIA_ROOT, 'workspaces', str(new_workspace.id))
                if os.path.exists(workspace_dir):
                    shutil.rmtree(workspace_dir)
            except Exception as cleanup_error:
                print(f"Error durante limpieza: {cleanup_error}")
        
        return JsonResponse({
            "status": "error",
            "message": str(e),
            "trace": traceback.format_exc() if settings.DEBUG else None
        }, status=400) """

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
    print("crear contexto!!!!")
    user_id = request.GET.get("user_id")
    context_data = request.POST.copy()
    
    answer = {
        "id": None,
        "saved": False
    }
    
    print(context_data)
    
    try:
        with transaction.atomic():
            getWorkspace = Workspace.objects.get(id=context_data.get("proyecto_id"))
            
            new_context               = Context()
            new_context.workspace     = getWorkspace
            #new_context.workspace     = context_data.get("proyecto_id")
            new_context.user_id       = user_id
            new_context.title         = context_data.get("nombre")
            new_context.description   = context_data.get("descripcion")
            #new_context.public        = context_data.get("public")
            new_context.save()
            
            
            if 'file' in request.FILES:  #archivo de portada (imagen)
                print("File!!!")
                uploaded_file = request.FILES['file']
                
                fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'uploads/contextos', str(new_context.id)))
                os.makedirs(fs.location, exist_ok=True)

                # Guardar el archivo físicamente
                filename = fs.save(uploaded_file.name, uploaded_file)
                file_path = os.path.join('uploads/contextos', str(new_context.id), filename)

                # Guardar info en la base de datos
                new_context.image_type  = file_path
                new_context.save()
                #upload_file = Files()
                #upload_file.document_type = uploaded_file.content_type
                #upload_file.user_id = user_id
                #upload_file.filename = filename
                #upload_file.path = os.path.join('uploads/contextos', str(new_context.id), filename)
                #upload_file.context = new_context
                #upload_file.save()

                # Agregar detalles del archivo subido a la respuesta
                answer["uploaded_file"] = {
                    "name": uploaded_file.name,
                    "type": uploaded_file.content_type,
                    "size": uploaded_file.size,
                    "path": filename
                }
                   
            
            answer["id"] = new_context.id
            answer["saved"] = True
        
        return JsonResponse(answer, status=200)    
    except Exception as e:
        print("Error al guardar contexto: ",e)
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
def list_admin_workspaces_files(request, workspace_id):
    user_id = request.GET.get("user_id")
    
    list_files = list(Files.objects.filter(
        workspace=workspace_id
    ).values(
        'id', 'document_id', 'document_type', 'user_id', 'context_id','filename','path'
    ))
    
    return JsonResponse(list(list_files), safe=False)

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
    allowed_extensions = ['pdf', 'txt', 'xls', 'xlsx']
    user_id = request.GET.get("user_id")
    file = request.FILES.get('file', None)
    if not file:
        return JsonResponse({"error": "No se proporcionó ningún archivo."}, status=400)
    
    filename = file.name
    file_extension = filename.split('.')[-1].lower()
    if file_extension not in allowed_extensions:
        return JsonResponse(
        {"error": f"Tipo de archivo no permitido. Se permiten: {', '.join(allowed_extensions)}"},
        status=400
    )

    token = request.headers.get("Authorization")
    cookie = request.headers.get("Cookie")
    title = request.POST.get("title", "Sin título")

    if not token:
        return JsonResponse({"error": "Authorization header missing"}, status=400)

    try:
        geo_response = upload_file_to_geonode(file, token, cookie, title)
        geo_response.raise_for_status()
        geo_data = geo_response.json()
        # print("Respuesta de GeoNode:", geo_data)
        document_uuid = get_geonode_document_uuid(geo_data.get("url", ""))
        # print("Valor de UUID que se va a guardar:", document_uuid)
        # return JsonResponse(geo_response.json(), status=geo_response.status_code)
    except Exception as e:
        return JsonResponse({"error": f"Upload failed: {str(e)}"}, status=500)

    try:
        # 1. Extraer texto
        extracted_text = extract_text_from_file(file)
        # 2. Guardar archivo en Files model (si no se hace antes)
        saved_file = Files.objects.create(
            context_id=request.POST.get('context_id'),
            user_id=user_id,
            # document_id=file.name, ######################################## acá iria el uuid
            #document_id=document_uuid,
            document_type=file.content_type
        )

        # 3. Vectorizar y guardar en pgvector
        vectorize_and_store_text(extracted_text, saved_file.id)

    except Exception as e:
        return JsonResponse({"error": f"Vectorización fallida: {str(e)}"}, status=500)

    # return JsonResponse( {"status": "ok"}, safe=False)
    return JsonResponse({
        "status": "ok",
        "geonode_response": geo_data
    })