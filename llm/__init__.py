# Importar Celery para asegurar que se carga cuando Django inicia
from .celery import app as celery_app

__all__ = ('celery_app',)