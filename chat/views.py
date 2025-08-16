from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.core.serializers import serialize
from rest_framework import status
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.http import StreamingHttpResponse
from django.core.serializers.json import DjangoJSONEncoder
from fileuploads.models import Workspace, Context, Files, DocumentEmbedding
from fileuploads.embeddings_service  import embedder
from pgvector.django import L2Distance
from .serializers import HistoryMiniSerializer
from .models import History
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

    model= payload["model"]
    print("modelo: ",model, flush=True)

    # Validaciones requeridas
    if 'type' not in payload or payload['type'] not in ['Preguntar', 'RAG']:
        return JsonResponse({"error": "El parámetro 'type' debe ser 'Preguntar' o 'RAG'"}, status=400)    


    if payload['type'] == 'RAG' and 'context_id' not in payload:
        return JsonResponse({"error": "Se requiere context_id para tipo RAG"}, status=400)

    # Configuración para Ollama
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
    
    # Adquirir lock para evitar sobrecarga
    acquired = llm_lock.acquire(blocking=False)
    if not acquired:
        return JsonResponse({"error": "Servicio ocupado, intenta más tarde"}, status=503)

    
    def event_stream(payload):
        try:
            #new_messages = [payload["messages"][-1]]  # Último mensaje del usuario
            #new_messages = [payload["messages"][1]]

            # Recuperar historial previo desde la base de datos
            history_obj = History.objects.get(id=payload['chat_id'])
            history_array = history_obj.history_array or []

            # Agregar el nuevo mensaje del usuario al final del historial
            history_array.append(payload["messages"][1])

            # Usar el historial completo como new_messages
            new_messages = history_array.copy()

            #TODO: limitar el historial conversacional a N mensajes
            #MAX_MESSAGES = 30
            #if len(new_messages) > MAX_MESSAGES:
            #    new_messages = new_messages[-MAX_MESSAGES:]


            # Actualizar el payload para Ollama
            updated_payload["messages"] = new_messages


            llm_response = {"role": "assistant", "content": ''}
            relevant_docs = []


            #Procesamiento para RAG
            if payload['type'] == 'RAG':
                context = Context.objects.get(id=payload['context_id'])
                query = payload["messages"][1]["content"]
                
                # 1. Generar embedding de la consulta
                query_embedding = embedder.embed_query(query)
                
                # 2. Buscar chunks relevantes en los archivos del contexto
                relevant_chunks = DocumentEmbedding.objects.filter(
                    file__contexts__id=context.id
                ).annotate(
                    similarity=1 - L2Distance('embedding', query_embedding)
                ).order_by('-similarity')[:50]  # Top N chunks más relevantes
                
                # 3. Construir contexto RAG
                if relevant_chunks:
                    rag_context = "Contexto relevante:\n"
                    rag_context += "\n---\n".join([
                        f"Documento: {chunk.file.filename}\nContenido: {chunk.text[:1000]}"
                        for chunk in relevant_chunks
                    ])
                    #print(f"[DEBUG] rag_context: {rag_context}")
                    
                    # 4. Modificar el prompt para Ollama
                    updated_payload["messages"].insert(0, {
                        "role": "system",
                        "content": f"""Eres un asistente amable que puede ayudar al usuario. Responde de manera cordial. Responde siempre en español. Responde consideando en el siguiente contexto:
                        {rag_context}
                        Si la pregunta no puede responderse con el contexto, di amablemente que no tienes información suficiente."""
                    })
                    
                    relevant_docs = list({chunk.file.filename for chunk in relevant_chunks})
            
            # Conexión con Ollama
            print(updated_payload)
            with requests.post(
                f"{server}/api/chat",
                json=updated_payload,
                headers={"Content-Type": "application/json"},
                timeout=500,
                stream=True
            ) as resp:
                resp.raise_for_status()
                
                for line in resp.iter_lines(decode_unicode=True):
                    # if line:
                    #     yield f"data: {line}\n"
                    #     #line_json = json.loads(line.decode("utf-8"))
                    #     line_json = json.loads(line)
                    #     llm_response["content"] += str(line_json['message']["content"])
                    #print(f"[DEBUG] tipo de line: {type(line)} - contenido: {repr(line)}")
                    yield f"{line}\n"
                    line_json = json.loads(line.decode("utf-8"))
                    llm_response["content"] += str(line_json['message']["content"])
                    #time.sleep(0.2)      
                new_messages.append(llm_response)              
            
            # Guardar en el historial
            update_history = History.objects.get(id=payload['chat_id'])
            
            if update_history.history_array is None: #es nuevochat
                update_history.history_array = []
                
            #update_history.history_array    = update_history.history_array + new_messages
            #update_history.history_array.extend([new_messages[0], llm_response])
            #update_history.history_array = new_messages + [llm_response]

            # Filtrar mensajes "system" antes de guardar
            cleaned_messages = [msg for msg in new_messages if msg.get("role") != "system"]
            #update_history.history_array = cleaned_messages + [llm_response]
            update_history.history_array = cleaned_messages

            update_history.job_status = "Finalizado"

            # Generar título basado en la primera pregunta y respuesta
            # 7. Generar título si es la primera interacción y aún no hay título
            print("cleaned_messages length: ",len(cleaned_messages), flush=True)
            print("cleaned_messages:", flush=True)
            print(update_history.title, flush=True)
            print(cleaned_messages, flush=True)
            #if update_history.title is None and len(cleaned_messages) == 2:
            if update_history.title is None:
                first_question = cleaned_messages[0]["content"]
                first_answer = cleaned_messages[1]["content"]
                #first_answer = llm_response["content"]
                generated_title = generate_chat_title(server, first_question, first_answer, model)
                if generated_title:
                    update_history.title = generated_title              


            
            # Guardar metadatos RAG si aplica
            # if payload['type'] == 'RAG':
            #     update_history.rag_metadata = {
            #         "context_id": payload['context_id'],
            #         "context_title": context.title,
            #         "documents_used": relevant_docs,
            #         "query": query
            #     }
            
            update_history.save()

            
            # with requests.post(f"{server}/api/chat", json=updated_payload,  headers={"Content-Type": "application/json"}, timeout=500, stream=True) as resp:
            #     resp.raise_for_status()
            #     for line in resp.iter_lines(decode_unicode=True):
            #         print(f"[DEBUG] tipo de line: {type(line)} - contenido: {repr(line)}")
            #         yield f"{line}\n"
            #         line_json = json.loads(line.decode("utf-8"))
            #         llm_response["content"] += str(line_json['message']["content"])
            #         time.sleep(0.2)

            #     new_messages.append(llm_response)
                
            #     if payload['type'] == 'Preguntar':
            #         update_history                  = History.objects.get(id=payload['chat_id'])
                    
            #         if(update_history.history_array == None):
            #             update_history.history_array = []
                        
            #         update_history.history_array    = update_history.history_array + new_messages
            #         update_history.job_status       = "Finalizado"
            #         update_history.save()



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

                existing_context = Context.objects.get(id=payload['context_id'])
                new_history.context.add(existing_context)
                
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



@api_view(['GET','POST'])   
@csrf_exempt
def get_chat_histories(request):
    #devuelva la lista de chats, si se manda el user_id, lo filtra, si no, trae todas
    user_id = request.GET.get('user_id')

    if user_id:
        histories = History.objects.filter(user_id=user_id)
    else:
        histories = History.objects.all()

    histories = histories.prefetch_related('context__workspace').order_by('-credate_date')

    serializer = HistoryMiniSerializer(histories, many=True)
    return Response(serializer.data)



def generate_chat_title(server_url: str, question: str, answer: str, model_name: str ) -> str:
    """
    Genera un título breve (máximo 6 palabras) a partir de la primera pregunta y respuesta.
    
    Args:
        server_url (str): URL base del servidor Ollama (ej: "http://host.docker.internal:11434").
        question (str): Primera pregunta del usuario.
        answer (str): Primera respuesta del modelo.

    Returns:
        str: Título generado (máximo 255 caracteres). Si hay error, devuelve None.
    """
    try:
        print("generando título para el chat...", flush=True)
        prompt = [
            {
                "role": "system",
                "content": "Genera un título muy corto (máximo 6 palabras) o una frase corta que resuma esta conversación."
            },
            {
                "role": "user",
                "content": f"Pregunta: {question}\nRespuesta: {answer}"
            }
        ]
        print(prompt, flush=True)
        payload = {
            "model": model_name,
            "messages": prompt,
            "stream": False,
            "think": False
        }

        response = requests.post(
            f"{server_url}/api/chat",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        response.raise_for_status()
        title_data = response.json()
        title = title_data["message"]["content"].strip()
        print(title, flush=True)
        return title[:255]  # Limita a 255 caracteres por seguridad

    except Exception as e:
        print(f"[ERROR] Error generando título del chat: {str(e)}", flush=True)
        return None