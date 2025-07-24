from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.core.serializers import serialize
from rest_framework import status
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.http import StreamingHttpResponse
from django.core.serializers.json import DjangoJSONEncoder
from chat.models import History
import time
import threading
import requests
import json

llm_lock: threading.Lock = threading.Lock()
@api_view(['GET','POST'])   
@csrf_exempt
def chat(request):
    server = "http://host.docker.internal:11434"
    payload = request.data
    updated_payload = {
        **payload,
        "stream": True,
        #"format": "json",
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

    
    def event_stream(payload):
        try:
            new_messages = [payload["messages"][1]]
            llm_response = {"role": "user", "content": ''}
            
            with requests.post(f"{server}/api/chat", json=updated_payload,  headers={"Content-Type": "application/json"}, timeout=500, stream=True) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines(decode_unicode=True):
                    print(f"[DEBUG] tipo de line: {type(line)} - contenido: {repr(line)}")
                    yield f"{line}\n"
                    line_json = json.loads(line.decode("utf-8"))
                    llm_response["content"] += str(line_json['message']["content"])
                    time.sleep(0.2)

                new_messages.append(llm_response)
                
                if payload['type'] == 'Preguntar':
                    update_history                  = History.objects.get(id=payload['chat_id'])
                    
                    if(update_history.history_array == None):
                        update_history.history_array = []
                        
                    update_history.history_array    = update_history.history_array + new_messages
                    update_history.job_status       = "Finalizado"
                    update_history.save()
        except Exception as e:
            print("[DEBUG] error: " + str(e))
            update_history                  = History.objects.get(id=payload['chat_id'])    
            update_history.job_status       = "Error"
            update_history.save()
            
            return JsonResponse({"error": str(e)}, status=500)
        finally:
            llm_lock.release()
            
                
    return StreamingHttpResponse(event_stream(payload), content_type='text/event-stream')


@api_view(['GET','POST'])   
@csrf_exempt
def historyGenerate(request):
    try:
        if request.method == 'POST':
            payload = request.data
            response_model = {
                "status": "ok"
            }   
            
            if(payload['chat_id'] == 0):
                print("[DEBUG] nuevo chat")
                new_history                  = History()
                new_history.user_id          = payload['user_id']
                new_history.job_id           = payload['session_id']
                new_history.job_status       = "Iniciado"
                new_history.save()
                
                response_model['chat_id']    = new_history.id
            else:
                print("[DEBUG] continuación de chat")
                update_history                  = History.objects.get(id=payload['chat_id'])
                update_history.user_id          = payload['user_id']
                update_history.job_id           = payload['session_id']
                update_history.job_status       = "Iniciado"
                update_history.save()
                
                response_model['chat_id']    = update_history.id
                
            return JsonResponse(response_model, status=200)
        else:
            return JsonResponse({"error": "Metodo no permitido"}, status=405)
    except Exception as e:
        print("[DEBUG] error: " + str(e))
        return JsonResponse({"error": str(e)}, status=500)

@api_view(['GET','POST'])   
@csrf_exempt
def historyUser(request):
    try:
        if request.method == 'POST':
            payload = request.data
            get_history  = History.objects.get(id=payload['chat_id'])
            
            serialized = serialize('json', [get_history])
            data = json.loads(serialized)[0]['fields']
            return JsonResponse(data, status=200)
        else:
            return JsonResponse({"error": "Metodo no permitido"}, status=405)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)
    