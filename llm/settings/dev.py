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

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'shared.authentication.KeycloakAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
}

#BASE_DIR = Path(__file__).resolve().parent.parent.parent  
#MEDIA_URL = '/media/'
#MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
