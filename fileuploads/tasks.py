"""
Tareas asíncronas de Celery para fileuploads
"""
from celery import shared_task
from django.core.files.uploadedfile import InMemoryUploadedFile
import os
import io


@shared_task(bind=True, name='fileuploads.update_geonode_metadata', ignore_result=True)
def update_geonode_metadata_task(
    self,
    file_id: int,
    geonode_document_id: int,
    authorization: str,
    cookie: str = None,
):
    """
    Tarea asíncrona para actualizar metadatos en GeoNode usando HybridMinimumMetadataExtractor.

    Esta tarea se ejecuta en segundo plano de forma NO BLOQUEANTE.
    La actualización de metadatos es OPCIONAL - si falla, no afecta la carga del archivo.

    Args:
        file_id: ID del registro Files en la base de datos
        geonode_document_id: ID del documento en GeoNode
        authorization: Token de autorización Bearer (puede expirar)
        cookie: Cookie de sesión (opcional)

    Returns:
        dict: Resultado de la actualización de metadatos (opcional)
    """
    from .models import Files
    from .minimum_metadata import HybridMinimumMetadataExtractor

    print(f"[CELERY] ===== Iniciando tarea de metadatos para documento {geonode_document_id} =====")
    print(f"[CELERY] File ID: {file_id}")
    print(f"[CELERY] Token disponible: {authorization[:50] if authorization else 'None'}...")

    try:
        # Obtener el registro del archivo
        file_record = Files.objects.get(id=file_id)
        print(f"[CELERY] Archivo encontrado: {file_record.filename}")

        # Obtener la URL de GeoNode desde variables de entorno
        geonode_server = os.getenv("GEONODE_SERVER", "https://geonode.dev.geoint.mx")

        # Crear el extractor de metadatos
        metadata_extractor = HybridMinimumMetadataExtractor(
            geonode_base_url=geonode_server,
            authorization=authorization,
            cookie=cookie,
        )

        # IMPORTANTE: HybridMinimumMetadataExtractor NO necesita el archivo físico
        # porque usa RAG sobre los embeddings ya almacenados en la base de datos
        # Solo necesita el file_record para acceder a los embeddings

        print(f"[CELERY] Ejecutando extracción de metadatos con RAG...")
        metadata_result = metadata_extractor.process(
            uploaded_file=None,  # No necesitamos el archivo físico
            file_record=file_record,
            geonode_document_id=geonode_document_id,
        )

        metadata_success = metadata_result.get('update_result', {}).get('success', False)
        print(f"[CELERY] ===== Metadatos actualizados: {metadata_success} =====")

        if not metadata_success:
            error_msg = metadata_result.get('update_result', {}).get('error', 'Unknown error')
            status_code = metadata_result.get('update_result', {}).get('status_code')
            print(f"[CELERY] WARNING: Actualización de metadatos falló: {error_msg} (status: {status_code})")

            # Si falló por token expirado, NO lanzar excepción - es un comportamiento esperado
            if 'expired' in str(error_msg).lower() or status_code == 403:
                print(f"[CELERY] INFO: Token JWT expiró - esto es normal en tareas async")
                print(f"[CELERY] INFO: Los metadatos pueden actualizarse manualmente más tarde")
                print(f"[CELERY] INFO: El archivo fue cargado exitosamente")
                # Retornar un resultado en lugar de lanzar excepción
                return {
                    'success': False,
                    'file_id': file_id,
                    'geonode_document_id': geonode_document_id,
                    'reason': 'token_expired',
                    'message': 'Token expiró - actualización de metadatos omitida (archivo cargado correctamente)'
                }

        return {
            'success': metadata_success,
            'file_id': file_id,
            'geonode_document_id': geonode_document_id,
            'result': metadata_result
        }

    except Files.DoesNotExist:
        error_msg = f"File record {file_id} not found in database"
        print(f"[CELERY] ERROR: {error_msg}")
        # No lanzar excepción - solo loguear el error
        print(f"[CELERY] INFO: La tarea se marcará como completada aunque falló")
        return {
            'success': False,
            'file_id': file_id,
            'reason': 'file_not_found',
            'message': error_msg
        }

    except Exception as e:
        print(f"[CELERY] ERROR: Exception en tarea de metadatos: {str(e)}")
        import traceback
        print(f"[CELERY] Traceback completo:")
        print(traceback.format_exc())
        print(f"[CELERY] INFO: La tarea se marcará como completada aunque falló")
        print(f"[CELERY] INFO: El archivo fue cargado correctamente - solo falló la actualización de metadatos")

        # NO re-lanzar la excepción - retornar resultado de error
        # Esto evita que Celery marque la tarea como fallida y la reintente
        return {
            'success': False,
            'file_id': file_id,
            'geonode_document_id': geonode_document_id,
            'reason': 'exception',
            'message': str(e)
        }
@shared_task(bind=True, name='fileuploads.process_file_rag_async', ignore_result=True)
def process_file_rag_async(self, file_id: int):
    """
    Tarea asíncrona para procesar la ingesta RAG de un archivo.
    Realiza:
    1. Extracción de texto
    2. Detección de idioma
    3. Smart Chunking
    4. Generación de Embeddings
    5. Guardado en Vector DB
    """
    from .models import Files, DocumentEmbedding
    from .utils import extract_text_from_file
    from .embeddings_service import embedder
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from django.core.files.storage import default_storage
    
    print(f"[CELERY-RAG] Iniciando procesamiento async para File ID: {file_id}")
    
    try:
        file_record = Files.objects.get(id=file_id)
        
        # Verificar existencia física
        if not os.path.exists(file_record.path):
            print(f"[CELERY-RAG] ERROR: Archivo no encontrado en disco: {file_record.path}")
            return
            
        print(f"[CELERY-RAG] Procesando archivo físico: {file_record.path}")
        
        # 1. Extracción de texto
        # Necesitamos abrir el archivo como objeto file-like para extract_text_from_file
        with open(file_record.path, 'rb') as f:
            # Simular objeto con atributo name para que extract_text_from_file detecte extensión
            # OJO: extract_text_from_file usa file.name.lower().split(".")
            # Creamos un wrapper o usamos el objeto file directamente si tiene name
            if not hasattr(f, 'name') or not f.name:
                # Fallback, aunque open() usualmente setea name
                pass
                
            extracted = extract_text_from_file(f)
            
        chunks = []
        json_originals = []
        metadata_chunks = []
        
        # Lógica de normalización (copiada de utils.process_files refactorizado)
        if isinstance(extracted, tuple):
            chunks, json_originals, metadata_chunks = extracted
            # Detección idioma JSON
            try:
                sample = " ".join(chunks[:3])
                language = embedder.detect_language(sample)
            except:
                language = "es"
                
        elif isinstance(extracted, list):
            chunks = extracted
            # Detección idioma CSV
            try:
                sample = " ".join(chunks[:3])
                language = embedder.detect_language(sample)
            except:
                language = "es"
                
        elif isinstance(extracted, str):
            try:
                language = embedder.detect_language(extracted)
                chunks = embedder._smart_text_splitting(extracted, language)
                print(f"[CELERY-RAG] Smart Chunking: {len(chunks)} chunks ({language})")
            except Exception as e:
                print(f"[CELERY-RAG] Error en splitting: {e}")
                chunks = []
        else:
            chunks = []
            
        if not chunks:
            print("[CELERY-RAG] No se generaron chunks. Abortando.")
            return

        # 3. Embeddings (Paralelismo)
        BATCH_SIZE = 150
        MAX_WORKERS = 12
        
        def process_batch(batch, batch_idx):
            try:
                return embedder.embed_texts(batch)
            except Exception as e:
                print(f"[CELERY-RAG] Error batch {batch_idx}: {e}")
                return [np.zeros(768) for _ in batch]

        batches = [chunks[i:i + BATCH_SIZE] for i in range(0, len(chunks), BATCH_SIZE)]
        all_embeddings = []
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(process_batch, batch, i): i for i, batch in enumerate(batches)}
            for future in as_completed(futures):
                all_embeddings.extend(future.result())
                
        # 4. Guardado
        objs = []
        for idx, (chunk, embedding) in enumerate(zip(chunks, all_embeddings)):
            objs.append(
                DocumentEmbedding(
                    file=file_record,
                    chunk_index=idx,
                    text=chunk,
                    text_json=json_originals[idx] if json_originals else None,
                    metadata_json=metadata_chunks[idx] if metadata_chunks else None,
                    embedding=embedding,
                    language=language,
                    metadata={
                        "source": file_record.filename,
                        "content_type": file_record.document_type,
                        "chunk_size": len(chunk),
                        "smart_chunking": True
                    },
                )
            )
            
        DocumentEmbedding.objects.bulk_create(objs, batch_size=500)
        
        file_record.processed = True
        file_record.language = language
        file_record.save()
        
        print(f"[CELERY-RAG] Finalizado con éxito File ID: {file_id}")

    except Exception as e:
        print(f"[CELERY-RAG] CRITICAL ERROR: {str(e)}")
        import traceback
        traceback.print_exc()
