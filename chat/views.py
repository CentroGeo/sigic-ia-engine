from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.http import StreamingHttpResponse
import time
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
        "stream": True,
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

    
    def event_stream():
        try:
        
            with requests.post(f"{server}/api/chat", json=updated_payload,  headers={"Content-Type": "application/json"}, timeout=500, stream=True) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines(decode_unicode=True):
                    print(f"[DEBUG] tipo de line: {type(line)} - contenido: {repr(line)}")
                    yield f"{line}\n"
                    time.sleep(0.2)
        finally:
            llm_lock.release()
            
                
    return StreamingHttpResponse(event_stream(), content_type='text/event-stream')