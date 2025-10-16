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
import io
import requests
import os
import time
import base64

model = SentenceTransformer("all-MiniLM-L6-v2")

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
                permissions_url = f"{geonode_base_url}/api/v2/resources/{doc_id}/permissions"
                permissions_payload = {
                    "uuid": None,  # Will be filled by GeoNode
                    "perm_spec": [
                        {
                            "name": "view",
                            "type": "user",
                            "avatar": None,
                            "permissions": "view",
                            "user": {
                                "username": "AnonymousUser",
                                "first_name": "",
                                "last_name": "",
                                "avatar": None,
                                "perms": ["view"]
                            }
                        }
                    ]
                }

                perm_headers = {
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                    "Accept": "application/json"
                }

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
        type = request.POST.get("type", "archivos cargados")

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
            upload_file.geonode_type = type
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
            
    return uploaded_files
        