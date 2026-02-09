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
from .prompt_question import BASE_SYSTEM_PROMPT_JSON
from .prompt_keys import BASE_SYSTEM_PROMPT_KEYS
from .prompt_semantico import BASE_SYSTEM_PROMPT_SEMANTICO
from typing import List, Optional, Any
from django.db import connection
from django.conf import settings
import os
import logging
from .utils_json_search import search_in_json_files

logger = logging.getLogger(__name__)

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
            logger.warning(f"No se pudo generar embedding para la consulta: {query[:100]}...")
            return []

        # Detectar idioma de la consulta
        query_language = embedder.detect_language(query)
        logger.debug(f"Consulta detectada en idioma: {query_language}")

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
                logger.debug(f"Usando chunks en {query_language}")
                relevant_chunks = language_chunks
            else:
                logger.debug(f"No hay chunks en {query_language}, usando todos los idiomas")

        # Obtener top chunks ordenados por similitud
        top_chunks = list(relevant_chunks.order_by('-similarity')[:top_k])

        # Filtrar chunks con similitud muy baja (umbral mínimo)
        # filtered_chunks = [chunk for chunk in top_chunks if chunk.similarity > 0.3]

        # logger.debug(f"RAG search: {len(filtered_chunks)} chunks encontrados para query en {query_language}")
        # logger.debug(f"Similitudes: {[round(chunk.similarity, 3) for chunk in filtered_chunks[:5]]}")

        return top_chunks[:min(20, len(top_chunks))]  # Limitar a 20 mejores resultados

    except Exception as e:
        logger.error(f"Error en optimized_rag_search: {str(e)}")
        return []


def generate_insight_prompt(user_query: str, rows_serializable: List[Any], sample_limit: int = 15) -> str:
    """
    Genera el prompt para el analista de datos basado en los resultados obtenidos.
    """
    row_count = len(rows_serializable)
    
    if row_count > 0:
        sample_rows = rows_serializable[:sample_limit]
        
        insight_prompt = (
            "Eres un analista de datos que genera DESCRIPCIONES FACTUALES.\n\n"

            "REGLAS ABSOLUTAS:\n"
            "- Usa ÚNICAMENTE la información contenida en los resultados.\n"
            "- NO agregues conocimiento externo.\n"
            "- NO infieras causas, consecuencias ni intenciones.\n"
            "- NO evalúes si los datos son suficientes.\n"
            "- NO respondas la pregunta con opinión.\n\n"

            "COMPORTAMIENTO:\n"
            "- Si hay al menos un registro, describe brevemente lo que se observa.\n"
            "- Si no hay registros, responde EXACTAMENTE:\n"
            "'No tengo información suficiente sobre ese tema particular en los documentos disponibles.'\n\n"

            f"Pregunta del usuario (solo como contexto, NO para inferir):\n"
            f"{user_query}\n\n"

            f"Resultados obtenidos ({row_count} filas):\n"
            f"Muestra de datos:\n{sample_rows}\n\n"

            "Responde en español.\n"
            "Devuelve SOLO el texto final."
        )
    else:
        insight_prompt = (
            "Eres un analista de datos experto. Tu tarea es generar un resumen estrictamente basado en los resultados obtenidos del sistema.\n\n"
            "INSTRUCCIONES ESTRICTAS:\n"
            "- Si existe al menos un dato en la muestra, debes generar un resumen basado únicamente en ese contenido, aunque sea un solo registro.\n"
            "- No evalúes si la cantidad de datos es suficiente para responder la pregunta; simplemente reporta lo que hay.\n\n"
            "Formato esperado:\n"
            "Si hay datos, genera un breve resumen estructurado describiendo lo que se observa directamente en los resultados.\n"
            "Si la muestra de datos está vacía, responde exactamente: 'No tengo información suficiente sobre ese tema particular en los documentos disponibles.'\n\n"
            f"Pregunta del usuario: {user_query}\n"
            f"Resultados obtenidos (0 filas):\n"
            "Muestra de datos: []\n\n"
            "Responde en español."
        )
            
    return f"Eres un analista de datos experto: {insight_prompt}"

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
    server = settings.OLLAMA_API_URL
    payload = request.data

    model = payload["model"]
    REASONING_MODEL = payload["model"]
    logger.info(f"modelo: {model}")

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

            # =================== RAG OPTIMIZADO + HYBRID + JSON ===================
            if payload['type'] == 'RAG':
                context = Context.objects.get(id=payload['context_id'])
                query = payload["messages"][1]["content"]

                logger.debug(f"Iniciando búsqueda RAG para: {query[:100]}...")

                # Detect file types
                files_json_count = context.files.filter(document_type='application/json').count()
                total_files = context.files.count()
                files_text_count = total_files - files_json_count
                
                logger.debug(f"File stats - Total: {total_files}, JSON: {files_json_count}, Text: {files_text_count}")

                rag_context = ""
                json_context_content = ""
                
                # 1. RAG Search (for Text files, or strict fallback)
                if files_text_count > 0 or files_json_count == 0:
                    relevant_chunks = optimized_rag_search(
                        context_id=context.id,
                        query=query,
                        top_k=30
                    )

                    if relevant_chunks:
                        docs_context = {}
                        for chunk in relevant_chunks:
                            doc_name = chunk.file.filename
                            if doc_name not in docs_context:
                                docs_context[doc_name] = []
                            docs_context[doc_name].append({
                                'text': chunk.text[:800],
                                'similarity': chunk.similarity
                            })

                        rag_context = "Contexto relevante de los documentos:\n\n"
                        for doc_name, chunks in docs_context.items():
                            chunks.sort(key=lambda x: x['similarity'], reverse=True)
                            rag_context += f"📄 **{doc_name}**:\n"
                            for i, chunk_data in enumerate(chunks[:3]):
                                rag_context += f"- {chunk_data['text']}\n"
                            rag_context += "\n"
                            
                        logger.debug(f"Documentos RAG utilizados: {list(docs_context.keys())}")
                    else:
                        logger.warning("No se encontraron chunks relevantes para la consulta RAG")

                # 2. JSON Search (SQL)
                if files_json_count > 0:
                    logger.debug("Ejecutando búsqueda JSON...")
                    print("Ejecutando búsqueda JSON...", flush=True)
                    
                    rows_serializable = search_in_json_files(context, query, REASONING_MODEL, server)
                    if rows_serializable:
                        json_context_content = generate_insight_prompt(query, rows_serializable)
                
                
                print("Ejecutando búsqueda JSON...",json_context_content , flush=True)
                #print("Ejecutando búsqueda JSON...",rag_context , flush=True)
                # 3. Combine and Set Prompt
                if rag_context and json_context_content:
                    # HYBRID MODE
                    print("Modo Híbrido Activado (RAG + JSON)", flush=True)
                    logger.info("Modo Híbrido Activado (RAG + JSON)")
                    system_prompt = f"""Eres un asistente avanzado capaz de analizar múltiples fuentes de información.
Tienes acceso tanto a documentos de texto (PDF, DOCX) como a datos estructurados (JSON/SQL).

=== FUENTE 1: DATOS ESTRUCTURADOS ===
{json_context_content}

=== FUENTE 2: DOCUMENTOS DE TEXTO ===
{rag_context}

INSTRUCCIONES COMBINADAS:
1. Integra la información de ambas fuentes para dar una respuesta completa.
2. Si los datos estructurados (Fuente 1) contienen cifras precisas, úsalas como autoridad principal para números.
3. Si los documentos de texto (Fuente 2) contienen explicaciones o contexto cualitativo, úsalos para enriquecer la respuesta.
4. Si hay contradicciones explícitas, menciona qué dice cada fuente.
5. Responde SIEMPRE en español y mantén un tono profesional.
"""
                    updated_payload["messages"].insert(0, {
                        "role": "system",
                        "content": system_prompt
                    })
                    
                elif json_context_content:
                    # JSON ONLY MODE
                    logger.info("Modo JSON Only Activado")
                    updated_payload["messages"].insert(0, {
                        "role": "system",
                        "content": json_context_content
                    })
                    
                elif rag_context:
                    # RAG ONLY MODE
                    logger.info("Modo RAG Only Activado")
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
                
                else:
                    # NO INFO FOUND
                    logger.info("No se encontró información en ninguna fuente")
                    updated_payload["messages"].insert(0, {
                        "role": "system",
                        "content": "Eres un asistente amable. El usuario ha hecho una pregunta pero no tengo información específica en los documentos para responderla. Responde amablemente que no tienes información suficiente sobre ese tema específico en los documentos disponibles."
                    })

            # =================== LLAMADA A OLLAMA ===================
            logger.debug(f"Enviando {len(updated_payload['messages'])} mensajes a Ollama")
            #logger.debug(f"datA!!!! {updated_payload}")
            with requests.post(
                    f"{server}/api/chat",
                    json=updated_payload,
                    headers={"Content-Type": "application/json"},
                    timeout=int(os.environ.get("OLLAMA_TIMEOUT", 600)),
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
                    logger.info("Cache automáticamente limpiado durante conversación")

        except Exception as e:
            logger.error(f"Error en chat: {str(e)}")
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
            timeout = int(os.environ.get("OLLAMA_TIMEOUT", 600)),
        )
        response.raise_for_status()
        title_data = response.json()
        title = title_data["message"]["content"].strip()
        print(title, flush=True)
        return title[:255]  # Limita a 255 caracteres por seguridad

    except Exception as e:
        print(f"[ERROR] Error generando título del chat: {str(e)}", flush=True)
        return None