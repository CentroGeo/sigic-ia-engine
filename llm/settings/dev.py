from .base import *
import os

DEBUG = True
ALLOWED_HOSTS = ["*",'172.17.0.1','localhost','llm_backend']  

print("DEBUG: Using settings module:", __name__)

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('DB_NAME', 'llm'),
        'USER': os.environ.get('DB_USER', 'postgres'),
        'PASSWORD': os.environ.get('DB_PASSWORD','postgres'),
        'HOST': os.environ.get('DB_HOST','db'),
        'PORT': os.environ.get('DB_PORT','5432'),
    }
}