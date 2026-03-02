import json
from typing import Any, Dict, List
import re

from pydantic import ValidationError

from reports.schemas.presentation_spec import PresentationSpec
from reports.services.ollama_client import ollama_chat
from fileuploads.views import optimized_rag_search_files


SYSTEM_PROMPT = """
Eres un generador de JSON ESTRICTO. Devuelve SOLO JSON válido (sin texto extra, sin markdown).

Vas a recibir un JSON de entrada con:
- report_name (título objetivo)
- report_type (tipo)
- guided_prompt (instrucciones)
- evidence (fragmentos de texto + metadatos)
- file_ids (ids de archivos)

Debes producir EXACTAMENTE un objeto con esta estructura:

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
1) El título NO se inventa:
   - "title" (raíz) DEBE ser exactamente report_name.
   - En el slide layout="title", el campo "title" DEBE ser exactamente report_name.
2) Usa SOLO la evidencia proporcionada en evidence. NO uses conocimiento externo.
3) Si falta evidencia para una afirmación, escribe esa carencia en "notes" (no inventes).
4) Si evidence está vacío:
   - Genera slides con bullets vacíos y notes indicando "No se recuperó evidencia".
5) layouts permitidos: title, bullets, two_columns, sources
6) bullets SIEMPRE es lista de strings (no objetos).
7) Mínimo 4 slides: title, contenido, síntesis/recomendaciones, sources
8) Slide "sources":
   - SOLO fuentes justificables con evidence.
   - En "name" usa el filename si existe; si no, "file_id:<id>".
   - En "detail" usa "file_id=<id>" y si existe page/chunk_id inclúyelo.
9) "filename" debe terminar en ".pptx" y ser un nombre corto (sin rutas).

IMPORTANTE: Responde SOLO con JSON válido.
""".strip()



REPAIR_PROMPT = """
El contenido siguiente no es JSON válido o no cumple el esquema. Corrígelo.
Devuelve SOLO JSON válido.

Contenido:
""".strip()

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

    # 2) Convertir chunks a evidence
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
        temperature=0.1,
        think=False,
    )

    data = _parse_or_repair(raw)

    # 4) Validar schema
    try:
        spec = PresentationSpec.model_validate(data)
    except ValidationError:
        repaired = _parse_or_repair(json.dumps(data, ensure_ascii=False))
        spec = PresentationSpec.model_validate(repaired)

    # 5) Pasar a dict para overrides seguros
    spec_dict = spec.model_dump()

    # 6) HARD OVERRIDE: título siempre igual al de  report_name enviado
    spec_dict["title"] = report_name
    if spec_dict.get("slides") and spec_dict["slides"][0].get("layout") == "title":
        spec_dict["slides"][0]["title"] = report_name

    # 7) filename seguro
    if not spec_dict.get("filename"):
        spec_dict["filename"] = "report.pptx"
    if not spec_dict["filename"].lower().endswith(".pptx"):
        spec_dict["filename"] = spec_dict["filename"] + ".pptx"
    spec_dict["filename"] = spec_dict["filename"].replace("/", "_").replace("\\", "_")

    # 8) Construir sources desde evidence 
    unique_sources = {}
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

    # 9) Reemplazar cualquier sources inventado por el LLM
    slides = spec_dict.get("slides", [])
    slides = [s for s in slides if s.get("layout") != "sources"]
    slides.append(sources_slide)
    spec_dict["slides"] = slides

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

