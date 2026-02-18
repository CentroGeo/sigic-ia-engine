
import json
import logging
import requests
from django.conf import settings
from fileuploads.models import Context, DocumentEmbedding

logger = logging.getLogger(__name__)


def extract_locations_from_context(context_id, model="llama3.1"):
    """
    Extrae ubicaciones (países, estados, municipios) de los documentos de un contexto.
    Procesa todos los chunks en lotes para asegurar cobertura completa.
    """
    try:
        context = Context.objects.get(id=context_id)
        files = context.files.all()
        
        all_locations = set()
        
        # Configuración del lote
        BATCH_SIZE = 5  # Número de chunks por llamada al LLM
        
        for file in files:
            # Obtener todos los chunks ordenados
            chunks = DocumentEmbedding.objects.filter(file=file).order_by('chunk_index')
            total_chunks = chunks.count()
            
            logger.info(f"Procesando archivo {file.filename} con {total_chunks} chunks para extracción de lugares.")
            
            current_batch_text = ""
            current_chunk_count = 0
            
            for chunk in chunks:
                current_batch_text += chunk.text + "\n\n"
                current_chunk_count += 1
                
                # Procesar si alcanzamos el tamaño del lote o es el último chunk
                if current_chunk_count >= BATCH_SIZE:
                    batch_locations = process_batch(current_batch_text, model)
                    all_locations.update(batch_locations)
                    
                    # Reiniciar lote
                    current_batch_text = ""
                    current_chunk_count = 0
            
            # Procesar el remanente si existe
            if current_batch_text:
                batch_locations = process_batch(current_batch_text, model)
                all_locations.update(batch_locations)

        return sorted(list(all_locations))

    except Exception as e:
        logger.error(f"Error extrayendo ubicaciones: {str(e)}")
        return []

def process_batch(text, model):
    """
    Función auxiliar para enviar un lote de texto a Ollama.
    """
    if not text.strip():
        return []
        
    prompt = f"""
    Analiza el siguiente texto y extrae UNICAMENTE las **JURISDICCIONES POLÍTICO-ADMINISTRATIVAS** (lugares que tienen un gobierno, alcalde o gobernador).

    TEXTO:
    {text[:12000]}
    
    REGLAS DE EXCLUSIÓN (Blacklist):
    NO incluyas nada que empiece o contenga:
    - "Catedral de..."
    - "Palacio de..."
    - "Jardines de..."
    - "Gran Muralla..."
    - "Parque Nacional..."
    - "Museo..."
    - "Universidad..."
    - "UNESCO"
    - "Zona Arqueológica..."
    - "Abadía..."
    - "Basílica..."
    - "Conjunto Histórico..."
    - "Sitio..."
    
    CRITERIO DE ACEPTACIÓN:
    - ¿Es un PAÍS? (SÍ) -> "China", "Francia"
    - ¿Es un ESTADO/PROVINCIA? (SÍ) -> "Yucatán", "Normandía"
    - ¿Es una CIUDAD/MUNICIPIO? (SÍ) -> "Beijing", "Versalles" (la ciudad, no el palacio)
    
    - ¿Es un edificio? (NO) -> Ignorar "Palacio de Versalles"
    - ¿Es un monumento? (NO) -> Ignorar "Gran Muralla China"
    - ¿Es una organización? (NO) -> Ignorar "UNESCO"
    - ¿Es un sitio religioso? (NO) -> Ignorar "Abadía de Fontenay"

    SALIDA:
    Devuelve un JSON: {{ "lugares": ["China", "Francia", "Versalles"] }}
    """

    ollama_url = f"{settings.OLLAMA_API_URL}/api/generate"
    
    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {
            "temperature": 0.1
        }
    }
    
    try:
        response = requests.post(ollama_url, json=payload, timeout=120)
        response.raise_for_status()
        
        result = response.json()
        response_text = result.get("response", "{}")
        
        data = json.loads(response_text)
        locations = data.get("lugares", [])
        
        # Limpieza básica
        raw_locations = data.get("lugares", [])
        
        # Filtro estricto post-procesamiento
        clean_locations = []
        
        BLACKLIST = [
            "catedral", "palacio", "jardines", "gran muralla", "parque nacional", 
            "museo", "universidad", "unesco", "zona arqueológica", "abadía", 
            "basílica", "conjunto", "sitio", "centro histórico", "reserva", 
            "santuario", "templo", "fortaleza", "castillo", "monasterio"
        ]
        
        for loc in raw_locations:
            if not isinstance(loc, str) or not loc.strip():
                continue
                
            loc_clean = loc.strip()
            loc_lower = loc_clean.lower()
            
            # Regla 1: Longitud excesiva (probablemente una descripción o nombre de sitio)
            # La mayoría de ciudades/países tienen 1-3 palabras. Más de 4 es sospechoso.
            if len(loc_clean.split()) > 4:
                continue
                
            # Regla 2: Palabras prohibidas
            if any(bad_word in loc_lower for bad_word in BLACKLIST):
                continue
                
            # Regla 3: No es dígito ni símbolo
            if any(char.isdigit() for char in loc_clean):
               continue
               
            clean_locations.append(loc_clean)

        return clean_locations
        
    except Exception as e:
        logger.error(f"Error en batch de extracción: {e}")
        return []
