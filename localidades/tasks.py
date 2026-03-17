from celery import shared_task
import os
from .models import Spatialization
from .utils import extract_localities_from_context

@shared_task(
    bind=True,
    name="localidades.generate_spatialization",
    time_limit=1800,
    soft_time_limit=1500,
)
def generate_spatialization_task(self, spatialization_id: int, authorization: str = "", refresh_token: str = "") -> dict:
    """
    Tarea Celery que genera la extracción de localidades asíncronamente y la persiste.
    """
    sp = Spatialization.objects.select_related("context").get(pk=spatialization_id)
    sp.status = "processing"
    sp.task_id = self.request.id
    sp.save(update_fields=["status", "task_id", "updated_date"])

    def set_progress(percentage: int):
        sp.progress = percentage
        sp.save(update_fields=["progress", "updated_date"])

    try:
        file_ids = list(sp.files_used.values_list("id", flat=True))
        
        result = extract_localities_from_context(
            context_id=sp.context.id,
            model="deepseek-r1:32b", # Hardcoded or dynamic model if we added it to model
            focus=sp.focus,
            file_ids=file_ids,
            entity_types=sp.entity_types,
            export_format=sp.export_format,
            geometry_type=sp.geometry_type,
            custom_instructions=sp.custom_instructions or "",
            authorization=authorization,
            refresh_token=refresh_token,
            progress_callback=set_progress
        )
        
        if "error" in result:
            sp.status = "error"
            sp.error_message = result["error"]
            sp.save(update_fields=["status", "error_message", "updated_date"])
            return {"error": result["error"]}
            
        sp.status = "done"
        if "download_url" in result:
            sp.geonode_url = result["download_url"]
            
        sp.save(update_fields=["status", "geonode_url", "updated_date"])
        
        return {"geonode_url": sp.geonode_url}
        
    except Exception as e:
        sp.status = "error"
        sp.error_message = str(e)
        sp.save(update_fields=["status", "error_message", "updated_date"])
        return {"error": str(e)}
