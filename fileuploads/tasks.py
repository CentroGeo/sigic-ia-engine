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