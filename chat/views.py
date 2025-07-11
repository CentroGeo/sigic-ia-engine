from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.http import JsonResponse
import threading
import requests

llm_lock: threading.Lock = threading.Lock()

@api_view(['GET','POST'])
def chat(request):
    server = "http://172.17.0.1:11434"
    
    payload = {
        "model": "deepseek-r1",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Haz un resumen de una noticia"}
        ],
        "think": False,
        "stream": False,
    }
    
    acquired = llm_lock.acquire(blocking=False)
    if not acquired:
        return JsonResponse({
            "error": "Servicio ocupado, intenta más tarde"
        }, status=503)

    
    try:
        resp = requests.post(f"{server}/v1/chat/completions", json=payload, timeout=500)
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