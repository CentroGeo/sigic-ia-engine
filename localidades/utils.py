import os
import json
import logging
import requests
from django.conf import settings
from fileuploads.models import Context, DocumentEmbedding, Files

logger = logging.getLogger(__name__)

def get_system_prompt(focus="México", entity_types=None):
    if not entity_types:
        entity_types = ["país", "estado", "municipio", "localidad"]
        
    valid_types = ["país", "estado", "municipio", "localidad", "infraestructura"]
    entity_types = [t for t in entity_types if t in valid_types]
    if not entity_types:
        entity_types = ["país", "estado", "municipio", "localidad"]
        
    types_str = " | ".join(entity_types)
    types_list_str = ", ".join([f"'{t}'" for t in entity_types])
    
    incluye_infra = "infraestructura" in entity_types
    
    exclusions = "10. EXCLUSIONES: Edificios, monumentos, parques naturales, museos, universidades, y sitios arqueológicos."
    infra_rule = ""
    if incluye_infra:
        exclusions = "10. EXCLUSIONES: Palabras aleatorias que no sean lugares geográficos concretos."
        infra_rule = "- ¿Es una INFRAESTRUCTURA/EDIFICIO/SITIO? (SÍ) -> 'Hospital Juárez', 'Museo de Antropología', 'Puente Baluarte', 'Universidad' -> (Tipo: 'infraestructura')"

    return f"""
Eres un extractor de entidades geográficas y geocodificador simultáneo.
Tu tarea es analizar el texto proporcionado y extraer exclusivamente las JURISDICCIONES POLÍTICO-ADMINISTRATIVAS e INFRAESTRUCTURAS solicitadas.

REGLAS ABSOLUTAS:
1. Devuelve la información en formato JSON.
2. NO incluyas explicaciones ni texto adicional fuera del JSON.
3. Si no encuentras ninguna entidad geográfica, devuelve una lista vacía.
4. Categoriza cada hallazgo ESTRICTAMENTE como uno de los siguientes: {types_list_str}. Ignora los que no correspondan.
5. PROHIBIDO INFERIR: No agregues lugares que no estén mencionados literalmente en el texto. El "context" DEBE incluir la palabra detectada.
6. ESTRICTO CUMPLIMIENTO DEL JSON: NUNCA omitas el campo "context", ni las "coordenadas". ESTÁ PROHIBIDO responder con "N/A" en los campos de estado y país. Si no tienes la información, escribe "No especificado".
7. DEPENDENCIAS JERÁRQUICAS OBLIGATORIAS:
   - Si la entidad es un 'estado', DEBES agregar un campo 'país'.
   - Si la entidad es un 'municipio', 'localidad' o 'infraestructura', DEBES agregar 'estado' y 'país'.
8. REGLAS DE CLASIFICACIÓN (EVITA ERRORES COMUNES):
   - NUNCA clasifiques a un país soberano (ej. Francia, Japón, México) como "estado". Los países siempre son "país".
   - Si un nombre puede referirse a un Estado y a un Municipio a la vez (ej. Puebla, Querétaro, Oaxaca), analiza el contexto.
9. COORDENADAS: Para cada lugar, estima sus coordenadas reales y devuélvelas en un arreglo numérico [longitud, latitud]. OBLIGATORIO escribir primero longitud y luego latitud.
{exclusions}

CRITERIO DE ACEPTACIÓN:
   - ¿Es un PAÍS SOBERANO? (SÍ) -> "China", "Francia" -> (Tipo: 'país')
   - ¿Es un ESTADO/PROVINCIA/DEPARTAMENTO? (SÍ) -> "Yucatán", "Normandía" -> (Tipo: 'estado')
   - ¿Es una CIUDAD/LOCALIDAD/PUEBLO? (SÍ) -> "París" -> (Tipo: 'municipio' o 'localidad')
   {infra_rule}

FORMATO DE SALIDA ESPERADO:
{{
  "entities": [
    {{
      "name": "Nombre exacto de la entidad",
      "type": "{types_str}",
      "context": "EXTRAE Y COPIA LA ORACIÓN COMPLETA (entre 10 y 30 palabras) del texto original donde aparece el lugar. ESTÁ PROHIBIDO poner solo el nombre del lugar aquí.",
      "país": "Nombre del país al que pertenece (sólo si type no es país)",
      "estado": "Nombre del estado o provincia al que pertenece (sólo si type no es país ni estado)",
      "coordenadas": [longitud, latitud]
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

def extract_localities_from_context(context_id=None, model="deepseek-r1:32b", focus=None, file_ids=None, entity_types=None):
    """
    Usa Ollama para extraer localidades de documentos, con enfoque geográfico configurable.
    Si focus es None o 'auto', intenta detectarlo automáticamente del texto.
    Acepta 'context_id' o una lista explícita de 'file_ids'.
    'entity_types' permite filtrar la extracción (ej. ['país', 'estado', 'municipio', 'localidad', 'infraestructura'])
    """
    server = settings.OLLAMA_API_URL
    
    try:
        # Obtener los archivos a procesar según los parámetros
        if context_id and file_ids:
            context_obj = Context.objects.get(id=context_id)
            files = context_obj.files.filter(id__in=file_ids)
        elif context_id:
            context_obj = Context.objects.get(id=context_id)
            files = context_obj.files.all()
        elif file_ids:
            files = Files.objects.filter(id__in=file_ids)
        else:
            return {"entities": [], "error": "Se requiere context_id o file_ids."}
        
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
        
        system_prompt = get_system_prompt(focus, entity_types)
        
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
        geojson_features = []
        seen = set()
        
        BLACKLIST = [
            "catedral", "palacio", "jardines", "gran muralla", "parque nacional", 
            "museo", "universidad","instituto", "unesco", "zona arqueológica", "abadía", 
            "basílica", "conjunto", "sitio", "centro histórico", "reserva", 
            "santuario", "templo", "fortaleza", "castillo", "monasterio", "calle", "avenida"
        ]
        
        incluye_infra = (entity_types and "infraestructura" in entity_types)
        if incluye_infra:
            BLACKLIST = ["calle", "avenida"]  # Reducimos blacklist solo a vialidades si piden infraestructura
            
        valid_requested = entity_types if entity_types else ["país", "estado", "municipio", "localidad"]
        
        for entity in all_entities:
            # Algunas veces Ollama podría no devolver el key
            name = entity.get("name", "")
            etype = entity.get("type", "")
            
            if not name or not isinstance(name, str) or not etype or not isinstance(etype, str):
                continue
            
            name_clean = name.strip()
            name_lower = name_clean.lower()
            
            # Homologar tipo (si dice ciudad, pueblo o aldea -> municipio o localidad)
            etype_lower = etype.strip().lower()
            
            if etype_lower in ["ciudad", "pueblo", "aldea"]:
                etype_clean = "localidad" if "localidad" in valid_requested else ("municipio" if "municipio" in valid_requested else etype_lower)
            elif etype_lower in ["edificio", "museo", "parque", "universidad", "infraestructura"]:
                etype_clean = "infraestructura" if "infraestructura" in valid_requested else etype_lower
            else:
                etype_clean = etype_lower
                
            # Si el clasificado final no esta en la lista valid_requested, lo forzamos o descartamos
            if etype_clean not in valid_requested:
                if "localidad" in valid_requested:
                    etype_clean = "localidad"
                else:
                    continue  # Descartamos porque no pertenece a lo solicitado
            
            # Regla 1: Longitud excesiva (probablemente una descripción)
            if len(name_clean.split()) > (8 if incluye_infra else 5):
                continue
                
            # Regla 2: Palabras prohibidas
            if any(bad_word in name_lower for bad_word in BLACKLIST):
                continue
                
            # Regla 3: No es dígito ni símbolo
            if any(char.isdigit() for char in name_clean):
               continue
                
            key = (name_clean.lower(), etype_clean)
            if key not in seen:
                seen.add(key)
                
                # Extraemos coordenadas para evitar que queden en "properties" si es indeseado
                # aunque en GeoJSON podrían quedarse, es mejor sacarlas para armar la geometría
                coords = entity.pop("coordenadas", None)
                
                entity["name"] = name_clean  # Actualizamos por el nombre limpio
                entity["type"] = etype_clean # Actualizamos por el tipo homologado
                
                # Garantizar que el campo "context" exista siempre
                if "context" not in entity:
                    entity["context"] = "Contexto no proporcionado por el modelo."
                
                # Garantizar jerarquías sin N/A
                val_placeholder = "No especificado"
                if etype_clean in ["municipio", "localidad"]:
                    if "estado" not in entity or entity["estado"] in ["N/A", "n/a", "N/a", ""]:
                        entity["estado"] = val_placeholder
                    if "país" not in entity or entity["país"] in ["N/A", "n/a", "N/a", ""]:
                        entity["país"] = focus if focus and focus != "auto" else val_placeholder
                elif etype_clean == "estado":
                    if "país" not in entity or entity["país"] in ["N/A", "n/a", "N/a", ""]:
                        entity["país"] = focus if focus and focus != "auto" else val_placeholder
                
                unique_entities.append(entity)
                
                # Construcción del Feature GeoJSON
                # Verificamos que las coordenadas sean un array de 2 números
                is_valid_coords = isinstance(coords, list) and len(coords) == 2 and all(isinstance(c, (int, float)) for c in coords)
                
                feature = {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": coords
                    } if is_valid_coords else None,
                    "properties": entity
                }
                geojson_features.append(feature)

        geojson_data = {
            "type": "FeatureCollection",
            "features": geojson_features
        }

        # Guardar archivo descargable
        import time
        timestamp = int(time.time())
        prefix = f"ctx_{context_id}" if context_id else f"files_{len(file_ids)}"
        filename = f"localidades_{prefix}_{timestamp}.geojson"
        geojsons_dir = os.path.join(settings.MEDIA_ROOT, "geojsons")
        os.makedirs(geojsons_dir, exist_ok=True)
        file_path = os.path.join(geojsons_dir, filename)

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(geojson_data, f, ensure_ascii=False, indent=2)

        file_url = f"{settings.MEDIA_URL}geojsons/{filename}"

        return {
            "entities": unique_entities,
            "geojson": geojson_data,
            "download_url": file_url,
            "detected_focus": focus
        }

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
