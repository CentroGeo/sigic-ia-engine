# import textract
import pandas as pd

import docx
from PyPDF2 import PdfReader
from sentence_transformers import SentenceTransformer
from .models import DocumentEmbedding, Files
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from langchain_text_splitters import RecursiveCharacterTextSplitter
from .embeddings_service import embedder

# from requests.auth import HTTPBasicAuth
from django.core.files.uploadedfile import SimpleUploadedFile
import mimetypes
import io
import requests
import os
import uuid

# import time
# import base64
import json

# import uuid
import tempfile
import zipfile
import ijson
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
# from datetime import datetime
from time import time

MAX_MEMORY_MB = 100
# MAX_JSON_ENTRIES_RAG = 1000
CSV_CHUNK_SIZE = 5000
MAX_WORKERS = 12
BATCH_SIZE = 150

model = SentenceTransformer("all-MiniLM-L6-v2")


def convert_to_uploadedfile(filepath, file_name=None):
    #filename = os.path.basename(filepath)
    filename = file_name
    
    # Detectar content_type
    content_type, _ = mimetypes.guess_type(filepath)
    if content_type is None:
        content_type = "application/octet-stream"

    with open(filepath, "rb") as f:
        return SimpleUploadedFile(
            name=filename, content=f.read(), content_type=content_type
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


def get_keys_and_types(data):
    def get_type(value):
        if isinstance(value, str):
            return "string"
        elif isinstance(value, int):
            return "integer"
        elif isinstance(value, bool):
            return "boolean"
        elif isinstance(value, list):
            return "array"
        elif isinstance(value, dict):
            return "object"
        else:
            return "unknown"

    result = {}

    for key, value in data.items():
        if any(char.isdigit() for char in key):
            parts = key.split(".")
            # Filtrar las partes con números y transformarlas en formato 'array'
            new_key_parts = [part if not part.isdigit() else "array" for part in parts]
            new_key = ".".join(new_key_parts)

            # Si la clave ya está en el resultado, simplemente agregamos el tipo
            if new_key not in result:
                result[new_key] = get_type(value)
        else:
            result[key] = get_type(value)

    return result


def limpiar_valor(valor):
    """
    Limpia y normaliza valores provenientes de archivos CSV o JSON antes de generar chunks.
    Maneja listas, diccionarios, nulos y valores repetitivos de 'Desconocido', 'None', etc.
    """

    if valor is None:
        return "(sin valor)"

    # Convertir NaN o nulos
    if str(valor).strip().lower() in ["", "none", "nan", "null", "desconocido"]:
        return "(sin valor)"

    # Si el valor es una lista  concatenar sus elementos
    if isinstance(valor, list):
        partes = [limpiar_valor(v) for v in valor]
        # Filtrar los "(sin valor)" para evitar ruido
        partes = [p for p in partes if p != "(sin valor)"]
        return ", ".join(partes) if partes else "(sin valor)"

    # Si el valor es un dict  representar como texto clave: valor
    if isinstance(valor, dict):
        partes = []
        for k, v in valor.items():
            subvalor = limpiar_valor(v)
            if subvalor != "(sin valor)":
                partes.append(f"{k.replace('_', ' ').capitalize()}: {subvalor}")
        return "; ".join(partes) if partes else "(sin valor)"

    # Normalizar strings largos
    if isinstance(valor, str):
        texto = valor.strip()
        # Evitar saltos de línea excesivos o espacios
        texto = " ".join(texto.split())
        # Truncar valores muy extensos (por ejemplo abstracts largos)
        if len(texto) > 5000:
            texto = texto[:5000] + "..."
        return texto or "(sin valor)"

    # Cualquier otro tipo → convertir a string limpio
    return str(valor).strip() or "(sin valor)"


def iter_json_entries(file, file_size_mb):
    if file_size_mb < MAX_MEMORY_MB:
        data = json.load(file)
        if isinstance(data, dict):
            data = [data]
        return iter(data)
    else:
        print("🚨 JSON grande detectado, usando streaming (ijson)")
        return ijson.items(file, "item")


def json_entry_to_text_and_metadata(entry):
    flattened = flatten_json(entry)

    campos_validos = {
        k: limpiar_valor(v)
        for k, v in flattened.items()
        if limpiar_valor(v) != "(sin valor)"
    }

    texto = "\n".join(
        f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in campos_validos.items()
    )

    metadata = get_keys_and_types(flattened)

    return texto.strip(), metadata


def extract_csv(file, file_size_mb):
    chunks = []

    def process_df(df):
        df.fillna("(sin valor)", inplace=True)
        for _, row in df.iterrows():
            texto = "\n".join(
                f"{col.replace('_', ' ').capitalize()}: {limpiar_valor(val)}"
                for col, val in row.items()
            )
            if texto.strip():
                chunks.append(texto.strip())

    try:
        if file_size_mb < MAX_MEMORY_MB:
            process_df(pd.read_csv(file, dtype=str, low_memory=False))
        else:
            print("📦 CSV grande, lectura por chunks")
            for df_chunk in pd.read_csv(
                file, dtype=str, chunksize=CSV_CHUNK_SIZE, low_memory=False
            ):
                process_df(df_chunk)

        return chunks

    except Exception as e:
        print(f"⚠️ Error procesando CSV: {e}")
        return []


def extract_text_from_file(file):
    ext = file.name.lower().split(".")[-1]

    try:
        file.seek(0, 2)
        file_size_mb = file.tell() / (1024 * 1024)
        file.seek(0)
    except Exception:
        file_size_mb = 0

    print(f"📦 Procesando {file.name} ({file_size_mb:.2f} MB)")

    # -------- JSON --------
    if ext == "json":
        chunks, originals, metadata = [], [], []

        try:
            for idx, entry in enumerate(iter_json_entries(file, file_size_mb)):
                texto, meta = json_entry_to_text_and_metadata(entry)
                if texto:
                    chunks.append(texto)
                    metadata.append(meta)
                    originals.append(entry)

            print(f"🧩 JSON → {len(chunks)} chunks generados")
            return chunks, originals, metadata

        except Exception as e:
            print(f"❌ Error procesando JSON: {e}")
            return None

    # -------- CSV --------
    if ext == "csv":
        chunks = extract_csv(file, file_size_mb)
        print(f"🧩 CSV → {len(chunks)} chunks generados")
        return chunks

    # -------- PDF --------
    if ext == "pdf":
        try:
            reader = PdfReader(file)
            text = "\n".join(
                page.extract_text()
                for page in reader.pages
                if page.extract_text()
            )
            return text.strip() if text.strip() else None
        except Exception as e:
            print(f"❌ Error procesando PDF: {e}")
            return None

    # -------- DOCX --------
    if ext == "docx":
        try:
            doc = docx.Document(file)
            text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            return text.strip() if text.strip() else None
        except Exception as e:
            print(f"❌ Error procesando DOCX: {e}")
            return None

    # -------- TXT --------
    if ext == "txt":
        try:
            text = file.read().decode("utf-8").strip()
            return text if text else None
        except Exception as e:
            print(f"❌ Error procesando TXT: {e}")
            return None

    # -------- NO SOPORTADO --------
    print(f"🚫 Formato no soportado: .{ext}")
    return None


# def extract_text_from_file(file, group_size=5):
#     ext = file.name.lower().split(".")[-1]
#     chunks = []

#     # --- Detección de tamaño ---
#     try:
#         file.seek(0, 2)
#         file_size_mb = file.tell() / (1024 * 1024)
#         file.seek(0)
#     except Exception:
#         file_size_mb = 0

#     MAX_MEMORY_MB = 100
#     print(f"Tamaño de archivo detectado: {file_size_mb:.2f} MB")

#     # --- JSON ---
#     if ext == "json":
# <<<<<<< Updated upstream
#         # data = json.load(
#         #     file
#         # )  # se crea dicionario de python con los pares clave valor del json

#         chunks = []
#         chunks_originales = []
#         metadata_chunks = []
#         count = 0
#         for entry in ijson.items(file, "item"):
#             if (count < 100):
#                 count += 1
#                 flattened = flatten_json(entry)
#                 campos_validos = {
#                     k: str(v)
#                     for k, v in flattened.items()
#                     if str(v).strip() not in ["", "Desconocido", "nan", "None", "null"]
#                 }
#                 texto = "\n".join(
#                     f"{k.replace('_', ' ').capitalize()}: {v}"
#                     for k, v in campos_validos.items()
#                 )

#                 if texto.strip():
#                     chunks.append(texto.strip())
#                     metadata_chunks.append(get_keys_and_types(flattened))
#                 chunks_originales.append(entry)

#         return chunks, chunks_originales, metadata_chunks
# =======
#         try:
#             if file_size_mb < MAX_MEMORY_MB:
#                 # Carga normal en memoria
#                 data = json.load(file)
#                 if isinstance(data, dict):
#                     data = [data]   # lo hacemos iterable

#                 for entry in data:
#                     #  aplanamos estructuras json profundas
#                     flattened = flatten_json(entry)
#                     flat_text = []
#                     for k, v in flattened.items():
#                         flat_text.append(f"{k.replace('_', ' ').capitalize()}: {limpiar_valor(v)}")
#                     chunks.append("\n".join(flat_text))
# >>>>>>> Stashed changes

#             else:
#                 # Lectura en streaming para JSON gigante
#                 print("🚨 Archivo JSON grande detectado, usando lectura por streaming con ijson...")
#                 for i, item in enumerate(ijson.items(file, "item")):
#                     flattened = flatten_json(item)
#                     flat_text = []
#                     for k, v in flattened.items():
#                         flat_text.append(f"{k.replace('_', ' ').capitalize()}: {limpiar_valor(v)}")
#                     chunks.append("\n".join(flat_text))
#                     if (i + 1) % 1000 == 0:
#                         print(f"Procesados {i + 1} objetos JSON")

#             return chunks

#         except Exception as e:
#             print(f"⚠️ Error al procesar JSON: {e}")
#             return []

#     # --- PDF ---
#     if ext == "pdf":
#         reader = PdfReader(file)
#         return "\n".join([page.extract_text() for page in reader.pages if page.extract_text()])

#     # --- TXT ---
#     elif ext == "txt":
#         return file.read().decode("utf-8")

#     # --- CSV ---
#     if ext == "csv":
#         try:
#             if file_size_mb < MAX_MEMORY_MB:
#                 # Lectura normal
#                 df = pd.read_csv(file, dtype=str, low_memory=False)
#                 df.fillna("(sin valor)", inplace=True)
#                 for _, row in df.iterrows():
#                     campos_validos = {col: limpiar_valor(val) for col, val in row.items()}
#                     texto = "\n".join(f"{col.replace('_', ' ').capitalize()}: {val}" for col, val in campos_validos.items())
#                     if texto.strip():
#                         chunks.append(texto.strip())

#             else:
#                 # Lectura por partes (streaming)
#                 print("📦 Archivo grande detectado, leyendo CSV por partes (chunksize=5000)...")
#                 for i, df_chunk in enumerate(pd.read_csv(file, dtype=str, chunksize=5000, low_memory=False)):
#                     df_chunk.fillna("(sin valor)", inplace=True)
#                     for _, row in df_chunk.iterrows():
#                         campos_validos = {col: limpiar_valor(val) for col, val in row.items()}
#                         texto = "\n".join(f"{col.replace('_', ' ').capitalize()}: {val}" for col, val in campos_validos.items())
#                         if texto.strip():
#                             chunks.append(texto.strip())
#                     print(f"Procesado chunk CSV {i + 1}")

#             return chunks

#         except Exception as e:
#             print(f"⚠️ Error al procesar CSV: {e}")
#             return []


def vectorize_and_store_text(text, file_id):
    vector = model.encode(text).tolist()

    DocumentEmbedding.objects.create(file_id=file_id, text=text, embedding=vector)


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

def upload_image_to_geonode(file, filename, token=""):
    file_bytes = file.read()
    files = {"file": (filename, file_bytes, file.content_type)}
    data = {"category": "contextos"}
    headers = {"Authorization": token}

    geonode_base_url = os.environ.get("GEONODE_SERVER")
    upload_url = f"{geonode_base_url}/sigic/ia/mediauploads/upload"

    response = requests.post(
        upload_url, files=files, data=data, headers=headers, timeout=30
    )

    return response


def get_geonode_document_uuid(doc_url, authorization=None, category_info="documents"):
    """
    Se extrae el ID numérico de la URL devuelta por GeoNode al acargar el archivo y se
    obtiene el UUID completo del documento.
    """
    headers = {"Accept": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    try:
        doc_id = doc_url.strip("/").split("/")[-1]
        geonode_base_url = os.environ.get("GEONODE_SERVER")
        response = requests.get(
            f"{geonode_base_url}/api/v2/{category_info}/{doc_id}",
            headers=headers,
        )
        response.raise_for_status()
        resp = response.json()

        return {"uuid": resp[category_info[:-1]]["uuid"], "id": doc_id}

    except Exception as e:
        raise ValueError(f"No se pudo obtener el UUID del documento: {str(e)}")


def get_geonode_document_uuid_by_id(
    doc_id, authorization=None, category_info="documents"
):
    """
    Se extrae el ID numérico de la URL devuelta por GeoNode al acargar el archivo y se
    obtiene el UUID completo del documento.
    """
    headers = {"Accept": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    try:
        geonode_base_url = os.environ.get("GEONODE_SERVER")
        response = requests.get(
            f"{geonode_base_url}/api/v2/{category_info}/{doc_id}",
            headers=headers,
        )
        response.raise_for_status()
        resp = response.json()

        if category_info == "documents":
            download_url = resp[category_info[:-1]]["download_url"]
        else:
            props = []
            attribute_set = resp[category_info[:-1]]["attribute_set"]
            for attribute in attribute_set:
                if attribute["visible"] is True:
                    props.append(attribute["attribute"])

            links = resp[category_info[:-1]]["links"]
            link_csv = next(
                (
                    x
                    for x in links
                    if x.get("extension") == "csv"
                    and x.get("link_type") == "data"
                    and x.get("mime") == "csv"
                    and x.get("name") == "CSV"
                ),
                None,
            )

            if link_csv is not None:
                download_url = link_csv["url"] + f"&propertyName={','.join(props)}"
            else:
                download_url = None

        return {
            "uuid": resp[category_info[:-1]]["uuid"],
            "id": doc_id,
            "url_download": download_url,
        }

    except Exception as e:
        raise ValueError(f"No se pudo obtener el UUID del documento: {str(e)}")

def process_files(request, workspace, user_id):
    start_time = time()
    uploaded_files = []

    if "archivos" not in request.FILES:
        print("⚠️ No se encontraron archivos.")
        return uploaded_files

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
        length_function=len
    )

    file_type = request.POST.get("type", "archivos cargados")

    for uploaded_file in request.FILES.getlist("archivos"):
        print(f"\n🚀 Procesando archivo: {uploaded_file.name}")

        # --- Crear registro del archivo ---
        upload_file = Files(
            geonode_uuid=uuid.uuid4(),
            geonode_id=0,
            geonode_type=file_type,
            geonode_category="documents",
            user_id=user_id,
            filename=uploaded_file.name,
            document_type=uploaded_file.content_type,
            path=os.path.join(
                "uploads/proyectos", str(workspace.id), uploaded_file.name
            ),
            workspace=workspace,
        )
        upload_file.save()

        # =========================
        # 1. Extracción de contenido
        # =========================
        uploaded_file.seek(0)
        extracted = extract_text_from_file(uploaded_file)

        # Limpieza defensiva (CLAVE para evitar bug)
        chunks = []
        json_originals = []
        metadata_chunks = []
        is_structured = False

        # --- Normalización estricta ---
        if isinstance(extracted, tuple):
            # JSON → (chunks, originals, metadata)
            chunks, json_originals, metadata_chunks = extracted
            is_structured = True

        elif isinstance(extracted, list):
            # CSV → lista de textos
            chunks = extracted
            is_structured = True

        elif isinstance(extracted, str):
            # PDF / TXT → texto plano
            try:
                chunks = text_splitter.split_text(extracted)
            except Exception as e:
                print(f"⚠️ Error al dividir texto: {e}")
                continue

        else:
            print("⚠️ Formato inesperado al extraer texto")
            continue

        # Fuerza nueva referencia (evita contaminación entre archivos)
        chunks = list(chunks)

        if not chunks:
            print("🚧 No se generaron chunks, se omite el archivo.")
            continue

        print(f"🧩 Chunks generados: {len(chunks)}")

        # =========================
        # 2. Detección de idioma
        # =========================
        try:
            sample = " ".join(chunks[:3])
            language = embedder.detect_language(sample)
        except Exception as e:
            print(f"⚠️ Error detectando idioma: {e}")
            language = "unknown"

        # =========================
        # 3. Embeddings en paralelo
        # =========================
        def process_batch(batch, batch_idx):
            print(f"🧵 Batch {batch_idx + 1}: {len(batch)} chunks")
            try:
                return embedder.embed_texts(batch)
            except Exception as e:
                print(f"⚠️ Error en batch {batch_idx + 1}: {e}")
                return [np.zeros(768) for _ in batch]

        batches = [
            chunks[i:i + BATCH_SIZE]
            for i in range(0, len(chunks), BATCH_SIZE)
        ]

        print(
            f"🚀 Paralelismo: {len(batches)} batches | "
            f"batch_size={BATCH_SIZE} | workers={MAX_WORKERS}"
        )

        all_embeddings = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(process_batch, batch, i): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                all_embeddings.extend(future.result())

        print(f"📈 Embeddings generados: {len(all_embeddings)}")

        # =========================
        # 4. Guardado en BD
        # =========================
        objs = []

        for idx, (chunk, embedding) in enumerate(zip(chunks, all_embeddings)):
            objs.append(
                DocumentEmbedding(
                    file=upload_file,
                    chunk_index=idx,
                    text=chunk,
                    text_json=json_originals[idx] if json_originals else None,
                    metadata_json=metadata_chunks[idx] if metadata_chunks else None,
                    embedding=embedding,
                    language=language,
                    metadata={
                        "source": uploaded_file.name,
                        "content_type": uploaded_file.content_type,
                        "chunk_size": len(chunk),
                    },
                )
            )

        DocumentEmbedding.objects.bulk_create(objs, batch_size=500)

        upload_file.processed = True
        upload_file.language = language
        upload_file.save()

        uploaded_files.append({
            "name": uploaded_file.name,
            "type": uploaded_file.content_type,
            "path": upload_file.path,
        })

        print(f"✅ Archivo procesado correctamente: {uploaded_file.name}")
    end_time = time()
    elapsed = end_time - start_time
    mins, secs = divmod(elapsed, 60)
    print(f"✅ Procesamiento completado en {int(mins)} min {secs:.2f} s ({len(uploaded_files)} archivo/s)")
    return uploaded_files



def process_files_catalog(request, workspace, user_id):
    uploaded_files = []
    archivos_geonode = request.POST.getlist("archivos_geonode")

    if len(archivos_geonode) > 0:
        token = request.headers.get("Authorization")
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000, chunk_overlap=200, length_function=len
        )

        for file in archivos_geonode:
            register = json.loads(file)
            id_document = register["id"]
            file_name = register["nombre"]
            
            try:
                # url = "https://geonode.dev.geoint.mx/documents/{register}/download".format(register=id_document)
                # url = "https://geonode.dev.geoint.mx/datasets/{register}/download".format(register=id_document)

                geonode_info = get_geonode_document_uuid_by_id(
                    id_document, token, register["category"]
                )
                print("url", geonode_info["url_download"], flush=True)
                url = geonode_info["url_download"]

                response = requests.get(url)
                response.raise_for_status()

                # 2. Crear directorio temporal
                with tempfile.TemporaryDirectory() as tmpdir:
                    content = io.BytesIO(response.content)
                    if zipfile.is_zipfile(content):
                        # 3. Abrir ZIP desde memoria
                        with zipfile.ZipFile(content) as zf:
                            zf.extractall(tmpdir)

                            for root, dirs, files in os.walk(tmpdir):
                                for file in files:
                                    filepath = os.path.join(root, file)
                                    uploaded_file = convert_to_uploadedfile(filepath, file_name)
                                    filename = uploaded_file.name

                                    # Guardar info en la base de datos
                                    upload_file = Files()
                                    upload_file.geonode_uuid = geonode_info["uuid"]
                                    upload_file.geonode_id = id_document
                                    upload_file.geonode_type = "Catalogo"
                                    upload_file.geonode_category = register["category"]
                                    upload_file.user_id = user_id
                                    upload_file.filename = filename
                                    upload_file.document_type = (
                                        uploaded_file.content_type
                                    )
                                    upload_file.path = os.path.join(
                                        "uploads/proyectos", str(workspace.id), filename
                                    )  # delete
                                    upload_file.workspace = workspace
                                    upload_file.save()
                                    archivo_id = upload_file.id
                                    print(archivo_id)

                                    # extraer texto
                                    uploaded_file.seek(0)
                                    extracted_text = extract_text_from_file(
                                        uploaded_file
                                    )

                                    # Detectar idioma
                                    language = embedder.detect_language(extracted_text)

                                    # Dividir texto en chunks
                                    if isinstance(extracted_text, list):
                                        # CSV o JSON serializado por registro → ya son chunks
                                        chunks = extracted_text
                                    else:
                                        try:
                                            chunks = text_splitter.split_text(
                                                extracted_text
                                            )
                                        except Exception as e:
                                            print(
                                                f"⚠️ Error al hacer split del texto: {str(e)}"
                                            )
                                            chunks = []

                                    # Generar embeddings por lotes (batch) para mejor rendimiento
                                    embeddings = embedder.embed_texts(chunks)

                                    # Guardar chunks con embeddings
                                    DocumentEmbedding.objects.bulk_create(
                                        [
                                            DocumentEmbedding(
                                                file=upload_file,
                                                chunk_index=idx,
                                                text=chunk,
                                                embedding=embedding,
                                                language=language,
                                                metadata={
                                                    "source": uploaded_file.name,
                                                    "content_type": uploaded_file.content_type,
                                                    "chunk_size": len(chunk),
                                                },
                                            )
                                            for idx, (chunk, embedding) in enumerate(
                                                zip(chunks, embeddings)
                                            )
                                        ]
                                    )

                                    upload_file.processed = True
                                    # upload_file.size = os.path.getsize(filename)
                                    upload_file.language = language
                                    upload_file.save()

                                    uploaded_files.append(
                                        {
                                            "name": uploaded_file.name,
                                            "type": uploaded_file.content_type,
                                            "size": uploaded_file.size,
                                            "path": filename,
                                        }
                                    )
                    else:
                        filename = file_name
                        # content_disp = response.headers.get("Content-Disposition", "")
                        # if content_disp:
                        #     match = re.findall('filename="?([^"]+)"?', content_disp)
                        #     if match:
                        #         filename = match[0]

                        print("csv!!", filename, flush=True)
                        file_path = os.path.join(tmpdir, filename)
                        with open(file_path, "wb") as f:
                            f.write(response.content)

                        for root, dirs, files in os.walk(tmpdir):
                            for file in files:
                                filepath = os.path.join(root, file)
                                uploaded_file = convert_to_uploadedfile(filepath)
                                filename = uploaded_file.name

                                # Guardar info en la base de datos
                                upload_file = Files()
                                upload_file.geonode_uuid = geonode_info["uuid"]
                                upload_file.geonode_id = id_document
                                upload_file.geonode_type = "Catalogo"
                                upload_file.geonode_category = register["category"]
                                upload_file.user_id = user_id
                                upload_file.filename = filename
                                upload_file.document_type = uploaded_file.content_type
                                upload_file.path = os.path.join(
                                    "uploads/proyectos", str(workspace.id), filename
                                )  # delete
                                upload_file.workspace = workspace
                                upload_file.save()
                                archivo_id = upload_file.id
                                print(archivo_id)

                                # extraer texto
                                uploaded_file.seek(0)
                                extracted_text = extract_text_from_file(uploaded_file)

                                # Detectar idioma
                                language = embedder.detect_language(extracted_text)

                                # Dividir texto en chunks
                                if isinstance(extracted_text, list):
                                    # CSV o JSON serializado por registro → ya son chunks
                                    chunks = extracted_text
                                else:
                                    try:
                                        chunks = text_splitter.split_text(
                                            extracted_text
                                        )
                                    except Exception as e:
                                        print(
                                            f"⚠️ Error al hacer split del texto: {str(e)}"
                                        )
                                        chunks = []

                                # Generar embeddings por lotes (batch) para mejor rendimiento
                                embeddings = embedder.embed_texts(chunks)

                                # Guardar chunks con embeddings
                                DocumentEmbedding.objects.bulk_create(
                                    [
                                        DocumentEmbedding(
                                            file=upload_file,
                                            chunk_index=idx,
                                            text=chunk,
                                            embedding=embedding,
                                            language=language,
                                            metadata={
                                                "source": uploaded_file.name,
                                                "content_type": uploaded_file.content_type,
                                                "chunk_size": len(chunk),
                                            },
                                        )
                                        for idx, (chunk, embedding) in enumerate(
                                            zip(chunks, embeddings)
                                        )
                                    ]
                                )

                                upload_file.processed = True
                                # upload_file.size = os.path.getsize(filename)
                                upload_file.language = language
                                upload_file.save()

                                uploaded_files.append(
                                    {
                                        "name": uploaded_file.name,
                                        "type": uploaded_file.content_type,
                                        "size": uploaded_file.size,
                                        "path": filename,
                                    }
                                )

            except requests.RequestException as e:
                print(f"Error al descargar el archivo: {str(e)}")

    return uploaded_files