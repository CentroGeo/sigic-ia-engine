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
import io
import requests
import os
import time

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

def upload_file_to_geonode(file, token, cookie=None, title="Sin título"):
        files = {
            "doc_file": (file.name, file, file.content_type)
        }
        data = {
            "title": title
        }
        headers = {
            "Authorization": token
        }
        # if cookie:
        #     headers["Cookie"] = cookie
        geonode_base_url = os.environ.get("GEONODE_SERVER")
        upload_url = f"{geonode_base_url}/documents/upload?no__redirect=true"

        time.sleep(1)

        response = requests.post(
            upload_url,
            files=files,
            data=data,
            headers=headers,
            timeout=30
        )
        return  response
        return "ok"

def get_geonode_document_uuid(doc_url):
    """
    Se extrae el ID numérico de la URL devuelta por GeoNode al acargar el archivo y se 
    obtiene el UUID completo del documento.
    """
    try:
        doc_id = doc_url.strip("/").split("/")[-1]
        geonode_base_url = os.environ.get("GEONODE_SERVER")
        response = requests.get(f"{geonode_base_url}/api/v2/documents/{doc_id}")
        response.raise_for_status()
        return response.json()["document"]["uuid"]
    except Exception as e:
        raise ValueError(f"No se pudo obtener el UUID del documento: {str(e)}")


def process_files(request, workspace, user_id):
    uploaded_files = []
    if 'archivos' in request.FILES:
        fs = FileSystemStorage(location=os.path.join(settings.MEDIA_ROOT, 'uploads/proyectos', str(workspace.id)))
        os.makedirs(fs.location, exist_ok=True)

        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len
        )                
        
        token = 'Bearer ' + request.POST.get("token")
        #token = 'Bearer ' + 'eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIiwia2lkIiA6ICJUZ1MxMG9adEhzcU12NDIwWG51ZGVXdFpsZ3pMSmRvUUNGRmpNSW5FdllJIn0.eyJleHAiOjE3NTU4MDU2MTQsImlhdCI6MTc1NTgwNTMxNCwiYXV0aF90aW1lIjoxNzU1ODA0NTA3LCJqdGkiOiJvbnJ0YWM6Zjg3ZmI1YTMtOWRkNi1hN2E1LTU4MTUtNmY3NDVkMmNkZDU4IiwiaXNzIjoiaHR0cHM6Ly9pYW0uZGV2Lmdlb2ludC5teC9yZWFsbXMvc2lnaWMiLCJhdWQiOiJhY2NvdW50Iiwic3ViIjoiNDcwNTA3NGQtMjkwYi00NjMwLWEwOTktNDIzN2EwZDQwMTFmIiwidHlwIjoiQmVhcmVyIiwiYXpwIjoic2lnaWMtbnV4dC1kZXYiLCJzaWQiOiJlYjI2Y2NiNS02ZjBmLTQyNDYtYjk1ZC00OGVjMDUyYjAwN2YiLCJhY3IiOiIwIiwiYWxsb3dlZC1vcmlnaW5zIjpbImh0dHA6Ly9sb2NhbGhvc3Q6MzAwMCIsImh0dHBzOi8vc2lnaWMuZGV2Lmdlb2ludC5teCJdLCJyZWFsbV9hY2Nlc3MiOnsicm9sZXMiOlsib2ZmbGluZV9hY2Nlc3MiLCJ1bWFfYXV0aG9yaXphdGlvbiIsImRlZmF1bHQtcm9sZXMtc2lnaWMiXX0sInJlc291cmNlX2FjY2VzcyI6eyJhY2NvdW50Ijp7InJvbGVzIjpbIm1hbmFnZS1hY2NvdW50IiwibWFuYWdlLWFjY291bnQtbGlua3MiLCJ2aWV3LXByb2ZpbGUiXX19LCJzY29wZSI6Im9wZW5pZCBlbWFpbCBwcm9maWxlIiwiZW1haWxfdmVyaWZpZWQiOnRydWUsIm5hbWUiOiJGZXJuYW5kbyBWYWxsZSIsInByZWZlcnJlZF91c2VybmFtZSI6ImZlcnZhbGxlIiwiZ2l2ZW5fbmFtZSI6IkZlcm5hbmRvIiwibG9jYWxlIjoiZXMiLCJmYW1pbHlfbmFtZSI6IlZhbGxlIiwiZW1haWwiOiJwc3AuZnZhbGxlQGNlbnRyb2dlby5lZHUubXgifQ.koeGz60zoV0sFFefJJUhU7yXbJEeX3lLRpy_2Zh1zZ50eS96PdG6aH3WOfiFWRiZeX2nOZricTzvMCCiANOrGdYWJd3L8YlizlA3-abBTRRF_Wz8TCjDEIUPZlBCWE-2a_GlaM0JRgmxmB348ddv3dYPRTA651esroLczZd8vBIpEsTQ8WFfEIS2-Nn1NGUIjoeoZvIuEG7xfiSxcUHvChRDZPbB-SRdn4NdBYKcTUWcpdg6ESglnH8PWpg--eciVlqEVPj5f7k2FoFXzj3Naym_uc8tDwrrSGa6DJ3sgXtJ-VfUFXK3xUMYi4O-JfR72Rs0NUWcK04ye31wC9eRqA'
        cookie = request.headers.get("Cookie")
        type = request.POST.get("type", "archivos cargados")
        
        for uploaded_file in request.FILES.getlist('archivos'):            
            # Guardar el archivo físicamente delete
            filename = fs.save(uploaded_file.name, uploaded_file)

            # Guardar el archivo geonode
            #try:
            geo_response = upload_file_to_geonode(uploaded_file, token, cookie, filename)
            geo_response.raise_for_status()
            geo_data = geo_response.json()
            # document_uuid = get_geonode_document_uuid(geo_data.get("url", ""))
            #except Exception as e:
            #    print(f"Upload failed: {str(e)}")

            
            #Guardar info en la base de datos
            upload_file= Files()
            #upload_file.document_id = document_uuid
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
        