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
import json
import uuid
import tempfile
import zipfile

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
        
def flatten_json(obj, parent_key="", sep="."):
    items = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            items.extend(flatten_json(v, new_key, sep=sep).items())
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            new_key = f"{parent_key}{sep}{i}" if parent_key else str(i)
            items.extend(flatten_json(v, new_key, sep=sep).items())
    else:
        items.append((parent_key, obj))
    return dict(items)

def extract_text_from_file(file, group_size=5):
    ext = file.name.lower().split(".")[-1]
    
    if ext == "json":
        data = json.load(
            file
        )  # se crea dicionario de python con los pares clave valor del json
        chunks = []
        if isinstance(data, dict):
            data = [data]  # lo hacemos iterable

        for entry in data:
            flattened = flatten_json(entry)
            # Implementamos una limpieza de datos:
            campos_validos = {
                k: str(v)
                for k, v in flattened.items()
                if str(v).strip() not in ["", "Desconocido", "nan", "None", "null"]
            }
            # formateamos el texto de manera legible para los chunks
            texto = "\n".join(
                f"{k.replace('_', ' ').capitalize()}: {v}"
                for k, v in campos_validos.items()
            )
            if texto.strip():
                chunks.append(texto.strip())
        return chunks

    if ext == "pdf":
        reader = PdfReader(file)
        return "\n".join(
            [page.extract_text() for page in reader.pages if page.extract_text()]
        )

    elif ext == "txt":
        return file.read().decode("utf-8")

    if ext == "csv":
        df = pd.read_csv(file)
        df.fillna("Desconocido", inplace=True)
        chunks = []
        for _, row in df.iterrows():
            #     serialized = " | ".join(f"{col}: {str(val)}" for col, val in row.items())
            #     chunks.append(serialized)
            # return chunks
            campos_validos = {
                col: str(val)
                for col, val in row.items()
                if str(val).strip() not in ["", "Desconocido", "nan", "None"]
            }
            texto = "\n".join(
                f"{col.replace('_', ' ').capitalize()}: {val}"
                for col, val in campos_validos.items()
            )
            if texto.strip():
                chunks.append(texto.strip())
        return chunks

    elif ext in ["xlsx", "xls"]:
        df = pd.read_excel(file)
        return df.to_string(index=False)

    elif ext == "docx":
        doc = docx.Document(file)
        return "\n".join([p.text for p in doc.paragraphs])

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
        files = {
            "doc_file": (
                file.name,
                file,
                getattr(file, "content_type", "application/octet-stream"),
            ),
        }
        data = {
            "title": title
        }
        headers = {
            "Authorization": authorization,
            "Accept": "application/json"
        }

        geonode_base_url = os.getenv("GEONODE_SERVER", "https://geonode.dev.geoint.mx")
        upload_url = f"{geonode_base_url}/documents/upload?no__redirect=true"

        # time.sleep(1)
        
        response = requests.post(
            upload_url,
            data=data,
            files=files,
            headers=headers
        )
        print("GeoNode upload response:", response.status_code, response.text)
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
        #type = request.POST.get("type", "archivos cargados")
        
        for uploaded_file in request.FILES.getlist('archivos'):            
            # Guardar el archivo físicamente delete
            #filename = fs.save(uploaded_file.name, uploaded_file)
            filename = uploaded_file.name
            # Guardar el archivo geonode
            #try:
            # geo_response = upload_file_to_geonode(uploaded_file, token, cookie, filename)
            # geo_response.raise_for_status()
            # geo_data = geo_response.json()
            # geonode_info = get_geonode_document_uuid(geo_data.get("url", ""), token)
            #except Exception as e:
            #    print(f"Uploaduploaded_file failed: {str(e)}")

            #Guardar info en la base de datos
            upload_file= Files()
            # upload_file.geonode_uuid = geonode_info["uuid"]
            # upload_file.geonode_id = geonode_info["id"]
            upload_file.geonode_uuid = uuid.uuid4()
            upload_file.geonode_id = 0
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

            if isinstance(extracted_text, list):
                # CSV o JSON serializado por registro → ya son chunks
                chunks = extracted_text
            else:
                try:
                    chunks = text_splitter.split_text(extracted_text)
                except Exception as e:
                    print(f"⚠️ Error al hacer split del texto: {str(e)}")
                    chunks = []
            # Dividir texto en chunks
            # chunks = text_splitter.split_text(extracted_text)   

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
        