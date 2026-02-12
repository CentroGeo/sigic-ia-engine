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


def generate_insight_prompt(user_query: str, rows_serializable: List[Any], 
                           sample_limit: int = 15, hybrid_mode: bool = False) -> str:
    
    row_count = len(rows_serializable)
    sample_rows = []
    
    for row in rows_serializable[:sample_limit]:
        formatted_row = []
        for item in row:
            try:
                if isinstance(item, str) and (item.startswith('{') or item.startswith('[')):
                    parsed_item = json.loads(item)
                    formatted_row.append(json.dumps(parsed_item, indent=2, ensure_ascii=False))
                else:
                    formatted_row.append(item)
            except:
                formatted_row.append(item)
        sample_rows.append(formatted_row)
    
    data_samples_str = ""
    for i, row in enumerate(sample_rows):
        data_samples_str += f"--- REGISTRO {i+1} ---\n"
        for item in row:
            data_samples_str += f"{item}\n"
        data_samples_str += "\n"

    if hybrid_mode:
        if row_count > 0:
            return f"""Resultados de búsqueda en datos estructurados ({row_count} registros encontrados)
Muestra de datos:
{data_samples_str}
"""
        else:
            return "No se encontraron datos estructurados para esta consulta."
    
    # MODO JSON ONLY: Retornar prompt completo con instrucciones
    if row_count > 0:
        insight_prompt = (
            "Eres un analista de datos experto que genera reportes técnicos ESTRICTOS y FACTUALES.\n\n"

            "REGLAS DE ORO (INCUMPLIMIENTO = FALLO CRÍTICO):\n"
            "- Prohibido saludar, despedirse o usar frases de cortesía.\n"
            "- Prohibido agregar conocimiento externo o sugerencias fuera de los datos.\n"
            "- Usa ÚNICAMENTE la información contenida en los resultados proporcionados.\n"
            "- SEMÁNTICA: El usuario puede usar términos generales como 'libro', 'documento' o 'archivo'. Debes mapear estos términos a los registros proporcionados (ej: 'Articulo', 'DIFUSION', 'Memorias', 'Tesis', etc.).\n"
            "- TEMA: Si el título, revista, autores o cualquier atributo tiene una relación razonable con el tema (ej: 'bioenergía' es relevante para 'energía renovable'), DEBES incluirlo.\n"
            "- NO evalúes si los datos son 'suficientes' para una conclusión científica compleja; simplemente reporta lo que los datos dicen sobre el tema de la pregunta.\n"
            "- FALLBACK: Solo si NINGUNO de los registros tiene relación alguna con la pregunta, responde EXACTAMENTE: 'No tengo información suficiente sobre ese tema particular en los documentos disponibles.'\n"
            "- NO infieras causas, consecuencias ni intenciones.\n"
            "- NO respondas con opiniones.\n\n"

            "COMPORTAMIENTO ESPERADO:\n"
            "- Analiza cuidadosamente cada registro en la 'Muestra de datos' (abajo).\n"
            "- Si un registro es relevante, descríbelo brevemente destacando los campos clave (año, título, etc.).\n\n"

            f"Pregunta del usuario (contexto de relevancia):\n"
            f"{user_query}\n\n"

            f"Resultados obtenidos ({row_count} filas):\n"
            f"Muestra de datos:\n{data_samples_str}\n\n"

            "Responde en español.\n"
            "Devuelve SOLO el texto final del reporte, sin introducciones ni comentarios adicionales."
        )
    else:
        insight_prompt = (
            "Eres un analista de datos experto. Si no hay resultados obtenidos, responde siguiendo la regla de fallback.\n\n"
            f"Pregunta del usuario: {user_query}\n"
            "Resultados obtenidos (0 filas):\n"
            "Muestra de datos: []\n\n"
            "Responde EXACTAMENTE: 'No tengo información suficiente sobre ese tema particular en los documentos disponibles.'\n"
            "Responde en español."
        )
            
    return data_samples_str


def filter_rag_for_hybrid(query: str, rag_context: str, model: str, server: str) -> str:
    """
    Filtra el contexto RAG para modo híbrido, extrayendo solo información relevante.
    """
    try:
        filter_prompt = f"""Analiza el siguiente contexto de documentos y extrae ÚNICAMENTE la información relevante para esta pregunta:

Pregunta: {query}

Contexto completo:
{rag_context}

Instrucciones:
- Extrae solo fragmentos directamente relacionados con la pregunta
- Mantén citas textuales importantes y nombres de documentos
- Descarta información no relacionada
- Sé conciso pero completo
- Si no hay información relevante, indica "No hay información relevante en los documentos"

Devuelve SOLO el contexto filtrado sin explicaciones adicionales."""

        payload = {
            "model": model,
            "prompt": filter_prompt,
            "stream": False
        }
        
        response = requests.post(
            f"{server}/api/generate",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=int(os.environ.get("OLLAMA_TIMEOUT", 600))
        )
        response.raise_for_status()
        
        result = response.json()
        filtered_context = result.get("response", "").strip()
        
        logger.debug(f"RAG context filtrado: {len(filtered_context)} caracteres (original: {len(rag_context)})")
        
        return filtered_context if filtered_context else rag_context
        
    except Exception as e:
        logger.error(f"Error filtrando RAG context: {str(e)}. Usando contexto original.")
        return rag_context

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
                        # Detectar si será modo híbrido (si ya existe rag_context)
                        json_context_content = generate_insight_prompt(
                            query, 
                            rows_serializable,
                            hybrid_mode=(rag_context != "")  # True si hay contexto RAG
                        )
                
                
                system_prompt = f"""Eres un asistente avanzado capaz de analizar múltiples fuentes de información.
Tienes acceso tanto a documentos de texto (PDF, DOCX) como a datos estructurados (JSON/SQL).

INSTRUCCIONES PARA RESPONDER:
1. **Analiza AMBAS fuentes**: Cada fuente contiene información valiosa y complementaria sobre el tema.
2. **Semántica y Relevancia**: El usuario puede usar términos generales (ej: 'libro', 'tecnología'). Debes mapear estos términos a los registros (ej: 'Articulo', 'Tesis', 'DESARROLLO_TECNOLOGIAS', etc.). Si hay una relación razonable con el tema, DEBES reportar el hallazgo.
3. **Fuente 1**: Registros estructurados. Úsalos para cifras, atributos específicos y listados.
4. **Fuente 2**: Documentos narrativos. Úsalos para explicaciones, antecedentes y contexto.
5. **Integración**: Combina la información de ambas fuentes para construir una respuesta completa y enriquecida.
6. **Idioma y Tono**: Responde SIEMPRE en español con tono profesional.
7. **Completitud**: Basa tu respuesta ÚNICAMENTE en la información proporcionada; no agregues conocimiento externo. Solo si NADA es relevante en ninguna fuente, indica que no tienes información suficiente.
"""
        
                print("Ejecutando búsqueda JSON...",json_context_content , flush=True)
                #print("Ejecutando búsqueda JSON...",rag_context , flush=True)
                # 3. Combine and Set Prompt
                if rag_context and json_context_content:
                    # HYBRID MODE
                    print("Modo Híbrido Activado (RAG + JSON)", flush=True)
                    logger.info("Modo Híbrido Activado (RAG + JSON)")
                    
                    # Filtrar RAG context para modo híbrido
                    logger.debug("Filtrando RAG context para modo híbrido...")
                    rag_context_filtered = filter_rag_for_hybrid(query, rag_context, REASONING_MODEL, server)

                    USER_PROMPT = f"""
                    PREGUNTA DEL USUARIO:
                    {query}

                    === FUENTE 1: DATOS ESTRUCTURADOS (JSON/SQL) ===
                    {json_context_content}

                    === FUENTE 2: DOCUMENTOS DE TEXTO (PDF/DOCX) ===
                    {rag_context_filtered}

                    INSTRUCCIONES:
                    - Responde con un resumen de lo más importante.
                    - Prioriza los datos actuales.
                    - Usa el histórico solo para dar contexto o cambios.
                    - Si hay contradicciones, menciona ambas y di cuál es actual y cuál histórico.
                    - Si no hay información suficiente, responde exactamente:
                    "No hay información suficiente en los registros."
                    """
                    
                    updated_payload["messages"] = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": USER_PROMPT},
                    ]
                    
                    with open("prompt_question.txt", "w", encoding="utf-8") as f:
                        f.write(system_prompt)
                    
                    with open("prompt_question_full.txt", "w", encoding="utf-8") as f:
                        f.write(json.dumps(updated_payload, ensure_ascii=False, indent=2))
                        
                elif json_context_content:
                    # JSON ONLY MODE
                    logger.info("Modo JSON Only Activado")
                    
                    USER_PROMPT = f"""
                    PREGUNTA DEL USUARIO:
                    {query}

                    === FUENTE 1: DATOS ESTRUCTURADOS (JSON/SQL) ===
                    {json_context_content}

                    INSTRUCCIONES:
                    - Responde con un resumen de lo más importante.
                    - Prioriza los datos actuales.
                    - Usa el histórico solo para dar contexto o cambios.
                    - Si hay contradicciones, menciona ambas y di cuál es actual y cuál histórico.
                    - Si no hay información suficiente, responde exactamente:
                    "No hay información suficiente en los registros."
                    """
                    
                    updated_payload["messages"] = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": USER_PROMPT},
                    ]
                    
                    with open("prompt_question_json.txt", "w", encoding="utf-8") as f:
                        f.write(json.dumps(updated_payload, ensure_ascii=False, indent=2))
                        
                elif rag_context:
                    
                    USER_PROMPT = f"""
                    PREGUNTA DEL USUARIO:
                    {query}

                    === FUENTE 2: DATOS ESTRUCTURADOS (JSON/SQL) ===
                    {rag_context}

                    INSTRUCCIONES:
                    - Responde con un resumen de lo más importante.
                    - Prioriza los datos actuales.
                    - Usa el histórico solo para dar contexto o cambios.
                    - Si hay contradicciones, menciona ambas y di cuál es actual y cuál histórico.
                    - Si no hay información suficiente, responde exactamente:
                    "No hay información suficiente en los registros."
                    """
                    
                    
                    updated_payload["messages"] = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": USER_PROMPT},
                    ]
                    
                    with open("prompt_question_rag.txt", "w", encoding="utf-8") as f:
                        f.write(json.dumps(updated_payload, ensure_ascii=False, indent=2))
                        
                else:
                    # NO INFO FOUND
                    logger.info("No se encontró información en ninguna fuente")
                    updated_payload["messages"].insert(0, {
                        "role": "system",
                        "content": "Eres un asistente amable. El usuario ha hecho una pregunta pero no tengo información específica en los documentos para responderla. Responde amablemente que no tienes información suficiente sobre ese tema específico en los documentos disponibles."
                    })

                if updated_payload["messages"][0]["role"] == "system":
                    last_user_idx = -1
                    for i in range(len(updated_payload["messages"]) - 1, -1, -1):
                        if updated_payload["messages"][i]["role"] == "user":
                            last_user_idx = i
                            break
                    
                    if last_user_idx != -1:
                        reminder = "\n\n(Recordatorio: Actúa estrictamente según las instrucciones de sistema. Sé semánticamente flexible: si un registro o documento trata sobre el tema de la pregunta aunque use términos distintos, DEBES reportarlo. Si no hay absolutamente nada relevante, di que no tienes información suficiente.)"
                        updated_payload["messages"][last_user_idx]["content"] += reminder

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