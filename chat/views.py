# En chat/views.py - Versión corregida sin imports circulares

from rest_framework.decorators import api_view
from rest_framework.response import Response
from django.core.serializers import serialize
from rest_framework import status
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.http import StreamingHttpResponse
from django.core.serializers.json import DjangoJSONEncoder
from fileuploads.models import Workspace, Context, Files, DocumentEmbedding
from fileuploads.embeddings_service import embedder
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from pgvector.django import L2Distance
from .serializers import HistoryMiniSerializer
from .models import History
from django.db import transaction
import time
import threading
import requests
import json
import os
from .prompts import BASE_SYSTEM_PROMPT_JSON
from .prompts_init_aux import BASE_SYSTEM_PROMPT_KEYS
from typing import List
from django.db import connection

llm_lock: threading.Lock = threading.Lock()
ollama_server = os.environ.get('ollama_server', 'http://host.docker.internal:11434')


def optimized_rag_search(context_id: int, query: str, top_k: int = 50) -> List[DocumentEmbedding]:
    """
    Búsqueda RAG optimizada con mejor ranking y filtrado
    """
    try:
        # Generar embedding de la consulta
        query_embedding = embedder.embed_query(query)

        if query_embedding is None or len(query_embedding) == 0:
            print(f"[WARNING] No se pudo generar embedding para la consulta: {query[:100]}...")
            return []

        # Detectar idioma de la consulta
        query_language = embedder.detect_language(query)
        print(f"[DEBUG] Consulta detectada en idioma: {query_language}")

        # Buscar chunks relevantes con filtros mejorados
        relevant_chunks = DocumentEmbedding.objects.filter(
            file__contexts__id=context_id
        ).annotate(
            similarity=1 - L2Distance('embedding', query_embedding)
        )

        # Filtrar por idioma si coincide (con fallback)
        if query_language in ['es', 'en', 'fr']:
            language_chunks = relevant_chunks.filter(language=query_language)
            if language_chunks.exists():
                print(f"[DEBUG] Usando chunks en {query_language}")
                relevant_chunks = language_chunks
            else:
                print(f"[DEBUG] No hay chunks en {query_language}, usando todos los idiomas")

        # Obtener top chunks ordenados por similitud
        top_chunks = list(relevant_chunks.order_by('-similarity')[:top_k])

        # Filtrar chunks con similitud muy baja (umbral mínimo)
        # filtered_chunks = [chunk for chunk in top_chunks if chunk.similarity > 0.3]

        # print(f"[DEBUG] RAG search: {len(filtered_chunks)} chunks encontrados para query en {query_language}")
        # print(f"[DEBUG] Similitudes: {[round(chunk.similarity, 3) for chunk in filtered_chunks[:5]]}")

        return top_chunks[:min(20, len(top_chunks))]  # Limitar a 20 mejores resultados

    except Exception as e:
        print(f"[ERROR] Error en optimized_rag_search: {str(e)}")
        return []


@extend_schema(
    methods=["POST"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "success": {"type": "boolean"},
                "context": {"type": "object"},
                "files": {"type": "array", "items": {"type": "object"}},
            },
        }
    },
    summary="Chat (POST)",
    description="Chat (POST).",
    tags=["Chat"],
)
@api_view(["POST"])
def chat(request):
    #server = "http://host.docker.internal:11434"
    server = ollama_server
    payload = request.data

    model = payload["model"]
    print("modelo: ", model, flush=True)

    # Validaciones requeridas
    if 'type' not in payload or payload['type'] not in ['Preguntar', 'RAG']:
        return JsonResponse({"error": "El parámetro 'type' debe ser 'Preguntar' o 'RAG'"}, status=400)

    if payload['type'] == 'RAG' and 'context_id' not in payload:
        return JsonResponse({"error": "Se requiere context_id para tipo RAG"}, status=400)

    # Configuración para Ollama
    updated_payload = {
        **payload,
        "stream": True,
    }

    # Adquirir lock para evitar sobrecarga
    acquired = llm_lock.acquire(blocking=False)
    if not acquired:
        return JsonResponse({"error": "Servicio ocupado, intenta más tarde"}, status=503)

    def event_stream(payload):
        try:
            # Recuperar historial previo desde la base de datos
            history_obj = History.objects.get(id=payload['chat_id'])
            history_array = history_obj.history_array or []
            
            if(len(history_array) > 0):
                history_array = history_array[:2]

            # Agregar el nuevo mensaje del usuario al final del historial
            history_array.append(payload["messages"][1])

            # Usar el historial completo como new_messages
            new_messages = history_array.copy()

            # Actualizar el payload para Ollama
            updated_payload["messages"] = new_messages

            llm_response = {"role": "assistant", "content": ''}
            relevant_docs = []

            # =================== RAG OPTIMIZADO ===================
            if payload['type'] == 'RAG':
                context = Context.objects.get(id=payload['context_id'])
                query = payload["messages"][1]["content"]

                print(f"[DEBUG] Iniciando búsqueda RAG para: {query[:100]}...")

                files_json = context.files.filter(document_type='application/json').count()
                print(f"[DEBUG] files_json: {files_json}", flush=True)
                if(files_json == 0):
                    # Usar la nueva función optimized_rag_search
                    relevant_chunks = optimized_rag_search(
                        context_id=context.id,
                        query=query,
                        top_k=30  # Reducido para mejor rendimiento
                    )

                    # Construir contexto RAG si hay chunks relevantes
                    if relevant_chunks:
                        # Agrupar chunks por documento para mejor contexto
                        docs_context = {}
                        for chunk in relevant_chunks:
                            doc_name = chunk.file.filename
                            if doc_name not in docs_context:
                                docs_context[doc_name] = []
                            docs_context[doc_name].append({
                                'text': chunk.text[:800],  # Limitar texto por chunk
                                'similarity': chunk.similarity
                            })

                        # Construir contexto optimizado
                        rag_context = "Contexto relevante de los documentos:\n\n"

                        for doc_name, chunks in docs_context.items():
                            # Ordenar chunks por similitud
                            chunks.sort(key=lambda x: x['similarity'], reverse=True)

                            rag_context += f"📄 **{doc_name}**:\n"
                            for i, chunk_data in enumerate(chunks[:3]):  # Max 3 chunks por documento
                                rag_context += f"- {chunk_data['text']}\n"
                            rag_context += "\n"

                        print(f"[DEBUG] RAG context construido: {len(rag_context)} caracteres")    
                        # Insertar contexto RAG en el sistema prompt
                        system_prompt = f"""Eres un asistente amable que puede ayudar al usuario. Responde de manera cordial y precisa basándote en el siguiente contexto de documentos.

    {rag_context}

    INSTRUCCIONES:
    - Responde SIEMPRE en español
    - Basa tu respuesta en el contexto proporcionado
    - Si la pregunta no puede responderse completamente con el contexto, menciona qué información tienes disponible
    - Cita los documentos relevantes cuando sea apropiado
    - Sé conciso pero completo en tu respuesta"""

                        updated_payload["messages"].insert(0, {
                            "role": "system",
                            "content": system_prompt
                        })

                        relevant_docs = list(docs_context.keys())
                        print(f"[DEBUG] Documentos utilizados: {relevant_docs}")

                    else:
                        print("[WARNING] No se encontraron chunks relevantes para la consulta RAG")
                        # Prompt para cuando no hay contexto
                        updated_payload["messages"].insert(0, {
                            "role": "system",
                            "content": "Eres un asistente amable. El usuario ha hecho una pregunta pero no tengo información específica en los documentos para responderla. Responde amablemente que no tienes información suficiente sobre ese tema específico en los documentos disponibles."
                        })

                else:
                    list_files_json = list(context.files.filter(document_type='application/json').values_list('id', flat=True))
                    
                    print("Lista de keys:", list_files_json,flush=True)
                    lista_de_keys = DocumentEmbedding.get_json_keys_with_types(list_files_json)
                    for row in lista_de_keys:
                        print(f"{row['key']}: {row['type']} ({row['count']} filas)", flush=True)
                    
                    llm_context = payload["messages"][1]["content"]
                    print("Lista de keys v3:",flush=True)
                    system_prompt_KEYS = BASE_SYSTEM_PROMPT_KEYS.format(schema=lista_de_keys, list_files_json=list_files_json)
                    
                    print("Lista de keys v2:",flush=True)
                    with open("prompt_keys.txt", "w", encoding="utf-8") as f:
                        f.write(system_prompt_KEYS)
                    
                    url = f"{server}/api/chat"
                    sql_payload = {
                        "model": "deepseek-r1",
                        "messages": [
                            {"role": "system", "content": system_prompt_KEYS},
                            {"role": "user", "content": llm_context},
                        ],
                        "stream": False,
                        "temperature": 0,
                        "think": False
                    }
                    
                    resp = requests.post(
                        url, 
                        json=sql_payload, 
                        headers={"Content-Type": "application/json"}, 
                        timeout=500
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    sql = data["message"]["content"]

                    print("Lista de keys v3:",flush=True)
                    with open("sql_keys.txt", "w", encoding="utf-8") as f:
                        f.write(sql)
                    
                    try:    
                        with connection.cursor() as cursor:
                            cursor.execute(sql)
                            rows = cursor.fetchall()
                    except Exception as e:
                        print(f"Error al ejecutar la consulta SQL: {e}", flush=True)    
                    
                    print("Lista de keys v4:",rows,flush=True)
                    rows = [k[0] for k in rows]
                    list_reduce_keys = []
                    for row_sql_keys in rows:
                        for row_all_keys in lista_de_keys:
                            if( 
                               len(row_sql_keys.split(".")) == len(row_all_keys['key'].replace(".array.", ".").split(".")) and 
                               row_sql_keys in row_all_keys['key'].replace(".array.", ".")
                            ):
                                print(f"if data", row_all_keys['key'],row_sql_keys ,  flush=True)
                                list_reduce_keys.append(row_all_keys)   
                    
                    
                    print("Lista de keys v5:", len(lista_de_keys), flush=True)
                    print("Lista de keys v5:", len(list_reduce_keys), flush=True)
                    for row in list_reduce_keys:
                        print(f"{row['key']}: {row['type']} ({row['count']} filas)", flush=True)
                    
                    if(len(list_reduce_keys) > 0):
                        system_prompt = BASE_SYSTEM_PROMPT_JSON.format(schema=list_reduce_keys, list_files_json=list_files_json)
                        llm_context = payload["messages"][1]["content"]
                                    
                        with open("prompt.txt", "w", encoding="utf-8") as f:
                            f.write(system_prompt)
                    
                        for interation in range(2): 
                            query_error = False
                            url = f"{server}/api/chat"
                            sql_payload = {
                                "model": "deepseek-r1",
                                "messages": [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": llm_context},
                                ],
                                "stream": False,
                                "temperature": 0,
                                "think": False
                            }
                            
                            resp = requests.post(
                                url, 
                                json=sql_payload, 
                                headers={"Content-Type": "application/json"}, 
                                timeout=500
                            )
                            resp.raise_for_status()
                            data = resp.json()
                            sql = data["message"]["content"]

                            with open("sql.txt", "w", encoding="utf-8") as f:
                                f.write(sql)
                                
                            rows = None
                            try:    
                                with connection.cursor() as cursor:
                                    cursor.execute(sql)
                                    rows = cursor.fetchall()
                                    break
                            except Exception as e:
                                query_error = True
                                print(f"Error al ejecutar la consulta SQL: {e}", flush=True)
                                llm_context = (
                                    f"Pregunta original: {payload["messages"][1]["content"]}\n"
                                    f"El SQL '{sql}' produjo el error: {str(e)}\n"
                                    "Corrige la consulta SQL."
                                )    
                                continue
                        
                        if(query_error):
                            return JsonResponse({"error": "Error al ejecutar la consulta SQL"}, status=500)
                        
                        rows_serializable = []
                        for row in rows:
                            serialized_row = []
                            for value in row:
                                if value is None:
                                    l = 0
                                else:
                                    # Convert all values to string for JSON serialization
                                    serialized_row.append(str(value))
                                    
                            if(len(serialized_row) > 0):
                                rows_serializable.append(serialized_row)

                        row_count = len(rows_serializable)

                        print("respuesta SQL", data["message"]["content"], flush=True)
                        print("respuesta SQL", rows_serializable, flush=True)

                        try:
                            if row_count > 0:
                                # Limit sample data for insight generation
                                #sample_rows = rows_serializable[:3]
                                sample_rows = rows_serializable[:15]
                                
                                insight_prompt = (
                                    "Eres un analista de datos experto. Tu tarea es generar un resumen estrictamente basado en los resultados obtenidos del sistema.\n\n"
                                    "INSTRUCCIONES ESTRICTAS:\n"
                                    "- Solo utiliza la información contenida en el campo 'Resultados obtenidos' o 'Muestra de datos'.\n"
                                    "- No agregues, inventes ni extrapoles información adicional.\n"
                                    "- No definas conceptos, no agregues contexto general ni explicaciones externas.\n"
                                    "- No uses conocimiento enciclopédico o general.\n"
                                    "- Usa siempre un tono analítico, directo y basado en los datos.\n\n"
                                    "Formato esperado:\n"
                                    "Si hay datos, genera un breve resumen estructurado describiendo lo que se observa directamente en los resultados.\n"
                                    "Si no hay datos, responde solo con la frase indicada.\n\n"
                                    f"Pregunta del usuario: {llm_context}\n"
                                    #f"Consulta SQL ejecutada: {sql}\n"
                                    f"Resultados obtenidos ({len(rows_serializable)} filas):\n"
                                    f"Muestra de datos: {sample_rows}\n\n"
                                    #"Proporciona un resumen breve y útil (máximo un parrafo) sobre estos resultados en español."
                                    "Proporciona un resumen sin omitir ninguna información de los Resultados obtenidos., Si no tienes muestra de datos o resultados obtenidos responde: 'No tengo información suficiente sobre ese tema particular en los documentos disponibles."
                                )
                                
                                system_prompt = f"Eres un analista de datos experto: {insight_prompt}"
                                print("if system_prompt", system_prompt, flush=True)
                                
                                with open("system_prompt.txt", "w", encoding="utf-8") as f:
                                    f.write(system_prompt)
                                    
                                #updated_payload["temperature"] = 0
                                updated_payload["messages"].insert(0, {
                                    "role": "system",
                                    "content": system_prompt
                                })
                            else:
                                insight_prompt = (
                                    "Eres un analista de datos experto. Tu tarea es generar un resumen estrictamente basado en los resultados obtenidos del sistema.\n\n"
                                    "INSTRUCCIONES ESTRICTAS:\n"
                                    "- Solo utiliza la información contenida en el campo 'Resultados obtenidos' o 'Muestra de datos'.\n"
                                    "- No agregues, inventes ni extrapoles información adicional.\n"
                                    
                                    "- No definas conceptos, no agregues contexto general ni explicaciones externas.\n"
                                    "- No uses conocimiento enciclopédico o general.\n"
                                    "- Usa siempre un tono analítico, directo y basado en los datos.\n\n"
                                    "Formato esperado:\n"
                                    "Si hay datos, genera un breve resumen estructurado describiendo lo que se observa directamente en los resultados.\n"
                                    "Si no hay datos, responde solo con la frase indicada.\n\n"
                                    f"Pregunta del usuario: {llm_context}\n"
                                    #f"Consulta SQL ejecutada: {sql}\n"
                                    f"Resultados obtenidos ({len(rows_serializable)} filas):\n"
                                    f"Muestra de datos: []\n\n"
                                    "responde que no se obtuvo respuesta en español."
                                )
                                
                                system_prompt = f"Eres un analista de datos experto: {insight_prompt}"
                                print("else system_prompt", system_prompt, flush=True)
                                
                                with open("system_prompt.txt", "w", encoding="utf-8") as f:
                                    f.write(system_prompt)
                                
                                updated_payload["messages"] = new_messages
                                #updated_payload["messages"] = []
                                #updated_payload["temperature"] = 0
                                updated_payload["messages"].insert(0, {
                                    "role": "system",
                                    "content": system_prompt
                                })
                        except Exception as e:
                            print(f"Error al ejecutar la consulta SQL: {e}", flush=True)
                            l = 0
                    else:
                        insight_prompt = (
                            "Eres un analista de datos experto. Tu tarea es generar un resumen estrictamente basado en los resultados obtenidos del sistema.\n\n"
                            "INSTRUCCIONES ESTRICTAS:\n"
                            "- Solo utiliza la información contenida en el campo 'Resultados obtenidos' o 'Muestra de datos'.\n"
                            "- No agregues, inventes ni extrapoles información adicional.\n"
                            "- No definas conceptos, no agregues contexto general ni explicaciones externas.\n"
                            "- No uses conocimiento enciclopédico o general.\n"
                            "- Usa siempre un tono analítico, directo y basado en los datos.\n\n"
                            "Formato esperado:\n"
                            "Si hay datos, genera un breve resumen estructurado describiendo lo que se observa directamente en los resultados.\n"
                            "Si no hay datos, responde solo con la frase indicada.\n\n"
                            f"Pregunta del usuario: {llm_context}\n"
                            #f"Consulta SQL ejecutada: {sql}\n"
                            f"Resultados obtenidos (0 filas):\n"
                            f"Muestra de datos: []\n\n"
                            "responde que no se obtuvo respuesta en español."
                        )
                        
                        system_prompt = f"Eres un analista de datos experto: {insight_prompt}"
                        #system_prompt = f"Eres un analista de datos experto: "
                        print("else system_prompt", system_prompt, flush=True)
                        
                        with open("system_prompt.txt", "w", encoding="utf-8") as f:
                            f.write(system_prompt)
                        
                        updated_payload["messages"] = new_messages
                        #updated_payload["temperature"] = 0
                        #updated_payload["messages"] = []
                        updated_payload["messages"].insert(0, {
                            "role": "system",
                            "content": system_prompt
                        })
                        
            # =================== LLAMADA A OLLAMA ===================
            print(f"[DEBUG] Enviando {len(updated_payload['messages'])} mensajes a Ollama")
            print("datA!!!!", updated_payload, flush=True)
            with requests.post(
                    f"{server}/api/chat",
                    json=updated_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=500,
                    stream=True
            ) as resp:
                resp.raise_for_status()

                for line in resp.iter_lines(decode_unicode=True):
                    yield f"{line}\n"
                    line_json = json.loads(line.decode("utf-8"))
                    llm_response["content"] += str(line_json['message']["content"])

                new_messages.append(llm_response)

                # =================== GUARDAR HISTORIAL ===================
            update_history = History.objects.get(id=payload['chat_id'])

            if update_history.history_array is None:
                update_history.history_array = []

            # Filtrar mensajes "system" antes de guardar
            cleaned_messages = [msg for msg in new_messages if msg.get("role") != "system"]
            update_history.history_array = cleaned_messages
            update_history.job_status = "Finalizado"

            # Generar título si es la primera interacción
            if update_history.title is None:
                first_question = cleaned_messages[0]["content"]
                first_answer = cleaned_messages[1]["content"]
                generated_title = generate_chat_title(server, first_question, first_answer, model)
                if generated_title:
                    update_history.title = generated_title

            update_history.save()

            # =================== LIMPIEZA DE CACHE ===================
            # Usar el método integrado del embedder para limpiar cache
            if len(new_messages) % 10 == 0:  # Cada 10 mensajes
                cache_cleaned = embedder.cleanup_cache()
                if cache_cleaned:
                    print("[INFO] Cache automáticamente limpiado durante conversación")

        except Exception as e:
            print(f"[ERROR] Error en chat: {str(e)}")
            update_history = History.objects.get(id=payload['chat_id'])
            update_history.job_status = "Error"
            update_history.save()

            return JsonResponse({"error": str(e)}, status=500)
        finally:
            llm_lock.release()

    return StreamingHttpResponse(event_stream(payload), content_type='text/event-stream')


# El resto de tus funciones permanecen igual...
@extend_schema(
    methods=["POST"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "chat_id": {"type": "integer"},
            },
        }
    },
    summary="Generar chat (POST)",
    description="Generar chat (POST).",
    tags=["Chat"],
)
@api_view(["POST"])
def historyGenerate(request):
    try:
        if request.method == 'POST':
            payload = request.data
            user_id = payload['user_id']
            response_model = {
                "status": "ok"
            }

            if (payload['chat_id'] == 0):
                print("[DEBUG] nuevo chat")
                new_history = History()
                new_history.user_id = user_id
                new_history.job_id = payload['session_id']
                new_history.job_status = "Iniciado"
                new_history.save()

                existing_context = Context.objects.get(id=payload['context_id'])
                new_history.context.add(existing_context)

                response_model['chat_id'] = new_history.id
            else:
                print("[DEBUG] continuación de chat")
                update_history = History.objects.get(id=payload['chat_id'])
                update_history.user_id = user_id
                update_history.job_id = payload['session_id']
                update_history.job_status = "Iniciado"
                update_history.save()

                response_model['chat_id'] = update_history.id

            return JsonResponse(response_model, status=200)
        else:
            return JsonResponse({"error": "Metodo no permitido"}, status=405)
    except Exception as e:
        print("[DEBUG] error: " + str(e))
        return JsonResponse({"error": str(e)}, status=500)


@extend_schema(
    methods=["POST"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "saved": {"type": "boolean"},
            },
        }
    },
    summary="Obtener chat (POST)",
    description="Obtener chat (POST).",
    tags=["Chat"],
)
@api_view(["POST"])
def historyUser(request):
    try:
        if request.method == 'POST':
            payload = request.data
            get_history = History.objects.get(id=payload['chat_id'])

            if(get_history.history_array is None):
                get_history.history_array = []
            
            serialized = serialize('json', [get_history])
            data = json.loads(serialized)[0]['fields']
            return JsonResponse(data, status=200)
        else:
            return JsonResponse({"error": "Metodo no permitido"}, status=405)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@extend_schema(
    methods=["POST"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "saved": {"type": "boolean"},
            },
        }
    },
    summary="Obtener chat (POST)",
    description="Obtener chat (POST).",
    tags=["Chat"],
)
@api_view(["POST"])
def get_chat_histories(request):
    try:
        if request.method == 'POST':
            payload = request.POST.copy()
        else:
            payload = request.GET.copy()
            
        user_id = payload.get('user_id')

        if user_id:
            histories = History.objects.filter(user_id=user_id)
        else:
            histories = History.objects.all()

        if not histories.exists():
            return Response([])
        
        histories = histories.prefetch_related('context__workspace').order_by('-credate_date')

        serializer = HistoryMiniSerializer(histories, many=True)
        return Response(serializer.data)
    
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)

@extend_schema(
    methods=["POST"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "saved": {"type": "boolean"},
            },
        }
    },
    summary="Obtener chat (POST)",
    description="Obtener chat (POST).",
    tags=["Chat"],
)
@api_view(["POST"])
@csrf_exempt
def historyTitle(request):
    try:
        answer = {
            "saved": False,
        }
        
        if request.method == 'POST':
            payload = request.data
            chat_id = payload['chat_id']
            title   = payload['title']
            
            get_history = History.objects.get(id=payload['chat_id'])
            get_history.title = title
            get_history.save()
            answer["saved"] = True
            return JsonResponse(answer, status=200)
        else:
            return JsonResponse({"error": "Metodo no permitido"}, status=405)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@extend_schema(
    methods=["DELETE"],
    responses={
        200: {
            "type": "object",
            "properties": {
                "saved": {"type": "boolean"},
            },
        }
    },
    summary="Eliminar chat (DELETE)",
    description="Elimina un chat (DELETE).",
    tags=["Chat"],
)
@api_view(["DELETE"])
@csrf_exempt
def historyRemove(request, chat_id):
    answer = {
        "saved": False,
    }
    
    try:
        with transaction.atomic():
            History.objects.filter(id=chat_id).delete()
            answer["saved"] = True
        return JsonResponse(answer, status=200)
    
    except Exception as e:
        print("Error al guardar: ",str(e))
        return JsonResponse({"status": "error", "message": str(e)}, status=400)



def generate_chat_title(server_url: str, question: str, answer: str, model_name: str) -> str:
    """
    Genera un título breve (máximo 6 palabras) a partir de la primera pregunta y respuesta.
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