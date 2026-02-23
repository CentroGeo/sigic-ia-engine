import os
import json
import logging
import requests
from django.conf import settings
from fileuploads.models import Context, DocumentEmbedding

logger = logging.getLogger(__name__)

def get_system_prompt(focus="México"):
    return f"""
Eres un extractor de entidades geográficas especializado.
Tu tarea es analizar el texto proporcionado y extraer exclusivamente las JURISDICCIONES POLÍTICO-ADMINISTRATIVAS (lugares que tienen un gobierno, alcalde o gobernador) .

REGLAS ABSOLUTAS:
1. Devuelve la información en formato JSON.
2. NO incluyas explicaciones ni texto adicional fuera del JSON.
3. Si no encuentras ninguna entidad geográfica, devuelve una lista vacía.
4. Categoriza cada hallazgo como 'país', 'municipio' o 'estado'.
5. Si el texto menciona una localidad y su municipio/estado, intenta relacionarlos.
6. EXCLUSIONES (NO DEBES INCLUIR):
   - Edificios o monumentos (ej. "Palacio de...", "Catedral de...", "Gran Muralla...")
   - Parques o reservas naturales (ej. "Parque Nacional...", "Reserva de...")
   - Museos, Universidades, Instituciones (ej. "Museo...", "Universidad...", "UNESCO")
   - Sitios arqueológicos o religiosos (ej. "Zona Arqueológica...", "Abadía...", "Santuario...")
   - Direcciones postales específicas, calles o avenidas.

CRITERIO DE ACEPTACIÓN:
   - ¿Es un PAÍS? (SÍ) -> "China", "Francia"
   - ¿Es un ESTADO/PROVINCIA? (SÍ) -> "Yucatán", "Normandía"
   - ¿Es una CIUDAD/MUNICIPIO/LOCALIDAD? (SÍ) -> "Beijing", "Versalles" (la ciudad, no el palacio)
   
   - ¿Es un edificio? (NO) -> Ignorar "Palacio de Versalles"
   - ¿Es un monumento? (NO) -> Ignorar "Gran Muralla China"
   - ¿Es una organización? (NO) -> Ignorar "UNESCO"
   - ¿Es un sitio religioso? (NO) -> Ignorar "Abadía de Fontenay"

FORMATO DE SALIDA ESPERADO:
{{
  "entities": [
    {{
      "name": "Nombre del país, estado o municipio",
      "type": "sólo puede ser una de estas opciones según corresponda:país | estado | municipio",
      "context": "fragmento del texto original donde se menciona la localidad"
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

def extract_localities_from_context(context_id, model="deepseek-r1:32b", focus=None):
    """
    Usa Ollama para extraer localidades de los documentos de un contexto, con enfoque geográfico configurable.
    Si focus es None o 'auto', intenta detectarlo automáticamente del texto.
    """
    server = settings.OLLAMA_API_URL
    
    try:
        context_obj = Context.objects.get(id=context_id)
        files = context_obj.files.all()
        
        all_entities = []
        BATCH_SIZE = 5
        sample_text = ""
        
        # Intentar recolectar texto de muestra si focus debe detectarse
        if not focus or focus == "auto":
            for file in files:
                chunks = DocumentEmbedding.objects.filter(file=file).order_by('chunk_index')[:BATCH_SIZE]
                for chunk in chunks:
                    sample_text += chunk.text + "\n\n"
                if sample_text:
                    break
            
            # Si logramos extraer texto, detectamos el focus
            if sample_text:
                focus = detect_geographic_focus(sample_text, model)
            else:
                focus = "México"
        
        system_prompt = get_system_prompt(focus)
        
        for file in files:
            chunks = DocumentEmbedding.objects.filter(file=file).order_by('chunk_index')
            total_chunks = chunks.count()
            
            logger.info(f"Procesando archivo {file.filename} con {total_chunks} chunks para extracción de localidades.")
            
            current_batch_text = ""
            current_chunk_count = 0
            
            for chunk in chunks:
                current_batch_text += chunk.text + "\n\n"
                current_chunk_count += 1
                
                if current_chunk_count >= BATCH_SIZE:
                    batch_entities = process_entities_batch(current_batch_text, model, system_prompt, server)
                    all_entities.extend(batch_entities)
                    
                    current_batch_text = ""
                    current_chunk_count = 0
            
            # Remanente
            if current_batch_text:
                batch_entities = process_entities_batch(current_batch_text, model, system_prompt, server)
                all_entities.extend(batch_entities)

        # Eliminar posibles duplicados y aplicar filtro estricto (blacklist)
        unique_entities = []
        seen = set()
        
        BLACKLIST = [
            "catedral", "palacio", "jardines", "gran muralla", "parque nacional", 
            "museo", "universidad","instituto", "unesco", "zona arqueológica", "abadía", 
            "basílica", "conjunto", "sitio", "centro histórico", "reserva", 
            "santuario", "templo", "fortaleza", "castillo", "monasterio", "calle", "avenida"
        ]
        
        for entity in all_entities:
            # Algunas veces Ollama podría no devolver el key
            name = entity.get("name", "")
            etype = entity.get("type", "")
            
            if not name or not isinstance(name, str):
                continue
            
            name_clean = name.strip()
            name_lower = name_clean.lower()
            
            # Regla 1: Longitud excesiva (probablemente una descripción o nombre de sitio en vez de ciudad)
            if len(name_clean.split()) > 5:
                continue
                
            # Regla 2: Palabras prohibidas
            if any(bad_word in name_lower for bad_word in BLACKLIST):
                continue
                
            # Regla 3: No es dígito ni símbolo
            if any(char.isdigit() for char in name_clean):
               continue
                
            key = (name_clean.lower(), etype.lower())
            if key not in seen:
                seen.add(key)
                entity["name"] = name_clean  # Actualizamos por el nombre limpio
                unique_entities.append(entity)

        return {"entities": unique_entities, "detected_focus": focus}

    except Exception as e:
        logger.error(f"Error extrayendo localidades del contexto: {str(e)}")
        return {"entities": [], "error": str(e)}

def process_entities_batch(text, model, system_prompt, server):
    if not text.strip():
        return []
        
    payload = {
        "model": model,
        "system": system_prompt,
        "prompt": f"Extrae las localidades de este texto: {text[:12000]}", # límite por seguridad
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
        
        entities_data = json.loads(result.get("response", "{}"))
        if isinstance(entities_data, dict):
            return entities_data.get("entities", [])
        elif isinstance(entities_data, list):
            # En caso de que el JSON root sea una lista
            return entities_data
        return []
    except Exception as e:
        logger.error(f"Error en batch de extracción: {e}")
        return []
