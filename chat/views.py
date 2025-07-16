from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

import threading
import requests

llm_lock: threading.Lock = threading.Lock()

@api_view(['GET','POST'])
@csrf_exempt
def chat(request):
    server = "http://host.docker.internal:11434"
    payload = request.data
    updated_payload = {
        **payload,
        "stream": False,
        "format": "json",
        # "options": { 
        #     "temperature": 0.1, 
        #     "seed": 42,        
        #     "top_p": 0.9,
        #     "num_ctx": 4096,
        #     "repeat_penalty": 1.1
        # }
    }
    
    acquired = llm_lock.acquire(blocking=False)
    if not acquired:
        return JsonResponse({
            "error": "Servicio ocupado, intenta más tarde"
        }, status=503)

    
    try:
        #resp = requests.post(f"{server}/v1/chat/completions", json=updated_payload, timeout=500)
        resp = requests.post(f"{server}/api/chat", json=updated_payload, timeout=500)
        resp.raise_for_status()
        data = resp.json()
        return JsonResponse(data)
    except requests.exceptions.Timeout:
        return JsonResponse({"error": "Timeout contactando Ollama"}, status=504)
    except requests.exceptions.RequestException as e:
        return JsonResponse({"error": f"Error en solicitud a Ollama: {str(e)}"}, status=502)
    except Exception as e:
        return JsonResponse({"error": f"Error inesperado: {str(e)}"}, status=500)
    finally:
        llm_lock.release()