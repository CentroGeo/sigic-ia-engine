# import textract
import pandas as pd
import docx
from PyPDF2 import PdfReader
from sentence_transformers import SentenceTransformer
from .models import DocumentEmbedding
import io
import requests
import os

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
        if cookie:
            headers["Cookie"] = cookie
        geonode_base_url = os.environ.get("GEONODE_SERVER")
        upload_url = f"{geonode_base_url}/documents/upload?no__redirect=true"
        response = requests.post(
            upload_url,
            files=files,
            data=data,
            headers=headers,
            timeout=30
        )
        return  response

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
