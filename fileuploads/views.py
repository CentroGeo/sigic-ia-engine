from rest_framework.response import Response
from django.http import JsonResponse
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from .models import Workspace, Context, Files, DocumentEmbedding
from django.db import transaction
from django.core.files.storage import FileSystemStorage
from rest_framework import status
from django.conf import settings
from django.db.models import Count, Q
from .utils import upload_file_to_geonode, extract_text_from_file, vectorize_and_store_text, get_geonode_document_uuid, process_files
import  json 
import os
import shutil
import requests
from langchain.text_splitter import RecursiveCharacterTextSplitter
from .embeddings_service import embedder
import uuid
from typing import List
import time
#import textract
#import magic

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


# En fileuploads/views.py - Actualizar la función create_admin_workspaces

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

            new_workspace = Workspace()
            new_workspace.user_id = user_id
            new_workspace.title = workspace_data.get("title")
            new_workspace.description = workspace_data.get("description")
            new_workspace.public = workspace_data.get("public", "False").lower() == "true"

            new_workspace.save()

            # Procesar archivos si existen
            answer["uploaded_files"] = process_files(request, new_workspace, user_id)
            answer["files_uploaded"] = True
            answer["id"] = new_workspace.id
            answer["saved"] = True

        return JsonResponse(answer, status=200)

    except Exception as e:
        print("Error al guardar: ", str(e))
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


# También agregar endpoint para monitorear el cache
@api_view(['GET'])
def cache_status(request):
    """Endpoint para verificar estado del cache de embeddings"""
    try:
        cache_stats = embedder.get_cache_stats()

        status = {
            'healthy': True,
            'memory_usage_mb': cache_stats['memory_usage_mb'],
            'cached_embeddings': cache_stats['cached_embeddings'],
            'warnings': [],
            'should_cleanup': embedder.should_cleanup_cache()
        }

        if cache_stats['memory_usage_mb'] > 100:
            status['healthy'] = False
            status['warnings'].append('Cache usando mucha memoria')

        if cache_stats['cached_embeddings'] > 1000:
            status['warnings'].append('Muchos embeddings en cache')

        return JsonResponse(status)

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@api_view(['POST'])
def force_cache_cleanup(request):
    """Endpoint para forzar limpieza del cache"""
    try:
        cache_stats_before = embedder.get_cache_stats()
        embedder.clear_cache()

        return JsonResponse({
            "success": True,
            "message": "Cache limpiado exitosamente",
            "stats_before": cache_stats_before,
            "stats_after": embedder.get_cache_stats()
        })

    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@api_view(["GET", "POST"])
@csrf_exempt
def edit_admin_workspaces(request, workspace_id):
    user_id = request.GET.get("user_id")
    workspace_data = request.POST.copy()
    
    answer = {
        "id": None,
        "saved": False,
        "files_uploaded": False,
        "uploaded_files": []
    }
    
    try:
        with transaction.atomic():
            workspace_data = request.POST
            
            get_workspace               = Workspace.objects.get(id=workspace_id)
            get_workspace.user_id       = user_id
            get_workspace.title         = workspace_data.get("title")
            get_workspace.description   = workspace_data.get("description")
            get_workspace.public        = workspace_data.get("public", "False").lower() == "true"
            get_workspace.save()
            
            Files.objects.filter(id__in=workspace_data.getlist('delete_files[]')).delete()
            answer["uploaded_files"] = process_files(request, get_workspace, user_id)
            answer["files_uploaded"] = True
            
            answer["id"] = workspace_id
            answer["saved"] = True

        return JsonResponse(answer, status=200)
    
    except Exception as e:
        print("Error al guardar: ",str(e))
        return JsonResponse({"status": "error", "message": str(e)}, status=400)



@api_view(["GET", "POST"])
@csrf_exempt
def register_admin_workspaces(request, workspace_id):
    user_id = request.GET.get("user_id")
    
    answer = {
        "success": False,
        "workspace": None,
        "files": []
    }
    
    try:
        with transaction.atomic():
            get_workspace            = Workspace.objects.get(id=workspace_id)            
            files                    = list(Files.objects.filter(workspace__id=workspace_id).values('id', 'document_id', 'document_type', 'user_id', 'filename','path'))
            
            answer["workspace"] = {
                "title": get_workspace.title,
                "description": get_workspace.description,
                "public": get_workspace.public
            }
            answer["files"] = files
            answer["success"] = True
        return JsonResponse(answer, status=200)
    
    except Exception as e:
        print("Error al guardar: ",str(e))
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


@api_view(["DELETE"])
@csrf_exempt
def delete_admin_workspaces(request, workspace_id):
    user_id = request.GET.get("user_id")
    answer = {
        "saved": False,
    }
    
    try:
        with transaction.atomic():
            Workspace.objects.filter(id=workspace_id).delete()
            answer["saved"] = True

        return JsonResponse(answer, status=200)
    
    except Exception as e:
        print("Error al guardar: ",str(e))
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
    
    list_workspaces_contexts = list(
        Context.objects.filter(
            user_id=user_id,
            workspace_id=workspace_id
        )
        .annotate(num_files=Count('files'))
        .values(
            'id', 'title', 'description', 'user_id', 'active',
            'public', 'created_date', 'image_type',
            'num_files'  # número de archivos asociados al context
        )
    )
    
    
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

    fuentes_raw = context_data.get("fuentes")  # ← es un string tipo "[3,4,5]"
    
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

            if fuentes_raw: #fuentes seleccionadas
                try:
                    fuentes_ids = json.loads(fuentes_raw)  # ← ahora es una lista [3, 4, 5]

                    if isinstance(fuentes_ids, list):
                        existing_files = Files.objects.filter(id__in=fuentes_ids)

                        for file_instance in existing_files:
                            new_context.files.add(file_instance)

                except json.JSONDecodeError as e:
                    print("Error al parsear fuentes:", e)            
            
            

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
    
    fuentes_raw = context_data.get("fuentes")  # ← es un string tipo "[3,4,5]"
    fuentes_delete = context_data.get("fuentes_elimnadas")
    
    try:
        with transaction.atomic():
            get_context               = Context.objects.get(id=context_id)
            get_context.title         = context_data.get("nombre")
            get_context.description   = context_data.get("descripcion")
            
            if fuentes_raw: #fuentes seleccionadas
                try:
                    fuentes_ids = json.loads(fuentes_raw)  # ← ahora es una lista [3, 4, 5]

                    if isinstance(fuentes_ids, list):
                        existing_files = Files.objects.filter(id__in=fuentes_ids)

                        for file_instance in existing_files:
                            get_context.files.add(file_instance)

                except json.JSONDecodeError as e:
                    print("Error al parsear fuentes:", e)            
            
            if fuentes_delete: #fuentes seleccionadas
                try:
                    fuentes_ids = json.loads(fuentes_delete)  # ← ahora es una lista [3, 4, 5]

                    if isinstance(fuentes_ids, list):
                        existing_files = Files.objects.filter(id__in=fuentes_ids)

                        for file_instance in existing_files:
                            get_context.files.remove(file_instance)
            

                except json.JSONDecodeError as e:
                    print("Error al parsear fuentes:", e)            
            
            
            if 'file' in request.FILES:  #archivo de portada (imagen)
                print("File!!!")
                uploaded_file = request.FILES['file']
                
                fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'uploads/contextos', str(get_context.id)))
                os.makedirs(fs.location, exist_ok=True)

                # Guardar el archivo físicamente
                filename = fs.save(uploaded_file.name, uploaded_file)
                file_path = os.path.join('uploads/contextos', str(context_id), filename)

                # Guardar info en la base de datos
                get_context.image_type  = file_path

                # Agregar detalles del archivo subido a la respuesta
                answer["uploaded_file"] = {
                    "name": uploaded_file.name,
                    "type": uploaded_file.content_type,
                    "size": uploaded_file.size,
                    "path": filename
                }
                   
            
            get_context.save()
            
            answer["id"] = context_id
            answer["saved"] = True
        
        return JsonResponse(answer, status=200)    
    except Exception as e:
        return JsonResponse({"status": "error", "message": str(e)}, status=400)

@api_view(["GET", "POST"])
@csrf_exempt
def register_admin_workspaces_contexts(request, context_id):
    user_id = request.GET.get("user_id")
    
    answer = {
        "success": False,
        "context": None,
        "files": []
    }
    
    try:
        with transaction.atomic():
            get_context            = Context.objects.get(id=context_id)            
            
            answer["context"] = {
                "title": get_context.title,
                "description": get_context.description,
                "public": get_context.public
            }
            
            answer["files"] = list(get_context.files.values('id', 'document_id', 'document_type', 'user_id', 'filename','path'))
        return JsonResponse(answer, status=200)
    
    except Exception as e:
        print("Error al guardar: ",str(e))
        return JsonResponse({"status": "error", "message": str(e)}, status=400)


@api_view(["DELETE"])
@csrf_exempt
def delete_admin_workspaces_contexts(request, context_id):
    user_id = request.GET.get("user_id")
    answer = {
        "saved": False,
    }
    
    try:
        with transaction.atomic():
            Context.objects.filter(id=context_id).delete()
            answer["saved"] = True

        return JsonResponse(answer, status=200)
    
    except Exception as e:
        print("Error al guardar: ",str(e))
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
        'id', 'document_id', 'document_type', 'user_id', 'filename','path'
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


def embeddingFile(archivo_id,file,context_id,user_id,document_type):
    print("######## embeddingFile #####")
    try:
        # 1. Extraer texto
        extracted_text = extract_text_from_file(file)
        print(extracted_text)
        # 2. Guardar archivo en Files model (si no se hace antes)
        # saved_file = Files.objects.create(
        #     context_id=request.POST.get('context_id'),
        #     user_id=user_id,
        #     # document_id=file.name, ######################################## acá iria el uuid
        #     #document_id=document_uuid,
        #     document_type=file.content_type
        # )

        # 3. Vectorizar y guardar en pgvector
        vectorize_and_store_text(extracted_text, archivo_id)

    except Exception as e:
        return JsonResponse({"error": f"Vectorización fallida: {str(e)}"}, status=500)


def optimized_rag_search(context_id: int, query: str, top_k: int = 50) -> List[DocumentEmbedding]:
    """
    Búsqueda RAG optimizada con mejor ranking y filtrado
    """
    # Generar embedding de la consulta
    query_embedding = embedder.embed_query(query)

    if query_embedding is None:
        return []

    # Detectar idioma de la consulta
    query_language = embedder.detect_language(query)

    # Buscar chunks relevantes con filtros mejorados
    relevant_chunks = DocumentEmbedding.objects.filter(
        file__contexts__id=context_id
    ).annotate(
        similarity=1 - L2Distance('embedding', query_embedding)
    )

    # Filtrar por idioma si coincide
    if query_language in ['es', 'en', 'fr']:
        language_chunks = relevant_chunks.filter(language=query_language)
        if language_chunks.exists():
            relevant_chunks = language_chunks

    # Obtener top chunks ordenados por similitud
    top_chunks = relevant_chunks.order_by('-similarity')[:top_k]

    # Filtrar chunks con similitud muy baja (umbral mínimo)
    filtered_chunks = [chunk for chunk in top_chunks if chunk.similarity > 0.3]

    print(f"RAG search: {len(filtered_chunks)} chunks encontrados para query en {query_language}")

    return filtered_chunks[:min(20, len(filtered_chunks))]  # Limitar a 20 mejores resultados


# Función auxiliar para limpiar cache periódicamente
def cleanup_embedding_cache():
    """Limpia el cache de embeddings si está muy grande"""
    cache_stats = embedder.get_cache_stats()
    if cache_stats['memory_usage_mb'] > 100:  # Si usa más de 100MB
        embedder.clear_cache()
        print("Cache de embeddings limpiado por uso excesivo de memoria")
