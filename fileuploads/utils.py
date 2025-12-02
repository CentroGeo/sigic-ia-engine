# import textract
import pandas as pd
import docx
from PyPDF2 import PdfReader
from sentence_transformers import SentenceTransformer
from .models import DocumentEmbedding, Files
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from langchain.text_splitter import RecursiveCharacterTextSplitter
from .embeddings_service import embedder
from requests.auth import HTTPBasicAuth
from django.core.files.uploadedfile import SimpleUploadedFile
import mimetypes
import io
import requests
import os
import uuid
import time
import base64
import tempfile
import zipfile
import json

model = SentenceTransformer("all-MiniLM-L6-v2")

def convert_to_uploadedfile(filepath):
    filename = os.path.basename(filepath)
    
    # Detectar content_type
    content_type, _ = mimetypes.guess_type(filepath)
    if content_type is None:
        content_type = "application/octet-stream"

    with open(filepath, "rb") as f:
        return SimpleUploadedFile(
            name=filename,
            content=f.read(),
            content_type=content_type
        )
        
def extract_text_from_file(file):
    ext = file.name.lower().split('.')[-1]

    if ext == 'pdf':
        reader = PdfReader(file)
        return '\n'.join([page.extract_text() for page in reader.pages if page.extract_text()])

    elif ext == 'txt':
        return file.read().decode('utf-8')

    elif ext in ['csv']:
        return io.StringIO(file.read().decode('utf-8')).read()

    elif ext in ['xlsx', 'xls']:
        df = pd.read_excel(file)
        return df.to_string(index=False)

    elif ext == 'docx':
        doc = docx.Document(file)
        return '\n'.join([p.text for p in doc.paragraphs])

    else:
        raise ValueError("Unsupported file type")
    
def vectorize_and_store_text(text, file_id):
    vector = model.encode(text).tolist()

    DocumentEmbedding.objects.create(
        file_id=file_id,
        text=text,
        embedding=vector
    )

def upload_file_to_geonode(file, authorization, cookie=None, title="Sin título"):
        print(f"[DEBUG] upload_file_to_geonode - Authorization: {authorization[:50] if authorization else 'None'}...")
        print(f"[DEBUG] upload_file_to_geonode - File: {file.name}")

        if not authorization:
            raise ValueError("Authorization token is required but not provided")

        files = {
            "doc_file": (
                file.name,
                file,
                getattr(file, "content_type", "application/octet-stream"),
            ),
        }
        data = {
            "title": title,
            "metadata_only": "false"
        }
        headers = {
            "Authorization": authorization,
            "Accept": "application/json"
        }

        geonode_base_url = os.getenv("GEONODE_SERVER", "https://geonode.dev.geoint.mx").rstrip('/')
        upload_url = f"{geonode_base_url}/documents/upload?no__redirect=true"

        print(f"[DEBUG] Uploading to: {upload_url}")

        # Upload the file first
        response = requests.post(
            upload_url,
            data=data,
            files=files,
            headers=headers
        )
        print(f"[DEBUG] GeoNode upload response: {response.status_code}")
        if response.status_code >= 400:
            print(f"[ERROR] GeoNode upload failed: {response.text}")
            return response

        # If upload successful, set permissions to make document public
        if response.status_code in [200, 201]:
            try:
                response_data = response.json()
                doc_url = response_data.get("url", "")
                doc_id = doc_url.strip("/").split("/")[-1]
                print(f"[DEBUG] Document uploaded with ID: {doc_id}, now setting public permissions...")

                # Set permissions using GeoNode 4.x API
                # Use the simpler format with groups for anonymous access
                permissions_url = f"{geonode_base_url}/api/v2/resources/{doc_id}/permissions"

                # Try to set permissions using groups (anonymous group typically has ID 1 or 2)
                # This makes the document publicly viewable
                permissions_payload = {
                    "groups": [
                        {
                            "id": 1,  # anonymous group ID (typically 1 or 2)
                            "permissions": "view"
                        }
                    ]
                }

                perm_headers = {
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }

                print(f"[DEBUG] Permissions payload: {permissions_payload}")
                perm_response = requests.put(
                    permissions_url,
                    json=permissions_payload,
                    headers=perm_headers,
                    timeout=10
                )

                print(f"[DEBUG] Permissions update response: {perm_response.status_code}")
                if perm_response.status_code not in [200, 201, 204]:
                    print(f"[WARNING] Failed to set public permissions: {perm_response.text[:500]}")
                else:
                    print(f"[SUCCESS] Document {doc_id} is now publicly accessible")

            except Exception as e:
                print(f"[ERROR] Exception while setting permissions: {str(e)}")
                # Don't fail the upload if permission setting fails
                pass

        return response

def upload_image_to_geonode(file, filename, token=''):
    file_bytes = file.read() 
    files = {"file": (filename, file_bytes, file.content_type)}
    data = {"category": "contextos"}
    headers = {
        "Authorization": token
    }
    
    geonode_base_url = os.environ.get("GEONODE_SERVER")
    upload_url = f"{geonode_base_url}/sigic/ia/mediauploads/upload"
    
    response = requests.post(
        upload_url,
        files=files,
        data=data,
        headers=headers,
        timeout=30
    )

    return response

def get_geonode_document_uuid(doc_url, authorization=None):
    """
    Se extrae el ID numérico de la URL devuelta por GeoNode al acargar el archivo y se 
    obtiene el UUID completo del documento.
    """
    headers = {
        "Accept": "application/json"
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    try:
        doc_id = doc_url.strip("/").split("/")[-1]
        geonode_base_url = os.environ.get("GEONODE_SERVER")
        response = requests.get(
            f"{geonode_base_url}/api/v2/documents/{doc_id}",
            headers=headers,
        )
        response.raise_for_status()
        resp = response.json()
        
        return {
         "uuid": resp["document"]["uuid"],
         'id': doc_id
        }
        
    except Exception as e:
        raise ValueError(f"No se pudo obtener el UUID del documento: {str(e)}")

def get_geonode_document_uuid_by_id(doc_id, authorization=None):
    """
    Se extrae el ID numérico de la URL devuelta por GeoNode al acargar el archivo y se 
    obtiene el UUID completo del documento.
    """
    headers = {
        "Accept": "application/json"
    }
    if authorization is not None:
        headers["Authorization"] = authorization
    try:
        geonode_base_url = os.environ.get("GEONODE_SERVER")
        response = requests.get(
            f"{geonode_base_url}/api/v2/documents/{doc_id}",
            headers=headers,
        )
        response.raise_for_status()
        resp = response.json()
        
        return {
         "uuid": resp["document"]["uuid"],
         'id': doc_id
        }
        
    except Exception as e:
        raise ValueError(f"No se pudo obtener el UUID del documento: {str(e)}")


def process_files(request, workspace, user_id):
    uploaded_files = []
    if 'archivos' in request.FILES:
        # fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'uploads/proyectos', str(workspace.id)))
        # os.makedirs(fs.location, exist_ok=True)
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len
        )

        token = request.headers.get("Authorization")
        cookie = request.headers.get("Cookie")
        type = request.POST.get("type", "archivos cargados") # checalo

        print(f"[DEBUG] process_files - Token present: {bool(token)}")
        print(f"[DEBUG] process_files - Cookie present: {bool(cookie)}")
        print(f"[DEBUG] process_files - Files count: {len(request.FILES.getlist('archivos'))}")

        for uploaded_file in request.FILES.getlist('archivos'):            
            # Guardar el archivo físicamente delete
            #filename = fs.save(uploaded_file.name, uploaded_file)
            filename = uploaded_file.name
            # Guardar el archivo geonode
            #try:
            geo_response = upload_file_to_geonode(uploaded_file, token, cookie, filename)
            geo_response.raise_for_status()
            geo_data = geo_response.json()
            geonode_info = get_geonode_document_uuid(geo_data.get("url", ""), token)
            #except Exception as e:
            #    print(f"Uploaduploaded_file failed: {str(e)}")

            
            #Guardar info en la base de datos
            upload_file= Files()
            upload_file.geonode_uuid = geonode_info["uuid"]
            upload_file.geonode_id = geonode_info["id"]
            upload_file.geonode_type = "Propio"
            upload_file.geonode_category = "Documento"
            upload_file.user_id = user_id
            upload_file.filename = filename
            upload_file.document_type = uploaded_file.content_type
            upload_file.path = os.path.join('uploads/proyectos', str(workspace.id), filename) # delete
            upload_file.workspace = workspace
            upload_file.save()
            archivo_id = upload_file.id
            print(archivo_id)

            #extraer texto
            uploaded_file.seek(0)
            extracted_text = extract_text_from_file(uploaded_file)

            # Detectar idioma
            language = embedder.detect_language(extracted_text)

            # Dividir texto en chunks
            chunks = text_splitter.split_text(extracted_text)

            # Generar embeddings por lotes (batch) para mejor rendimiento
            embeddings = embedder.embed_texts(chunks)

            # Guardar chunks con embeddings ANTES de actualizar metadatos
            # Esto es necesario porque la extracción de metadatos RAG necesita los embeddings
            print(f"[DEBUG] Guardando {len(chunks)} chunks con embeddings...")
            DocumentEmbedding.objects.bulk_create([
                DocumentEmbedding(
                    file=upload_file,
                    chunk_index=idx,
                    text=chunk,
                    embedding=embedding,
                    language=language,
                    metadata={
                        "source": uploaded_file.name,
                        "content_type": uploaded_file.content_type,
                        "chunk_size": len(chunk)
                    }
                )
                for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings))
            ])
            print(f"[DEBUG] Chunks guardados exitosamente")

            upload_file.processed = True
            #upload_file.size = os.path.getsize(filename)
            upload_file.language = language
            upload_file.save()

            # Actualizar metadatos en GeoNode usando tarea asíncrona de Celery
            # NOTA: La tarea se ejecuta en segundo plano de forma NO BLOQUEANTE
            # El token se pasa pero puede expirar - esto es aceptable para metadatos opcionales
            print(f"[DEBUG] ===== Encolando tarea async de metadatos para documento {geonode_info['id']} =====")
            print(f"[DEBUG] File ID: {upload_file.id}")
            print(f"[DEBUG] Token disponible: {token[:50] if token else 'None'}...")

            try:
                from .tasks import update_geonode_metadata_task

                # Usar apply_async con ignore_result=True para ejecución totalmente asíncrona
                # Esto garantiza que el proceso continúe sin esperar la tarea
                task_result = update_geonode_metadata_task.apply_async(
                    args=[
                        upload_file.id,
                        int(geonode_info['id']),
                        token,
                        cookie,
                    ],
                    # countdown=2 hace que la tarea se ejecute 2 segundos después
                    # Esto da tiempo para que se complete la respuesta HTTP primero
                    countdown=2,
                    # No almacenar resultado para mejor rendimiento
                    ignore_result=True,
                )

                print(f"[DEBUG] Tarea encolada exitosamente. Task ID: {task_result.id}")
                print(f"[DEBUG] Los metadatos se actualizarán en 2 segundos en segundo plano")

            except Exception as e:
                print(f"[ERROR] Error al encolar tarea de metadatos: {str(e)}")
                import traceback
                print(f"[ERROR] Traceback completo:")
                print(traceback.format_exc())
                # Don't fail the upload if task queueing fails
                pass

            uploaded_files.append({
                "name": uploaded_file.name,
                "type": uploaded_file.content_type,
                "size": uploaded_file.size,
                "path": filename
            })

    return uploaded_files


def process_files_catalog(request, workspace, user_id):
    uploaded_files = []
    archivos_geonode = request.POST.getlist('archivos_geonode')
    
    if(len(archivos_geonode)> 0):
        token = request.headers.get("Authorization")
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len
        )   
        
        for file in archivos_geonode:
            register = json.loads(file)
            id_document = register['id']
            try:
                url = "https://geonode.dev.geoint.mx/documents/{register}/download".format(register=id_document)
                
                response = requests.get(url)
                response.raise_for_status()
                
                # 2. Crear directorio temporal
                with tempfile.TemporaryDirectory() as tmpdir:
                    # 3. Abrir ZIP desde memoria
                    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                        zf.extractall(tmpdir)
                        
                        for root, dirs, files in os.walk(tmpdir):
                            for file in files:
                                filepath = os.path.join(root, file)
                                uploaded_file = convert_to_uploadedfile(filepath)
                                filename = uploaded_file.name
                                
                                geonode_info = get_geonode_document_uuid_by_id(id_document, token)
                                
                                #Guardar info en la base de datos
                                upload_file= Files()
                                upload_file.geonode_uuid = geonode_info["uuid"]
                                upload_file.geonode_id = id_document
                                upload_file.geonode_type = "Catalogo"
                                upload_file.geonode_category = register['category']
                                upload_file.user_id = user_id
                                upload_file.filename = filename
                                upload_file.document_type = uploaded_file.content_type
                                upload_file.path = os.path.join('uploads/proyectos', str(workspace.id), filename) # delete
                                upload_file.workspace = workspace
                                upload_file.save()
                                archivo_id = upload_file.id
                                print(archivo_id)

                                #extraer texto
                                uploaded_file.seek(0)
                                extracted_text = extract_text_from_file(uploaded_file)

                                # Detectar idioma
                                language = embedder.detect_language(extracted_text)

                                # Dividir texto en chunks
                                chunks = text_splitter.split_text(extracted_text)   

                                # Generar embeddings por lotes (batch) para mejor rendimiento
                                embeddings = embedder.embed_texts(chunks)    

                                # Guardar chunks con embeddings
                                DocumentEmbedding.objects.bulk_create([
                                    DocumentEmbedding(
                                        file=upload_file,
                                        chunk_index=idx,
                                        text=chunk,
                                        embedding=embedding,
                                        language=language,
                                        metadata={
                                            "source": uploaded_file.name,
                                            "content_type": uploaded_file.content_type,
                                            "chunk_size": len(chunk)
                                        }
                                    )
                                    for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings))
                                ])
                                
                                upload_file.processed = True
                                #upload_file.size = os.path.getsize(filename)
                                upload_file.language = language
                                upload_file.save()                                                     

                                
                                uploaded_files.append({
                                    "name": uploaded_file.name,
                                    "type": uploaded_file.content_type,
                                    "size": uploaded_file.size,
                                    "path": filename
                                })

                                    
            except requests.RequestException as e:
                print(f"Error al descargar el archivo: {str(e)}")
                            
    return uploaded_files
        