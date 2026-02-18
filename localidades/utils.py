import os
import json
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

def get_system_prompt(focus="México"):
    return f"""
Eres un extractor de entidades geográficas especializado en {focus}.
Tu tarea es analizar el texto proporcionado y extraer exclusivamente los nombres de LOCALIDADES, MUNICIPIOS y ESTADOS mencionados para la región de {focus}.

REGLAS ABSOLUTAS:
1. Devuelve la información en formato JSON.
2. NO incluyas explicaciones ni texto adicional fuera del JSON.
3. Si no encuentras ninguna entidad geográfica, devuelve una lista vacía.
4. Categoriza cada hallazgo como 'localidad', 'municipio' o 'estado' (o la subdivisión administrativa equivalente en {focus}).
5. Si el texto menciona una localidad y su municipio/estado, intenta relacionarlos.

FORMATO DE SALIDA ESPERADO:
{{
  "entities": [
    {{
      "name": "Nombre de la entidad",
      "type": "localidad | municipio | estado",
      "context": "breve fragmento del texto original"
    }}
  ]
}}
"""

def detect_geographic_focus(text, model="deepseek-r1:32b"):
    """
    Detecta el país o región principal del texto.
    """
    server = settings.OLLAMA_API_URL
    system_prompt = "Eres un experto en geografía. Analiza el texto y responde ÚNICAMENTE con el nombre del país o región principal al que se refiere (ej: 'México', 'Alemania', 'Estados Unidos'). Si no es claro, responde 'Global'."
    
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": f"Texto: {text}\n\nPaís principal:",
        "stream": False
    }
    
    try:
        response = requests.post(
            f"{server}/api/generate",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        response.raise_for_status()
        result = response.json()
        focus = result.get("response", "Global").strip()
        # Limpiar respuesta (a veces los modelos chatty ponen punto final o intro)
        if "\n" in focus:
            focus = focus.split("\n")[0]
        logger.info(f"Enfoque geográfico detectado: {focus}")
        return focus
    except Exception as e:
        logger.warning(f"Error detectando enfoque, usando default 'México': {str(e)}")
        return "México"

def extract_localities_from_text(text, model="deepseek-r1:32b", focus=None):
    """
    Usa Ollama para extraer localidades de un texto, con enfoque geográfico configurable.
    Si focus es None, intenta detectarlo automáticamente.
    """
    server = settings.OLLAMA_API_URL
    
    if not focus or focus == "auto":
        focus = detect_geographic_focus(text, model)
    
    system_prompt = get_system_prompt(focus)
    
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": f"Extrae las localidades de este texto: {text}",
        "stream": False,
        "format": "json"
    }
    
    try:
        response = requests.post(
            f"{server}/api/generate",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=int(os.environ.get("OLLAMA_TIMEOUT", 600))
        )
        response.raise_for_status()
        result = response.json()
        
        # El resultado suele venir en result['response'] como un string JSON
        entities_data = json.loads(result.get("response", "{}"))
        
        # Inyectar el focus detectado en el resultado para referencia
        if isinstance(entities_data, dict):
            entities_data["detected_focus"] = focus
            
        return entities_data
        
    except requests.exceptions.HTTPError as e:
        error_msg = f"HTTP Error {e.response.status_code}: {e.response.text}"
        logger.error(f"Error extrayendo localidades: {error_msg}")
        return {"entities": [], "error": error_msg}
    except Exception as e:
        logger.error(f"Error extrayendo localidades: {str(e)}")
        return {"entities": [], "error": str(e)}
