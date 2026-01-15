"""
Configuración de Celery para el proyecto Django
"""
import os
from celery import Celery

# Establecer el módulo de settings de Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'llm.settings.dev')

# Crear la aplicación Celery
app = Celery('llm')

# Cargar la configuración desde Django settings con el namespace 'CELERY'
# Esto significa que todas las configuraciones de Celery deben tener el prefijo CELERY_
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-descubrir tareas desde todas las apps instaladas
# Esto busca archivos tasks.py en cada app
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Tarea de debug para verificar que Celery funciona"""
    print(f'Request: {self.request!r}')