from django.db import connection
from fileuploads.models import DocumentEmbedding
from .prompt_question import BASE_SYSTEM_PROMPT_JSON
from .prompt_keys import BASE_SYSTEM_PROMPT_KEYS
from .prompt_semantico import BASE_SYSTEM_PROMPT_SEMANTICO
import json
import logging
import requests
from typing import List, Any

logger = logging.getLogger(__name__)

def search_in_json_files(context, query, reasoning_model, server_url) -> List[List[str]]:
    """
    Realiza la búsqueda en archivos JSON usando SQL generado por LLM.
    Returns: List[List[str]] (rows_serializable)
    """
    try:
        list_files_json = list(context.files.filter(document_type__in=['application/json','text/csv']).values_list('id', flat=True))
        if not list_files_json:
            return []

        # 1. Semantic Search
        system_prompt_semantico = BASE_SYSTEM_PROMPT_SEMANTICO.format()
        
        url = f"{server_url}/api/chat"
        sql_payload = {
            "model": reasoning_model,
            "messages": [
                {"role": "system", "content": system_prompt_semantico},
                {"role": "user", "content": query},
            ],
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0
            }
        }
        
        resp = requests.post(
            url, json=sql_payload, headers={"Content-Type": "application/json"}, timeout=500
        )
        resp.raise_for_status()
        data = resp.json()
        search_terms_str = data["message"]["content"]
        logger.debug(f"Semantico!!!: {search_terms_str}")
        print(f"Semantico!!!: {search_terms_str}", flush=True)
        try:
            search_terms = json.loads(search_terms_str)
        except json.JSONDecodeError:
            logger.error("Error decoding semantic search terms JSON")
            return []
        
        if not search_terms.get("should_search", False):
            return []

        # 2. Key/Structure Search
        if search_terms.get("has_terms") or search_terms.get("has_quantity") or search_terms.get("has_range"):
            
            lista_de_keys = DocumentEmbedding.get_json_keys_with_types(list_files_json)
            
            llm_context_keys = f"""
                SEARCH_TERMS (AUTORIDAD FINAL):
                {json.dumps(search_terms, indent=2)}
            """
            system_prompt_KEYS = BASE_SYSTEM_PROMPT_KEYS.format(schema=lista_de_keys, list_files_json=list_files_json)
            
            rows_keys = []
            for _ in range(3): 
                url = f"{server_url}/api/chat"
                sql_payload = {
                    "model": reasoning_model,
                    "messages": [
                        {"role": "system", "content": system_prompt_KEYS},
                        {"role": "user", "content": llm_context_keys},
                    ],
                    "stream": False, "temperature": 0, "think": False
                }
                
                resp = requests.post(
                    url, json=sql_payload, headers={"Content-Type": "application/json"}, timeout=500
                )
                resp.raise_for_status()
                data = resp.json()
                sql = data["message"]["content"]

                try:    
                    with connection.cursor() as cursor:
                        cursor.execute(sql)
                        rows_keys = cursor.fetchall()
                        break
                except Exception as e:
                    logger.error(f"Error executing key SQL: {e}")
                    llm_context_keys = (
                        f"Pregunta original: {query}\n"
                        f"El SQL '{sql}' produjo el error: {str(e)}\n"
                        "Corrige la consulta SQL."
                    )    
                    continue
            
            # Process keys
            key_sql = [k[0] for k in rows_keys]
            type_sql = [k[1] for k in rows_keys]
            list_reduce_keys = []
            
            i = 0
            for row_sql_keys in key_sql:
                for row_all_keys in lista_de_keys:
                    # Logic directly copied from original
                     if( 
                        len(row_sql_keys.split(".")) == len(row_all_keys['key'].replace(".array.", ".").split(".")) and 
                        row_sql_keys in row_all_keys['key'].replace(".array.", ".")
                    ):
                        json_path = row_all_keys['key'].replace(".array.", ".").split(".")
                        is_array = row_all_keys['key'] != row_all_keys['key'].replace(".array.", "[].")
                        row_all_keys['key'] = row_all_keys['key'].replace(".array.", "[].")
                        
                        info = {    
                            "key": row_all_keys['key'],
                            "type": type_sql[i],
                            "count": row_all_keys['count'],
                            "is_array":  is_array,
                            "json_path" : json_path,
                            "is_nested_depth" : len(json_path) - 1
                        }
                        list_reduce_keys.append(info)   
                i += 1
            
            # 3. Data Search
            if len(list_reduce_keys) > 0:
                system_prompt = BASE_SYSTEM_PROMPT_JSON.format(list_files_json=list_files_json)
                
                llm_context_data = f"""
                    SEARCH_TERMS:
                    {json.dumps(search_terms, indent=2)}

                    METADATA_KEYS:
                    {json.dumps(list_reduce_keys, indent=2)}
                """
                
                rows_data = None
                for _ in range(5): 
                    url = f"{server_url}/api/chat"
                    sql_payload = {
                        "model": reasoning_model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": llm_context_data},
                        ],
                        "stream": False, "temperature": 0, "think": False
                    }
                    
                    resp = requests.post(
                        url, json=sql_payload, headers={"Content-Type": "application/json"}, timeout=500
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    sql = data["message"]["content"]
                    
                    with open("llm_context_data.txt", "w", encoding="utf-8") as f:
                        f.write(llm_context_data)
                    
                    try:    
                        with connection.cursor() as cursor:
                            cursor.execute(sql)
                            rows_data = cursor.fetchall()
                            break
                    except Exception as e:
                        logger.error(f"Error executing data SQL: {e}")
                        llm_context_data = f"""
                            Pregunta original: {query}
                            
                            SEARCH_TERMS:
                            {json.dumps(search_terms, indent=2)}

                            METADATA_KEYS:
                            {json.dumps(list_reduce_keys, indent=2)}

                            SQL previo que falló:
                            {sql}

                            Error producido:
                            {str(e)}
                            
                            Instrucciones:
                            - Corrige la consulta SQL.
                            """
                        continue
                
                if rows_data is None:
                    logger.error("Failed to execute final data SQL")
                    return []
                
                # Serialize Results
                rows_serializable = []
                for row in rows_data:
                    serialized_row = []
                    for value in row:
                        if value is not None:
                            serialized_row.append(str(value))
                    if serialized_row:
                        rows_serializable.append(serialized_row)

                return rows_serializable

            else:
                # Fallback specific (has terms but no keys found to reduce)
                return _fallback_search(list_files_json)
        else:
            # Fallback simple (no search terms)
            return _fallback_search(list_files_json)

    except Exception as e:
        logger.error(f"Error in search_in_json_files: {str(e)}")
        return []

def _fallback_search(list_files_json):
    sql = f"""
        SELECT text_json
        FROM fileuploads_documentembedding as f
        WHERE file_id = ANY(ARRAY{list_files_json})
        limit 20
    """
    try:    
        with connection.cursor() as cursor:
            cursor.execute(sql)
            rows = cursor.fetchall()
            
        rows_serializable = []
        if rows:
            for row in rows:
                serialized_row = []
                for value in row:
                    if value is not None:
                        serialized_row.append(str(value))
                if serialized_row:
                    rows_serializable.append(serialized_row)
        return rows_serializable
    except Exception as e:
        logger.error(f"Error in fallback search: {e}")
        return []
