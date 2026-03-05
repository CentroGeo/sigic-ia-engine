import json
from typing import Any, Dict, List
import re

from pydantic import ValidationError

from reports.schemas.presentation_spec import PresentationSpec
from reports.services.ollama_client import ollama_chat
from fileuploads.views import optimized_rag_search_files


# SYSTEM_PROMPT = """
# Eres un generador de JSON ESTRICTO. Devuelve SOLO JSON válido (sin texto extra, sin markdown).

# Vas a recibir un JSON de entrada con:
# - report_name (título objetivo)
# - report_type (tipo)
# - guided_prompt (instrucciones)
# - evidence (fragmentos de texto + metadatos)
# - file_ids (ids de archivos)

# Debes producir EXACTAMENTE un objeto con esta estructura:

# {
#   "title": "string",
#   "subtitle": "string (opcional)",
#   "filename": "string (opcional)",
#   "slides": [
#     {"layout":"title","title":"string","subtitle":"string"},
#     {"layout":"bullets","title":"string","bullets":["..."],"notes":"string (opcional)"},
#     {"layout":"two_columns","title":"string",
#       "left":{"heading":"...","bullets":["..."]},
#       "right":{"heading":"...","bullets":["..."]},
#       "notes":"string (opcional)"
#     },
#     {"layout":"sources","title":"string","sources":[{"name":"...","detail":"..."}]}
#   ]
# }

# REGLAS OBLIGATORIAS:
# 1) El título NO se inventa:
#    - "title" (raíz) DEBE ser exactamente report_name.
#    - En el slide layout="title", el campo "title" DEBE ser exactamente report_name.
# 2) Usa SOLO la evidencia proporcionada en evidence. NO uses conocimiento externo.
# 3) Si falta evidencia para una afirmación, escribe esa carencia en "notes" (no inventes).
# 4) Si evidence está vacío:
#    - Genera slides con bullets vacíos y notes indicando "No se recuperó evidencia".
# 5) layouts permitidos: title, bullets, two_columns, sources
# 6) bullets SIEMPRE es lista de strings (no objetos).
# 7) Mínimo 4 slides: title, contenido, síntesis/recomendaciones, sources
# 8) Slide "sources":
#    - SOLO fuentes justificables con evidence.
#    - En "name" usa el filename si existe; si no, "file_id:<id>".
#    - En "detail" usa "file_id=<id>" y si existe page/chunk_id inclúyelo.
# 9) "filename" debe terminar en ".pptx" y ser un nombre corto (sin rutas).

# IMPORTANTE: Responde SOLO con JSON válido.
# """.strip()
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
4) Citas obligatorias en cada bullet visible:
   - Cada bullet DEBE terminar con al menos una cita con formato:
     [file_id=<doc_id> chunk=<chunk_id>]
   - IMPORTANTE: doc_id y chunk_id deben existir literalmente en evidence.
     Prohibido inventar o modificar doc_id/chunk_id.
5) Control de extensión:
   - En slides layout="bullets": máximo 6 bullets.
   - Cada bullet máximo 22 palabras (o una frase corta).
6) Si NO hay evidencia suficiente para un punto:
   - NO lo inventes.
   - En su lugar, agrega un bullet: "Evidencia insuficiente en los documentos proporcionados. [file_id=<doc_id> chunk=<chunk_id>]"
     usando un doc_id/chunk_id REAL que exista en evidence (elige el más cercano).
   - Puedes ampliar detalles en notes (opcional).
7) bullets SIEMPRE es lista de strings (no objetos) y nunca vacía en slides layout="bullets".
8) Mínimo 4 slides y en este orden:
   1) title
   2) contenido/hallazgos (bullets o two_columns)
   3) síntesis/recomendaciones (bullets)
   4) sources
9) filename debe terminar en .pptx, ser corto y no incluir rutas.

Devuelve SOLO JSON válido.
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

def _sanitize_slides_in_place(spec_dict: Dict[str, Any], *, default_cite: str) -> None:
    """
    - Elimina bullets sin cita.
    - Si queda vacío, mete un bullet de "evidencia insuficiente" con cita default.
    - Para two_columns: lo mismo para left/right.
    - Evita slides en blanco.
    """
    slides = spec_dict.get("slides") or []
    for s in slides:
        layout = s.get("layout")

        if layout == "bullets":
            _ensure_nonempty_title(s, "Contenido")
            bullets = [str(x) for x in (s.get("bullets") or [])]

            kept = [b for b in bullets if _bullet_has_cite(b)]
            if not kept:
                # Si el LLM puso todo en notes, lo hacemos visible como bullet
                notes = (s.get("notes") or "").strip()
                if notes:
                    kept = [f"{notes} {default_cite}"]
                    # opcional: s.pop("notes", None)
                else:
                    kept = [f"Evidencia insuficiente en los documentos proporcionados. {default_cite}"]

            s["bullets"] = kept

        elif layout == "two_columns":
            _ensure_nonempty_title(s, "Comparativo")
            for side, fallback_heading in (("left", "Sección A"), ("right", "Sección B")):
                col = s.get(side) or {}
                if not (col.get("heading") or "").strip():
                    col["heading"] = fallback_heading

                bullets = [str(x) for x in (col.get("bullets") or [])]
                kept = [b for b in bullets if _bullet_has_cite(b)]
                if not kept:
                    kept = [f"Evidencia insuficiente. {default_cite}"]
                col["bullets"] = kept
                s[side] = col

        # title/sources no se tocan aquí

# def generate_presentation_spec(
#     *,
#     report_name: str,
#     report_type: str,
#     file_ids: List[int],
#     guided_prompt: str = "",
#     top_k: int = 20,
# ) -> Dict[str, Any]:
#     # 1) RAG query
#     query = f"{report_name}. {guided_prompt}".strip()
#     print(f"[PPTX] rag_query: {query}")

#     chunks = optimized_rag_search_files(file_ids=file_ids, query=query, top_k=top_k)
#     print(f"[PPTX] chunks from RAG: {len(chunks)}")

#     # 2) Convertir chunks a evidence
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
#     if evidence:
#         print("[PPTX] evidence[0] preview:", (evidence[0].get("text") or "")[:200])

#     user_payload = {
#         "report_name": report_name,
#         "report_type": report_type,
#         "guided_prompt": guided_prompt,
#         "file_ids": file_ids,
#         "evidence": evidence[:30],
#     }

#     # 3) LLM
#     raw = ollama_chat(
#         messages=[
#             {"role": "system", "content": SYSTEM_PROMPT},
#             {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
#         ],
#         temperature=0.1,
#         think=False,
#     )

#     data = _parse_or_repair(raw)

#     # 4) Validar schema
#     try:
#         spec = PresentationSpec.model_validate(data)
#     except ValidationError:
#         repaired = _parse_or_repair(json.dumps(data, ensure_ascii=False))
#         spec = PresentationSpec.model_validate(repaired)

#     # 5) Pasar a dict para overrides seguros
#     spec_dict = spec.model_dump()

#     # 6) HARD OVERRIDE: título siempre igual al de  report_name enviado
#     spec_dict["title"] = report_name
#     if spec_dict.get("slides") and spec_dict["slides"][0].get("layout") == "title":
#         spec_dict["slides"][0]["title"] = report_name

#     # 7) filename seguro
#     if not spec_dict.get("filename"):
#         spec_dict["filename"] = "report.pptx"
#     if not spec_dict["filename"].lower().endswith(".pptx"):
#         spec_dict["filename"] = spec_dict["filename"] + ".pptx"
#     spec_dict["filename"] = spec_dict["filename"].replace("/", "_").replace("\\", "_")
    
#     # 8) Construir sources desde evidence 
#     unique_sources = {}
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
#         "sources": list(unique_sources.values()) or [{"name": "Sin fuentes", "detail": "No se recuperó evidencia"}],
#     }

#     # 9) Reemplazar cualquier sources inventado por el LLM
#     slides = spec_dict.get("slides", [])
#     slides = [s for s in slides if s.get("layout") != "sources"]
#     slides.append(sources_slide)
#     spec_dict["slides"] = slides

#     return spec_dict

def generate_presentation_spec(
    *,
    report_name: str,
    report_type: str,
    file_ids: List[int],
    guided_prompt: str = "",
    top_k: int = 20,
) -> Dict[str, Any]:
    # 1) RAG query
    query = f"{report_name}. {guided_prompt}".strip()
    print(f"[PPTX] rag_query: {query}")

    chunks = optimized_rag_search_files(file_ids=file_ids, query=query, top_k=top_k)
    print(f"[PPTX] chunks from RAG: {len(chunks)}")

    # 2) chunks -> evidence
    evidence: List[Dict[str, Any]] = []
    for ch in chunks:
        meta = getattr(ch, "metadata_json", None) or {}
        evidence.append({
            "doc_id": getattr(ch, "file_id", None),
            "title": getattr(getattr(ch, "file", None), "filename", "") or "",
            "page": meta.get("page"),
            "chunk_id": f"{getattr(ch, 'file_id', 'x')}-{getattr(ch, 'chunk_index', 'x')}",
            "text": getattr(ch, "text", "") or "",
        })

    print(f"[PPTX] evidence items: {len(evidence)}")
    if evidence:
        print("[PPTX] evidence[0] preview:", (evidence[0].get("text") or "")[:200])

    user_payload = {
        "report_name": report_name,
        "report_type": report_type,
        "guided_prompt": guided_prompt,
        "file_ids": file_ids,
        "evidence": evidence[:30],
    }

    # 3) LLM
    raw = ollama_chat(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
        temperature=0.0,  # recomendado para estabilidad y menos “de más”
        think=False,
    )

    data = _parse_or_repair(raw)

    # 4) Validar schema
    try:
        spec = PresentationSpec.model_validate(data)
    except ValidationError:
        repaired = _parse_or_repair(json.dumps(data, ensure_ascii=False))
        spec = PresentationSpec.model_validate(repaired)

    spec_dict = spec.model_dump()

    # 5) HARD OVERRIDE: title siempre = report_name
    spec_dict["title"] = report_name
    if spec_dict.get("slides"):
        # Asegura que exista slide title
        title_slide = None
        for s in spec_dict["slides"]:
            if s.get("layout") == "title":
                title_slide = s
                break
        if title_slide is None:
            spec_dict["slides"].insert(0, {"layout": "title", "title": report_name, "subtitle": ""})
        else:
            title_slide["title"] = report_name
            title_slide.setdefault("subtitle", "")

    # 6) filename seguro
    if not spec_dict.get("filename"):
        spec_dict["filename"] = "report.pptx"
    if not spec_dict["filename"].lower().endswith(".pptx"):
        spec_dict["filename"] = spec_dict["filename"] + ".pptx"
    spec_dict["filename"] = spec_dict["filename"].replace("/", "_").replace("\\", "_")

    # 7) Sanitize: filtra “de más” y evita slides en blanco
    default_cite = _default_cite(evidence)
    _sanitize_slides_in_place(spec_dict, default_cite=default_cite)

    # 8) Construir sources desde evidence (solo evidencia real)
    unique_sources: Dict[tuple, Dict[str, str]] = {}
    for e in evidence:
        doc_id = e.get("doc_id")
        title = e.get("title") or ""
        key = (doc_id, title)
        if key not in unique_sources:
            detail_parts = [f"file_id={doc_id}"]
            if e.get("page"):
                detail_parts.append(f"page={e.get('page')}")
            if e.get("chunk_id"):
                detail_parts.append(f"chunk_id={e.get('chunk_id')}")
            unique_sources[key] = {
                "name": title or f"file_id:{doc_id}",
                "detail": ", ".join(detail_parts),
            }

    sources_slide = {
        "layout": "sources",
        "title": "Fuentes consultadas",
        "sources": list(unique_sources.values()) or [{"name": "Sin fuentes", "detail": "No se recuperó evidencia"}],
    }

    # 9) Reemplazar sources inventado por el LLM
    slides = spec_dict.get("slides", [])
    slides = [s for s in slides if s.get("layout") != "sources"]
    slides.append(sources_slide)
    spec_dict["slides"] = slides

    # 10) Garantiza mínimo 4 slides
    # (title + 2 bullets + sources)
    # Nota: tu renderer no pinta notes; mejor bullets visibles.
    def _count_non_sources(sl):
        return len([x for x in sl if x.get("layout") != "sources"])

    while _count_non_sources(spec_dict["slides"]) < 3:
        spec_dict["slides"].insert(
            -1,  # antes de sources
            {"layout": "bullets", "title": "Contenido", "bullets": [f"Evidencia insuficiente. {default_cite}"]}
        )

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

