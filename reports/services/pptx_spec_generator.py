import json
from typing import Any, Dict, List
import re
from pydantic import ValidationError
from reports.schemas.presentation_spec import PresentationSpec
from reports.services.ollama_client import ollama_chat
from fileuploads.views import optimized_rag_search_files
from collections import defaultdict, Counter
from typing import Optional

SYSTEM_PROMPT = """
Eres un generador de JSON ESTRICTO. Devuelve SOLO JSON válido (sin markdown, sin texto extra).

Recibirás un JSON con:
- report_name
- report_type
- guided_prompt
- evidence: lista de fragmentos con {doc_id,title,page,chunk_id,text}
- file_ids

Debes producir EXACTAMENTE:

{
  "title": "string",
  "subtitle": "string (opcional)",
  "filename": "string (opcional)",
  "slides": [
    {"layout":"title","title":"string","subtitle":"string"},
    {"layout":"bullets","title":"string","bullets":["..."],"notes":"string (opcional)"},
    {"layout":"two_columns","title":"string",
      "left":{"heading":"...","bullets":["..."]},
      "right":{"heading":"...","bullets":["..."]},
      "notes":"string (opcional)"
    },
    {"layout":"sources","title":"string","sources":[{"name":"...","detail":"..."}]}
  ]
}

REGLAS OBLIGATORIAS:
1) title (raíz) DEBE ser exactamente report_name.
2) En el slide layout="title", el campo "title" DEBE ser exactamente report_name.
3) Usa SOLO información presente en evidence. NO uses conocimiento externo.
4) CITAS Y EVIDENCIA:
- NO incluyas citas en los bullets.
- NO generes referencias como [file_id=...] dentro del texto.
- El sistema añadirá las citas automáticamente después.
- Tu tarea es SOLO generar contenido basado en evidence.
5) USO DE EVIDENCIA:
- Usa únicamente la información presente en evidence.
- NO inventes información externa.
- NO completes con conocimiento general si no está en evidence.
6) En slides layout="bullets":
   - máximo 6 bullets
   - cada bullet máximo 28 palabras o una frase corta
   - bullets nunca debe ir vacío
7) Si NO hay evidencia suficiente para un punto:
   - NO lo inventes
   - usa un bullet: "Evidencia insuficiente en los documentos proporcionados. [file_id=<doc_id> chunk=<chunk_id>]"
     con un doc_id/chunk_id REAL de evidence
   - puedes ampliar en notes (opcional)
8) La presentación debe incluir:
   - 1 slide de título (obligatorio)
   - 1 o más slides de contenido (layout="bullets" o "two_columns")
   - 1 slide final de fuentes (obligatorio)
9) Si el usuario NO especifica un número de diapositivas, el número total de slides debe ser el mínimo necesario para responder la solicitud.
No agregues slides adicionales que no estén directamente relacionadas con la solicitud del usuario.
10) Si la solicitud del usuario es simple o puntual, responde con una estructura breve.
11) filename debe terminar en .pptx, ser corto y no incluir rutas.
12) COBERTURA DE CONTENIDO:
- Si la solicitud del usuario es amplia (por ejemplo: "explicar", "resumir", "presentar"),
  debes cubrir los temas principales presentes en la evidencia.
- No concentres todas las diapositivas en un solo fragmento o sección si existen múltiples temas en evidence.
- Identifica los temas o secciones más recurrentes en evidence y distribuye las diapositivas entre ellos de forma equilibrada.
13) ORGANIZACIÓN DE LA PRESENTACIÓN:
- Cada slide debe representar una idea o tema distinto.
- Evita repetir contenido similar en múltiples slides.
- Agrupa ideas relacionadas en un mismo slide cuando sea posible.
14) USO DE EVIDENCIA:
- Prioriza usar evidencia de diferentes fragmentos (chunks) cuando sea posible.
- Evita generar múltiples bullets basados en el mismo chunk si hay otros disponibles.
15) CALIDAD DE RESUMEN:
- Resume y parafrasea la información; no copies texto literal largo.
- Los bullets deben representar ideas claras y diferenciadas.
16) Si la evidencia contiene múltiples secciones o partes del documento, intenta cubrir al menos 3 temas distintos en 
la presentación cuando sea posible.
17) CONTROL DE NÚMERO DE DIAPOSITIVAS (CRÍTICO):
- Si el usuario especifica un número exacto de diapositivas, este requisito es OBLIGATORIO y tiene prioridad sobre todas las demás reglas.
- Debes generar EXACTAMENTE ese número de diapositivas, ni más ni menos.
- Si falta contenido, divide los temas en múltiples slides.
- Si sobra contenido, agrupa los temas.
- No puedes ignorar esta regla bajo ninguna circunstancia.
18) USO DE TEMAS:
- Si se proporcionan "topics", debes usarlos como guía principal para estructurar la presentación.
- Cada slide debe corresponder a uno de los topics cuando sea posible.
- No ignores los topics proporcionados, salvo que no exista evidencia suficiente para desarrollarlos.
19) FORMATO DE BULLETS (OBLIGATORIO Y PRIORITARIO):
- TODOS los bullets deben seguir estrictamente el formato: "Idea principal: breve explicación".
- Ningún bullet puede ser solo una frase incompleta o un encabezado.
- Si un bullet no tiene descripción, se considera inválido.
- La descripción debe ser de una sola oración breve y clara.
- Este formato es obligatorio incluso si la información es limitada.
20) CONSISTENCIA DE BULLETS:
- No generes bullets que sean títulos genéricos o encabezados (ej: "Recolección de datos", "Análisis de datos").
- Cada bullet debe ser una idea completa y explicativa.
21) VALIDACIÓN INTERNA:
- Antes de devolver el resultado, verifica que TODOS los bullets contienen ":" separando idea y explicación.
- Si algún bullet no cumple este formato, corrígelo antes de responder.
22) RELACIÓN ENTRE IDEA Y DESCRIPCIÓN:
- La parte antes de ":" debe ser un concepto claro y breve.
- La parte después de ":" debe ampliar o explicar ese concepto, no repetirlo.
Devuelve SOLO JSON válido.
23) BALANCE ENTRE DOCUMENTOS:
- Si la evidencia proviene de múltiples documentos (diferentes file_id), debes utilizar contenido de al menos dos documentos distintos en la presentación.
- Distribuye las diapositivas de forma equilibrada entre los documentos cuando sea posible.
- Evita que un solo documento domine la mayoría de las diapositivas si existen otros con evidencia relevante.
24) COHERENCIA TEMÁTICA:
- Cada diapositiva debe mantenerse dentro de un mismo nivel conceptual (por ejemplo: procesos físicos, impactos, soluciones).
- No mezcles en una misma diapositiva conceptos de diferentes niveles (ej: procesos del ciclo del agua con políticas públicas).
- Organiza las diapositivas de lo general a lo específico o de lo básico a lo aplicado.
""".strip()

REPAIR_PROMPT = """
El contenido siguiente no es JSON válido o no cumple el esquema. Corrígelo.
Devuelve SOLO JSON válido.

Contenido:
""".strip()

_CITE_RE = re.compile(r"\[file_id=\d+\s+chunk=[^\]]+\]")
def _bullet_has_cite(b: str) -> bool:
    return bool(_CITE_RE.search(b or ""))

def _default_cite(evidence: List[Dict[str, Any]]) -> str:
    """
    Usa una cita real (primer snippet) para evitar placeholders falsos.
    """
    if not evidence:
        return "[file_id=0 chunk=0]"
    e = evidence[0]
    return f"[file_id={e.get('doc_id')} chunk={e.get('chunk_id')}]"

def _ensure_nonempty_title(s: Dict[str, Any], fallback: str) -> None:
    if not (s.get("title") or "").strip():
        s["title"] = fallback

_CITE_RE = re.compile(r"\[file_id=[^\]]+?\s+chunk=[^\]]+?\]")
def _extract_cites(text: str) -> List[str]:
    if not text:
        return []
    return _CITE_RE.findall(text)

def _attach_best_cites_to_bullets(spec_dict: Dict[str, Any], evidence: List[Dict[str, Any]]) -> None:
    """
    Asigna una cita real a cada bullet basado en similitud simple de texto.
    """

    def normalize(text: str) -> str:
        return (text or "").lower()

    for slide in spec_dict.get("slides", []):
        layout = slide.get("layout")

        if layout == "bullets":
            new_bullets = []
            for b in slide.get("bullets", []):
                best_e = None
                best_score = 0

                b_text = normalize(b)

                for e in evidence:
                    e_text = normalize(e.get("text", ""))

                    # scoring muy simple (puedes mejorar después)
                    score = sum(1 for w in b_text.split() if w in e_text)

                    if score > best_score:
                        best_score = score
                        best_e = e

                if best_e:
                    cite = f"[file_id={best_e['doc_id']} chunk={best_e['chunk_id']}]"
                    new_bullets.append(f"{b} {cite}")
                else:
                    new_bullets.append(b)

            slide["bullets"] = new_bullets

        elif layout == "two_columns":
            for side in ("left", "right"):
                col = slide.get(side) or {}
                new_bullets = []

                for b in col.get("bullets", []):
                    best_e = None
                    best_score = 0

                    b_text = normalize(b)

                    for e in evidence:
                        e_text = normalize(e.get("text", ""))

                        score = sum(1 for w in b_text.split() if w in e_text)

                        if score > best_score:
                            best_score = score
                            best_e = e

                    if best_e:
                        cite = f"[file_id={best_e['doc_id']} chunk={best_e['chunk_id']}]"
                        new_bullets.append(f"{b} {cite}")
                    else:
                        new_bullets.append(b)

                col["bullets"] = new_bullets
                slide[side] = col

def _sanitize_slides_in_place(spec_dict: Dict[str, Any], *, default_cite: str) -> None:
    """
    - Conserva bullets no vacíos.
    - Si un bullet no tiene cita, intenta reutilizar una cita presente en notes.
    - NO agrega default_cite a todo bullet normal sin cita.
    - Solo usa default_cite para placeholders reales ("evidencia insuficiente").
    - Para two_columns: misma lógica para left/right.
    - Evita slides en blanco.
    """
    slides = spec_dict.get("slides") or []

    for s in slides:
        layout = s.get("layout")

        if layout == "bullets":
            _ensure_nonempty_title(s, "Contenido")

            notes = (s.get("notes") or "").strip()
            notes_cites = _extract_cites(notes)
            slide_fallback_cite = notes_cites[0] if notes_cites else None

            bullets = [str(x).strip() for x in (s.get("bullets") or []) if str(x).strip()]

            kept = []
            for b in bullets:
                if _bullet_has_cite(b):
                    kept.append(b)
                elif slide_fallback_cite:
                    kept.append(f"{b} {slide_fallback_cite}")
                else:
                    # conserva el contenido útil SIN inventar una cita incorrecta
                    kept.append(b)

            if not kept:
                if notes:
                    if _bullet_has_cite(notes):
                        kept = [notes]
                    elif slide_fallback_cite:
                        kept = [f"{notes} {slide_fallback_cite}"]
                    else:
                        kept = [f"{notes} {default_cite}"]
                else:
                    kept = [f"Evidencia insuficiente en los documentos proporcionados. {default_cite}"]

            s["bullets"] = kept

        elif layout == "two_columns":
            _ensure_nonempty_title(s, "Comparativo")

            notes = (s.get("notes") or "").strip()
            notes_cites = _extract_cites(notes)
            slide_fallback_cite = notes_cites[0] if notes_cites else None

            for side, fallback_heading in (("left", "Sección A"), ("right", "Sección B")):
                col = s.get(side) or {}

                if not (col.get("heading") or "").strip():
                    col["heading"] = fallback_heading

                bullets = [str(x).strip() for x in (col.get("bullets") or []) if str(x).strip()]

                kept = []
                for b in bullets:
                    if _bullet_has_cite(b):
                        kept.append(b)
                    elif slide_fallback_cite:
                        kept.append(f"{b} {slide_fallback_cite}")
                    else:
                        kept.append(b)

                if not kept:
                    kept = [f"Evidencia insuficiente en los documentos proporcionados. {default_cite}"]

                col["bullets"] = kept
                s[side] = col

def _build_pptx_queries(report_name: str, guided_prompt: str, report_type: str = "") -> List[str]:
    """
    Construye queries orientadas a contenido, no a formato.
    Elimina ruido típico de instrucciones de presentación y genera
    variaciones semánticas generales para mejorar la cobertura del RAG.
    """
    import re
    print(f"[PPTX] USING _build_pptx_queries V2")
    print(f"[PPTX] raw guided_prompt={guided_prompt}")

    raw = " ".join(
        part.strip() for part in [report_name, guided_prompt, report_type]
        if part and part.strip()
    ).strip()

    cleaned = raw.lower()

    # Quitar instrucciones de formato / salida
    noise_patterns = [
        r"genera una presentación(?: en powerpoint)?",
        r"crear una presentación(?: en powerpoint)?",
        r"haz una presentación(?: en powerpoint)?",
        r"presentación en powerpoint",
        r"powerpoint",
        r"pptx",
        r"con exactamente \d+ diapositivas",
        r"exactamente \d+ diapositivas",
        r"\d+ diapositivas",
        r"usa viñetas claras",
        r"usa viñetas",
        r"con viñetas claras",
        r"basada en el documento proporcionado",
        r"basada en los documentos proporcionados",
        r"integrando información de ambos documentos",
        r"integrando información de los documentos",
        r"organiza la presentación",
        r"distribuye el contenido",
        r"\bpresentation\b",
    ]

    for pat in noise_patterns:
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,-")

    # Tema base
    thematic_base = cleaned or raw

    # Variaciones semánticas generales
    candidates = [
        thematic_base,
        f"{thematic_base} temas principales conceptos clave",
        f"{thematic_base} contexto ideas principales",
        f"{thematic_base} causas efectos implicaciones",
        f"{thematic_base} hallazgos recomendaciones conclusiones",
    ]

    # Deduplicar conservando orden
    seen = set()
    queries = []
    for q in candidates:
        q = re.sub(r"\s+", " ", q).strip(" .,-")
        if q and q not in seen:
            seen.add(q)
            queries.append(q)

    return queries[:5]

def _rewrite_queries_for_rag(report_name: str, guided_prompt: str, report_type: str = "") -> List[str]:
    print("[PPTX] USING _rewrite_queries_for_rag")
    print(f"[PPTX] rewrite input report_name={report_name}")
    print(f"[PPTX] rewrite input guided_prompt={guided_prompt}")

    payload = {
        "report_name": report_name,
        "guided_prompt": guided_prompt,
        "report_type": report_type,
    }

    prompt = """
Eres un asistente que convierte solicitudes de usuario en queries limpias para búsqueda semántica sobre documentos.

Tu tarea:
1) Identificar el tema principal de la solicitud.
2) Ignorar instrucciones de formato o salida, por ejemplo:
   - generar una presentación
   - PowerPoint / pptx
   - número de diapositivas
   - viñetas
   - estilo de salida
3) Devolver entre 3 y 5 queries centradas SOLO en el contenido temático.
4) Mantener el idioma predominante de la solicitud.
5) Las queries deben ser útiles para recuperar fragmentos relevantes de documentos.

Devuelve SOLO JSON válido con este formato:

{
  "topic": "string",
  "queries": ["q1", "q2", "q3"]
}
""".strip()

    raw = ollama_chat(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.0,
        think=False,
    )

    data = _parse_or_repair(raw)

    queries = data.get("queries", [])
    if not isinstance(queries, list):
        print("[PPTX] rewrite_queries invalid format")
        return []

    clean_queries = []
    seen = set()
    for q in queries:
        q = str(q).strip()
        if q and q not in seen:
            seen.add(q)
            clean_queries.append(q)

    print(f"[PPTX] rewrite_queries output: {clean_queries}")

    return clean_queries[:5]

def _extract_and_order_topics(evidence: List[Dict[str, Any]]) -> List[str]:
    print(f"[PPTX] USING conservative _extract_and_order_topics, evidence_count={len(evidence)}")
    evidence_count = len(evidence)

    if evidence_count <= 6:
        min_topics, max_topics = 3, 4
    elif evidence_count <= 10:
        min_topics, max_topics = 4, 4
    elif evidence_count <= 14:
        min_topics, max_topics = 4, 5
    else:
        min_topics, max_topics = 5, 6

    print(f"[PPTX] topic limits: min_topics={min_topics}, max_topics={max_topics}")

    payload = {
        "task": "extract_and_order_topics",
        "evidence_count": evidence_count,
        "evidence": [
            {
                "doc_id": e.get("doc_id"),
                "title": e.get("title"),
                "text": e.get("text", "")[:500],
            }
            for e in evidence[:20]
        ]
    }

    prompt = f"""
Eres un asistente que identifica y ordena los temas principales de uno o varios documentos.

A partir de los fragmentos proporcionados:

1) Detecta entre {min_topics} y {max_topics} temas principales.
2) Ordénalos en una secuencia lógica típica de una presentación.
3) Si hay fragmentos de múltiples documentos, procura incluir temas representativos de más de un documento cuando sea posible.

Ejemplo de orden lógico:
- Introducción / conceptos básicos
- Contexto o marco general
- Procesos o metodología
- Resultados / impactos
- Discusión / implicaciones
- Conclusiones
- Referencias

Reglas:
- No repitas temas.
- Usa nombres claros, generales y amplios.
- Evita temas demasiado específicos si no están claramente respaldados por varios fragmentos.
- Si la evidencia es limitada, prefiere menos temas y más generales.
- Si hay múltiples documentos, prioriza temas comunes o complementarios entre ellos.
- Respeta el idioma predominante del contenido.
- Ordena los temas de lo básico a lo aplicado, o de lo general a lo específico.
- Si aparece "Introducción" o un tema conceptual básico, debe ir al inicio cuando corresponda.
- Si aparecen "Referencias" o "Bibliografía", deben ir SIEMPRE al final.

Devuelve SOLO JSON válido:

{{
  "topics": ["tema1", "tema2", "tema3"]
}}
""".strip()

    raw = ollama_chat(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.0,
        think=False,
    )

    data = _parse_or_repair(raw)
    topics = data.get("topics", [])

    if not isinstance(topics, list):
        return []

    clean_topics = [str(t).strip() for t in topics if str(t).strip()]

    print(f"[PPTX] raw topics from extractor: {topics}")
    print(f"[PPTX] final trimmed topics: {clean_topics[:max_topics]}")

    return clean_topics[:max_topics]

def _extract_requested_slide_count(guided_prompt: str) -> Optional[int]:
    if not guided_prompt:
        return None

    text = guided_prompt.lower()

    patterns = [
        r"exactamente\s+(\d+)\s+diapositivas",
        r"(\d+)\s+diapositivas",
        r"exactamente\s+(\d+)\s+slides",
        r"(\d+)\s+slides",
    ]

    for pat in patterns:
        m = re.search(pat, text, flags=re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None

    return None

def _rebalance_chunks_by_file(chunks: List[Any], top_k: int) -> List[Any]:
    if not chunks:
        return []

    file_groups: Dict[Any, List[Any]] = defaultdict(list)
    ordered_file_ids: List[Any] = []

    for ch in chunks:
        fid = getattr(ch, "file_id", None)
        if fid not in file_groups:
            ordered_file_ids.append(fid)
        file_groups[fid].append(ch)

    valid_file_ids = [fid for fid in ordered_file_ids if fid is not None]
    if not valid_file_ids:
        return chunks[:top_k]

    balanced: List[Any] = []
    round_idx = 0

    while len(balanced) < top_k:
        added_this_round = False

        for fid in valid_file_ids:
            group = file_groups[fid]
            if round_idx < len(group) and group[round_idx] not in balanced:
                balanced.append(group[round_idx])
                added_this_round = True
                if len(balanced) >= top_k:
                    break

        if not added_this_round:
            break

        round_idx += 1

    return balanced[:top_k]

# def _ensure_requested_slide_count(spec_dict: Dict[str, Any], requested_count: Optional[int], default_cite: str) -> None:
#     if not requested_count or requested_count < 3:
#         return

#     slides = spec_dict.get("slides", [])
#     if not slides:
#         return

#     title_slides = [s for s in slides if s.get("layout") == "title"]
#     sources_slides = [s for s in slides if s.get("layout") == "sources"]
#     content_slides = [s for s in slides if s.get("layout") in ("bullets", "two_columns")]

#     target_content_count = max(1, requested_count - len(title_slides) - len(sources_slides))

#     if len(content_slides) == target_content_count:
#         return

#     if len(content_slides) < target_content_count:
#         missing = target_content_count - len(content_slides)
#         for i in range(missing):
#             content_slides.append({
#                 "layout": "bullets",
#                 "title": f"Contenido complementario {i + 1}",
#                 "bullets": [f"Evidencia insuficiente en los documentos proporcionados: no fue posible completar esta diapositiva. {default_cite}"],
#             })

#     elif len(content_slides) > target_content_count:
#         content_slides = content_slides[:target_content_count]

#     rebuilt = []
#     title_added = False
#     sources_added = False

#     for s in slides:
#         if s.get("layout") == "title" and not title_added:
#             rebuilt.append(s)
#             title_added = True

#     rebuilt.extend(content_slides)

#     for s in slides:
#         if s.get("layout") == "sources" and not sources_added:
#             rebuilt.append(s)
#             sources_added = True

#     spec_dict["slides"] = rebuilt

# def generate_presentation_spec(
#     *,
#     report_name: str,
#     report_type: str,
#     file_ids: List[int],
#     guided_prompt: str = "",
#     top_k: int = 20,
# ) -> Dict[str, Any]:
#     print(f"[PPTX] START generate_presentation_spec top_k={top_k}, file_ids={file_ids}")

#     queries = _rewrite_queries_for_rag(report_name, guided_prompt, report_type)

#     # fallback si falla el LLM
#     if not queries:
#         print("[PPTX] fallback to _build_pptx_queries")
#         queries = _build_pptx_queries(report_name, guided_prompt, report_type)

#     print(f"[PPTX] rag_queries: {queries}")

#     chunks = []
#     per_query_k = max(4, int(top_k / max(1, len(queries))))
#     print(f"[PPTX] per_query_k={per_query_k}, num_queries={len(queries)}")

#     for q in queries:
#         print(f"[PPTX] rag_query_part: {q}")

#         partial = optimized_rag_search_files(
#             file_ids=file_ids,
#             query=q,
#             top_k=per_query_k,
#         )

#         chunks.extend(partial)

#     # deduplicar por documento + chunk
#     seen = set()
#     dedup_chunks = []

#     for ch in chunks:
#         key = (
#             getattr(ch, "file_id", None),
#             getattr(ch, "chunk_index", None),
#         )

#         if key in seen:
#             continue

#         seen.add(key)
#         dedup_chunks.append(ch)

#     dedup_file_counts = Counter(getattr(ch, "file_id", None) for ch in dedup_chunks)
#     print(f"[PPTX] dedup_chunks total={len(dedup_chunks)}, by_file={dict(dedup_file_counts)}")

#     # rebalancear por documento antes del recorte final
#     chunks = _rebalance_chunks_by_file(dedup_chunks, top_k=top_k)

#     final_file_counts = Counter(getattr(ch, "file_id", None) for ch in chunks)
#     print(f"[PPTX] final chunks total={len(chunks)}, by_file={dict(final_file_counts)}")

#     print(f"[PPTX] chunks from RAG: {len(chunks)}")

#     # 2) chunks -> evidence
#     evidence: List[Dict[str, Any]] = []
#     for ch in chunks:
#         meta = getattr(ch, "metadata_json", None) or {}
#         evidence.append({
#             "doc_id": getattr(ch, "file_id", None),
#             "title": getattr(getattr(ch, "file", None), "filename", "") or "",
#             "page": meta.get("page"),
#             "chunk_id": f"{getattr(ch, 'file_id', 'x')}-{getattr(ch, 'chunk_index', 'x')}",
#             "text": getattr(ch, "text", "") or "",
#         })

#     print(f"[PPTX] evidence items: {len(evidence)}")

#     evidence_file_counts = Counter(e.get("doc_id") for e in evidence)
#     print(f"[PPTX] evidence by file: {dict(evidence_file_counts)}")

#     if evidence:
#         print("[PPTX] evidence[0] preview:", (evidence[0].get("text") or "")[:200])

#     requested_slide_count = _extract_requested_slide_count(guided_prompt)
#     print(f"[PPTX] requested_slide_count: {requested_slide_count}")

#     topics = _extract_and_order_topics(evidence)
#     print(f"[PPTX] detected topics: {topics}")

#     user_payload = {
#         "report_name": report_name,
#         "report_type": report_type,
#         "guided_prompt": guided_prompt,
#         "file_ids": file_ids,
#         "topics": topics,
#         "evidence": evidence[:30],
#     }

#     # 3) LLM
#     raw = ollama_chat(
#         messages=[
#             {"role": "system", "content": SYSTEM_PROMPT},
#             {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
#         ],
#         temperature=0.0,
#         think=False,
#     )

#     data = _parse_or_repair(raw)

#     # 4) Validar schema
#     try:
#         spec = PresentationSpec.model_validate(data)
#     except ValidationError:
#         repaired = _parse_or_repair(json.dumps(data, ensure_ascii=False))
#         spec = PresentationSpec.model_validate(repaired)

#     spec_dict = spec.model_dump()

#     # 5) HARD OVERRIDE: título siempre = report_name
#     spec_dict["title"] = report_name

#     if spec_dict.get("slides"):
#         title_slide = None
#         for s in spec_dict["slides"]:
#             if s.get("layout") == "title":
#                 title_slide = s
#                 break

#         if title_slide is None:
#             spec_dict["slides"].insert(0, {
#                 "layout": "title",
#                 "title": report_name,
#                 "subtitle": ""
#             })
#         else:
#             title_slide["title"] = report_name
#             title_slide.setdefault("subtitle", "")
#     else:
#         spec_dict["slides"] = [{
#             "layout": "title",
#             "title": report_name,
#             "subtitle": ""
#         }]

#     # 6) filename seguro
#     if not spec_dict.get("filename"):
#         spec_dict["filename"] = "report.pptx"
#     if not spec_dict["filename"].lower().endswith(".pptx"):
#         spec_dict["filename"] = spec_dict["filename"] + ".pptx"
#     spec_dict["filename"] = spec_dict["filename"].replace("/", "_").replace("\\", "_")

#     # 7) Sanitize: filtra “de más” y evita slides en blanco
#     default_cite = _default_cite(evidence)
#     _sanitize_slides_in_place(spec_dict, default_cite=default_cite)

#     _attach_best_cites_to_bullets(spec_dict, evidence)

#     # 8) Construir sources desde evidence (solo evidencia real)
#     unique_sources: Dict[tuple, Dict[str, str]] = {}
#     for e in evidence:
#         doc_id = e.get("doc_id")
#         title = e.get("title") or ""
#         key = (doc_id, title)

#         if key not in unique_sources:
#             detail_parts = [f"file_id={doc_id}"]
#             if e.get("page"):
#                 detail_parts.append(f"page={e.get('page')}")
#             if e.get("chunk_id"):
#                 detail_parts.append(f"chunk_id={e.get('chunk_id')}")

#             unique_sources[key] = {
#                 "name": title or f"file_id:{doc_id}",
#                 "detail": ", ".join(detail_parts),
#             }

#     sources_slide = {
#         "layout": "sources",
#         "title": "Fuentes consultadas",
#         "sources": list(unique_sources.values()) or [
#             {"name": "Sin fuentes", "detail": "No se recuperó evidencia"}
#         ],
#     }

#     # 9) Reemplazar cualquier sources inventado por el LLM
#     slides = spec_dict.get("slides", [])
#     slides = [s for s in slides if s.get("layout") != "sources"]
#     slides.append(sources_slide)
#     spec_dict["slides"] = slides

#     # 9.5) Forzar número de slides si el usuario lo pidió explícitamente
#     _ensure_requested_slide_count(
#     spec_dict,
#     requested_count=requested_slide_count,
#     default_cite=default_cite,
# )

#     # 10) Garantiza estructura mínima lógica:
#     # title + al menos 1 slide de contenido + sources
#     slides_no_sources = [s for s in spec_dict["slides"] if s.get("layout") != "sources"]

#     has_title = any(s.get("layout") == "title" for s in slides_no_sources)
#     has_content = any(s.get("layout") in ("bullets", "two_columns") for s in slides_no_sources)

#     if not has_title:
#         spec_dict["slides"].insert(0, {
#             "layout": "title",
#             "title": report_name,
#             "subtitle": ""
#         })

#     if not has_content:
#         spec_dict["slides"].insert(
#             -1,
#             {
#                 "layout": "bullets",
#                 "title": "Contenido",
#                 "bullets": [f"Evidencia insuficiente en los documentos proporcionados. {default_cite}"],
#             }
#         )

#     return spec_dict

def _fit_topics_to_slide_count(topics: List[str], content_slide_count: int) -> List[str]:
    topics = [str(t).strip() for t in (topics or []) if str(t).strip()]

    if content_slide_count <= 0:
        return []

    if not topics:
        return ["Contenido principal"] * content_slide_count

    if len(topics) >= content_slide_count:
        return topics[:content_slide_count]

    def expand_topic(topic: str) -> List[str]:
        """
        Genera variantes genéricas de un topic sin depender del dominio.
        """
        return [
            f"{topic}: conceptos principales",
            f"{topic}: elementos clave",
            f"{topic}: implicaciones",
            f"{topic}: aplicaciones",
            f"{topic}: análisis",
        ]

    fitted = topics[:]
    idx = 0
    base_topics = topics[:]

    while len(fitted) < content_slide_count:
        base = base_topics[idx % len(base_topics)]
        candidates = expand_topic(base)

        added = False
        for c in candidates:
            if c not in fitted:
                fitted.append(c)
                added = True
                break

        if not added:
            fitted.append(f"{base}: desarrollo")

        idx += 1

    return fitted[:content_slide_count][:content_slide_count]

def _select_best_evidence_for_topic(
    topic: str,
    evidence: List[Dict[str, Any]],
    max_items: int = 2,
) -> List[Dict[str, Any]]:
    topic_l = (topic or "").lower()
    topic_words = set(topic_l.split())

    scored = []

    for e in evidence:
        text = (e.get("text") or "").lower()
        if not text:
            continue

        text_words = set(text.split())

        overlap = len(topic_words & text_words)

        # bonus por densidad temática
        density_bonus = overlap / max(1, len(topic_words))

        # penalización leve por chunks demasiado largos y difusos
        length_penalty = min(len(text.split()) / 1000.0, 0.5)

        score = overlap + density_bonus - length_penalty

        if score > 0:
            scored.append((score, e))

    scored.sort(key=lambda x: x[0], reverse=True)

    selected = [e for _, e in scored[:max_items]]

    if not selected:
        selected = evidence[:max_items]

    return selected

def _generate_bullets_for_topic(
    topic: str,
    topic_evidence: List[Dict[str, Any]],
) -> List[str]:
    if not topic_evidence:
        return ["No hay evidencia suficiente para desarrollar este tema."]

    payload = {
        "topic": topic,
        "evidence": [
            {"text": (e.get("text") or "")[:700]}
            for e in topic_evidence
        ]
    }

    prompt = """
Eres un generador de JSON ESTRICTO.

Tu tarea:
- resumir la evidencia en 2 o 3 bullets claros
- usar SOLO la información presente en evidence
- NO inventar información
- NO usar conocimiento externo
- NO incluir citas
- NO usar prefijos como "Idea principal:"
- cada bullet debe ser una frase clara y completa
- todos los bullets deben estar estrictamente enfocados en el topic
- evita mezclar ideas de subtemas diferentes en el mismo bullet

Devuelve SOLO JSON válido:

{
  "bullets": [
    "texto del bullet",
    "texto del bullet"
  ]
}
""".strip()

    raw = ollama_chat(
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        temperature=0.0,
        think=False,
    )

    data = _parse_or_repair(raw)
    bullets = data.get("bullets", [])

    if not isinstance(bullets, list):
        return ["No hay evidencia suficiente para desarrollar este tema."]

    clean = [str(b).strip() for b in bullets if str(b).strip()]

    return clean[:3] or ["No hay evidencia suficiente para desarrollar este tema."]

def generate_presentation_spec_v2(
    *,
    report_name: str,
    report_type: str,
    file_ids: List[int],
    guided_prompt: str = "",
    top_k: int = 20,
) -> Dict[str, Any]:

    print(f"[PPTX V2] STARTING to generate_presentation_spec_v2 top_k={top_k}, file_ids={file_ids}")

    # -------------------------
    # 1) MULTI-QUERY RAG
    # -------------------------
    queries = _rewrite_queries_for_rag(report_name, guided_prompt, report_type)

    if not queries:
        print("[PPTX V2] fallback to _build_pptx_queries")
        queries = _build_pptx_queries(report_name, guided_prompt, report_type)

    print(f"[PPTX V2] rag_queries: {queries}")

    chunks = []
    per_query_k = max(4, int(top_k / max(1, len(queries))))
    print(f"[PPTX V2] per_query_k={per_query_k}, num_queries={len(queries)}")

    for q in queries:
        print(f"[PPTX V2] rag_query_part: {q}")

        partial = optimized_rag_search_files(
            file_ids=file_ids,
            query=q,
            top_k=per_query_k,
        )
        chunks.extend(partial)

    # dedup
    seen = set()
    dedup_chunks = []
    for ch in chunks:
        key = (getattr(ch, "file_id", None), getattr(ch, "chunk_index", None))
        if key in seen:
            continue
        seen.add(key)
        dedup_chunks.append(ch)

    chunks = _rebalance_chunks_by_file(dedup_chunks, top_k=top_k)

    from collections import Counter
    chunk_counts = Counter(getattr(ch, "file_id", None) for ch in chunks)
    print(f"[PPTX V2] final chunks: {len(chunks)}, by_file={dict(chunk_counts)}")

    # -------------------------
    # 2) EVIDENCE
    # -------------------------
    evidence = []
    for ch in chunks:
        meta = getattr(ch, "metadata_json", None) or {}
        evidence.append({
            "doc_id": getattr(ch, "file_id", None),
            "title": getattr(getattr(ch, "file", None), "filename", "") or "",
            "page": meta.get("page"),
            "chunk_id": f"{getattr(ch, 'file_id', 'x')}-{getattr(ch, 'chunk_index', 'x')}",
            "text": getattr(ch, "text", "") or "",
        })

    evidence_counts = Counter(e.get("doc_id") for e in evidence)
    print(f"[PPTX V2] evidence items: {len(evidence)}, by_file={dict(evidence_counts)}")

    if evidence:
        print("[PPTX V2] evidence[0] preview:", (evidence[0].get("text") or "")[:200])

    # -------------------------
    # 3) TOPICS
    # -------------------------
    topics = _extract_and_order_topics(evidence)

    if not topics:
        topics = ["Contenido principal"]

    print(f"[PPTX V2] topics: {topics}")

    requested_slide_count = _extract_requested_slide_count(guided_prompt)
    total_slides = requested_slide_count or 8

    content_slide_count = max(1, total_slides - 2)  # title + sources

    fitted_topics = _fit_topics_to_slide_count(topics, content_slide_count)
    print(f"[PPTX V2] fitted_topics: {fitted_topics}")

    # -------------------------
    # 4) BUILD SLIDES
    # -------------------------
    slides = []

    # TITLE
    slides.append({
        "layout": "title",
        "title": report_name,
        "subtitle": "",
    })

    # CONTENT
    for topic in fitted_topics:
        topic_evidence = _select_best_evidence_for_topic(topic, evidence, max_items=2)

        bullets = _generate_bullets_for_topic(topic, topic_evidence)

        slides.append({
            "layout": "bullets",
            "title": topic,
            "bullets": bullets,
        })

    # -------------------------
    # 5) BUILD SPEC
    # -------------------------
    spec_dict = {
        "title": report_name,
        "subtitle": "",
        "filename": "report.pptx",
        "slides": slides,
    }

    # -------------------------
    # 6) SANITIZE
    # -------------------------
    default_cite = _default_cite(evidence)
    _sanitize_slides_in_place(spec_dict, default_cite=default_cite)

    # -------------------------
    # 7) ATTACH REAL CITES
    # -------------------------
    _attach_best_cites_to_bullets(spec_dict, evidence)

    # -------------------------
    # 8) SOURCES (SIN DUPLICADOS POR CHUNK)
    # -------------------------
    unique_sources: Dict[tuple, Dict[str, str]] = {}

    for e in evidence:
        doc_id = e.get("doc_id")
        title = e.get("title") or ""
        key = (doc_id, title)

        if key not in unique_sources:
            detail_parts = [f"file_id={doc_id}"]

            if e.get("page"):
                detail_parts.append(f"page={e.get('page')}")

            unique_sources[key] = {
                "name": title or f"file_id:{doc_id}",
                "detail": ", ".join(detail_parts),
            }

    sources_slide = {
        "layout": "sources",
        "title": "Fuentes consultadas",
        "sources": list(unique_sources.values()) or [
            {"name": "Sin fuentes", "detail": "No se recuperó evidencia"}
        ],
    }

    spec_dict["slides"].append(sources_slide)

    print(f"[PPTX V2] FINAL slides: {len(spec_dict['slides'])}")

    return spec_dict

def _extract_json_candidate(s: str) -> str | None:
    """
    Intenta rescatar el primer objeto/array JSON dentro de un texto.
    Soporta casos tipo:
    - 
json { ... } 
    - texto antes/después
    """
    if not s:
        return None

    s = s.strip()

    # Caso ideal: ya es JSON puro
    if s.startswith("{") or s.startswith("["):
        return s

    # 1) bloque objeto
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if m:
        return m.group(0).strip()

    # 2) bloque array
    m = re.search(r"\[.*\]", s, flags=re.DOTALL)
    if m:
        return m.group(0).strip()

    return None

def _loads_json_loose(s: str) -> Dict[str, Any]:
    cand = _extract_json_candidate(s)
    if not cand:
        raise json.JSONDecodeError("No JSON candidate found", s, 0)
    return json.loads(cand)


def _parse_or_repair(text: str) -> Dict[str, Any]:
    raw = (text or "").strip()

    # LOG mínimo
    print("[PPTX] raw_from_ollama (first 400 chars):", raw[:400].replace("\n", "\\n"))

    # 1) Intento directo
    try:
        return _loads_json_loose(raw)
    except json.JSONDecodeError:
        pass

    # 2) Intento reparación vía LLM
    fixed = ollama_chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": REPAIR_PROMPT + "\n" + raw},
        ],
        temperature=0.0,
        think=False,  # IMPORTANTE: evita thinking en respuesta
    )

    fixed_str = (fixed or "").strip()
    print("[PPTX] fixed_from_ollama (first 400 chars):", fixed_str[:400].replace("\n", "\\n"))

    # 3) Parse final
    return _loads_json_loose(fixed_str)

