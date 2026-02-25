import json
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Prompts por tipo de reporte
# ---------------------------------------------------------------------------

INSTITUTIONAL_PROMPT = """
Eres un redactor de reportes institucionales formales.
Genera el reporte con la siguiente estructura obligatoria:
1. Resumen ejecutivo
2. Antecedentes y contexto
3. Hallazgos principales
4. Conclusiones
5. Recomendaciones

Usa únicamente la evidencia proporcionada. No inventes datos ni uses conocimiento externo.
Si la evidencia es insuficiente para alguna sección, indícalo explícitamente.
""".strip()

DESCRIPTIVE_PROMPT = """
Eres un analista especializado en reportes descriptivos detallados.
Genera un análisis descriptivo con énfasis en datos, cifras y hechos concretos.
Organiza el contenido en secciones temáticas claramente definidas.
Incluye tablas o listas cuando ayuden a presentar la información.

Usa únicamente la evidencia proporcionada. No inventes datos ni uses conocimiento externo.
Si algún dato no está disponible en la evidencia, indícalo explícitamente.
""".strip()

SUMMARY_PROMPT = """
Eres un redactor de resúmenes ejecutivos concisos.
Genera un resumen ejecutivo breve (máximo equivalente a 1 página) con:
- Objetivo principal
- Puntos clave (máximo 5)
- Conclusión

Sé directo y conciso. Usa únicamente la evidencia proporcionada.
""".strip()

EVALUATION_PROMPT = """
Eres un evaluador crítico especializado en análisis de documentos.
Genera una evaluación crítica con los siguientes criterios:
1. Criterios de evaluación aplicados
2. Fortalezas identificadas
3. Debilidades y áreas de mejora
4. Recomendaciones concretas y accionables

Basa tu evaluación exclusivamente en la evidencia proporcionada.
Sé objetivo e imparcial. No uses conocimiento externo.
""".strip()

_PROMPT_MAP = {
    "institutional": INSTITUTIONAL_PROMPT,
    "descriptive": DESCRIPTIVE_PROMPT,
    "summary": SUMMARY_PROMPT,
    "evaluation": EVALUATION_PROMPT,
}

# ---------------------------------------------------------------------------
# Reglas de formato por output_format
# ---------------------------------------------------------------------------

_FORMAT_RULES = {
    "markdown": """
FORMATO DE SALIDA: Markdown
- Usa ## para secciones principales y ### para subsecciones
- Usa tablas Markdown cuando presentes datos comparativos
- Usa listas con guiones (-) para enumeraciones
- Usa **negritas** para resaltar conceptos clave
- Separa secciones con líneas en blanco
- NO uses HTML ni caracteres especiales fuera del estándar Markdown
""".strip(),

    "plain_text": """
FORMATO DE SALIDA: Texto plano
- NO uses caracteres Markdown (sin #, **, _, -, etc.)
- Separa secciones con líneas en blanco y títulos en MAYÚSCULAS
- Usa espaciado para organizar el contenido
- Escribe de forma clara y directa sin formato especial
""".strip(),

    "csv": """
FORMATO DE SALIDA: CSV
- Responde ÚNICAMENTE con datos en formato CSV válido
- Primera fila: encabezados de columnas
- Cada fila subsecuente: un registro de datos
- Separa campos con comas; usa comillas dobles para campos con comas internas
- NO incluyas explicaciones, texto introductorio ni texto después del CSV
- Si los datos no son tabulares, extrae las métricas/valores clave en formato tabla
""".strip(),
}


def build_prompt(
    report_type: str,
    output_format: str,
    instructions: str,
    evidence: List[Dict[str, Any]],
    report_name: str = "",
) -> List[Dict[str, str]]:
    """
    Construye la lista de mensajes [system, user] listos para ollama_chat.

    Parameters
    ----------
    report_type   : institutional | descriptive | summary | evaluation
    output_format : markdown | plain_text | csv
    instructions  : instrucciones adicionales del usuario (puede estar vacío)
    evidence      : lista de dicts con doc_id, title, page, chunk_id, text
    report_name   : nombre/título del reporte
    """
    type_prompt = _PROMPT_MAP.get(report_type, DESCRIPTIVE_PROMPT)
    format_rules = _FORMAT_RULES.get(output_format, _FORMAT_RULES["markdown"])

    system_content = f"{type_prompt}\n\n{format_rules}"

    user_payload = {
        "report_name": report_name,
        "instructions": instructions or "",
        "evidence": evidence,
    }

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]
