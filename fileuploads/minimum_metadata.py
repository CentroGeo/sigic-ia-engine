import logging
import re
import uuid
from datetime import datetime, timezone
from tempfile import NamedTemporaryFile
from typing import Any, Dict, List, Optional, Tuple

import ollama
import requests
from PyPDF2 import PdfReader
from pgvector.django import L2Distance

from .embeddings_service import embedder
from .models import DocumentEmbedding, Files
from django.conf import settings

logger = logging.getLogger(__name__)


class HybridMinimumMetadataExtractor:
    """Extracción híbrida de metadatos Dublin Core + DataCite."""

    METADATA_SCHEMA: Dict[str, Dict[str, Any]] = {
        "title": {
            "label": "Título",
            "required": True,
        },
        "description": {
            "label": "Resumen",
            "required": True,
        },
        "dateIssued": {
            "label": "Fecha de publicación",
            "required": True,
        },
        "resourceType": {
            "label": "Tipo de recurso",
            "required": True,
        },
        "identifier": {
            "label": "Identificador persistente",
            "required": True,
        },
        "creator": {
            "label": "Autor(es)",
            "required": True,
        },
        "publisher": {
            "label": "Institución responsable",
            "required": True,
        },
        "subject": {
            "label": "Palabras clave",
            "required": False,
        },
        "rights": {
            "label": "Licencia",
            "required": True,
        },
        "language": {
            "label": "Idioma",
            "required": True,
        },
    }

    RAG_QUERIES: Dict[str, List[str]] = {
        "title": [
            "¿Cuál es el título principal de este documento?",
        ],
        "description": [
            "Resume el propósito principal del documento en un párrafo conciso.",
        ],
        "dateIssued": [
            "¿Cuándo se publicó este documento?",
        ],
        "resourceType": [
            "Identifica el tipo de recurso descrito en el documento.",
        ],
        "identifier": [
            "¿Qué identificadores (DOI, UUID, etc.) se mencionan para este documento?",
        ],
        "creator": [
            "Lista a los autores o responsables principales del documento.",
        ],
        "publisher": [
            "¿Qué institución o entidad publica o respalda este documento?",
        ],
        "subject": [
            "Enumera las palabras clave o temas principales del documento.",
        ],
        "rights": [
            "¿Bajo qué licencia o términos de uso está disponible el documento?",
        ],
        "language": [
            "¿En qué idioma está escrito el documento?",
        ],
    }

    RESOURCE_TYPE_MAPPING: Dict[str, List[str]] = {
        "dataset": ["dataset", "datos", "base de datos", "conjunto de datos"],
        "text": ["texto", "documento", "document"],
        "report": ["informe", "reporte", "report", "estudio"],
        "article": ["artículo", "article", "paper", "publicación"],
        "book": ["libro", "book", "manual", "guía"],
        "map": ["mapa", "map", "cartografía", "plano"],
        "presentation": ["presentación", "presentation", "slides"],
        "software": ["software", "programa", "aplicación"],
        "audiovisual": ["video", "audio", "multimedia"],
        "image": ["imagen", "image", "foto", "fotografía"],
        "collection": ["colección", "collection", "serie"],
    }

    LICENSE_MAPPING: Dict[str, List[str]] = {
        "CC-BY": ["creative commons attribution", "cc by", "cc-by"],
        "CC-BY-SA": ["creative commons share alike", "cc by-sa", "cc-by-sa"],
        "CC-BY-NC": ["creative commons non commercial", "cc by-nc", "cc-by-nc"],
        "CC0": ["creative commons zero", "cc0", "dominio público"],
        "MIT": ["mit license", "mit"],
        "APACHE": ["apache license", "apache"],
        "GPL": ["gnu general public license", "gpl"],
    }

    LANGUAGE_CODES: Dict[str, str] = {
        "español": "es",
        "spanish": "es",
        "inglés": "en",
        "english": "en",
        "francés": "fr",
        "french": "fr",
        "portugués": "pt",
        "portuguese": "pt",
        "italiano": "it",
        "italian": "it",
        "alemán": "de",
        "german": "de",
    }

    def __init__(
        self,
        geonode_base_url: str,
        authorization: str,
        cookie: Optional[str] = None,
        llm_model: str = "llama3.1",
        ollama_host: str = settings.OLLAMA_API_URL,
    ) -> None:
        self.geonode_base_url = geonode_base_url.rstrip("/")
        self.authorization = authorization
        self.cookie = cookie
        self.llm_model = llm_model

        # Create Ollama client with proper host for Docker container
        self.ollama_client = ollama.Client(host=ollama_host)
        print(f"[METADATA] Ollama client initialized with host: {ollama_host}")

        self.http_headers = {
            "Authorization": authorization,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if cookie:
            self.http_headers["Cookie"] = cookie

        # Cache de keywords de GeoNode para evitar múltiples llamadas al API
        self._keywords_cache: Optional[Dict[str, Dict[str, Any]]] = None

    def process(
        self,
        uploaded_file,
        file_record: Files,
        geonode_document_id: int,
        additional_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        # Si no hay archivo, solo usamos RAG metadata (caso async desde Celery)
        pdf_path = None
        if uploaded_file is not None:
            pdf_path = self._persist_temporal_file(uploaded_file)

        file_identifiers = {
            "file_pk": getattr(file_record, "pk", None),
            "geonode_id": getattr(file_record, "geonode_id", None),
            "geonode_uuid": getattr(file_record, "geonode_uuid", None),
            "legacy_document_id": getattr(file_record, "document_id", None),
        }

        logger.info("[Metadata] Iniciando extracción mínima: %s", file_identifiers)
        print(f"[METADATA] === Iniciando extracción de metadatos ===")
        print(f"[METADATA] File ID: {file_identifiers.get('file_pk')}, GeoNode ID: {geonode_document_id}")
        print(f"[METADATA] Modo: {'PDF + RAG' if pdf_path else 'Solo RAG (async)'}")

        try:
            # Extraer metadatos PDF solo si tenemos el archivo
            pdf_metadata = {}
            if pdf_path:
                pdf_metadata = self._extract_pdf_metadata(pdf_path)
                logger.info("[Metadata] Metadatos PDF extraídos: %s", pdf_metadata)
                print(f"[METADATA] Metadatos PDF extraídos: {pdf_metadata.get('title', 'N/A')}")
            else:
                print(f"[METADATA] Saltando extracción PDF (modo async)")

            rag_metadata = self._extract_rag_metadata(file_record)
            logger.info("[Metadata] Metadatos RAG extraídos: %s", rag_metadata)
            print(f"[METADATA] Metadatos RAG extraídos: {len(rag_metadata)} campos")

            merged_metadata = self._merge_metadata(pdf_metadata, rag_metadata)
            logger.info("[Metadata] Metadatos combinados listos: %s", merged_metadata)
            print(f"[METADATA] Metadatos combinados: título='{merged_metadata.get('title', 'N/A')}'")

            validation = self._validate_metadata(merged_metadata)
            logger.info("[Metadata] Validación completada: %s", validation)
            print(f"[METADATA] Validación: score={validation.get('quality_score', 0)}%")

            geonode_payload = self._map_to_geonode(merged_metadata, additional_data)
            logger.info("[Metadata] Payload preparado para GeoNode: %s", geonode_payload)
            print(f"[METADATA] Payload GeoNode preparado con {len(geonode_payload)} campos")

            update_result = self._update_geonode_document(geonode_document_id, geonode_payload)
            logger.info("[Metadata] Resultado actualización GeoNode: %s", update_result)
            print(f"[METADATA] Actualización GeoNode: success={update_result.get('success', False)}")

            return {
                "pdf_metadata": pdf_metadata,
                "rag_metadata": rag_metadata,
                "merged_metadata": merged_metadata,
                "validation": validation,
                "geonode_payload": geonode_payload,
                "update_result": update_result,
            }
        finally:
            if pdf_path:
                pdf_path.unlink(missing_ok=True)
                logger.info("[Metadata] Archivo temporal eliminado")

    def _persist_temporal_file(self, uploaded_file) -> "Path":
        from pathlib import Path

        uploaded_file.seek(0)
        with NamedTemporaryFile(delete=False, suffix=Path(uploaded_file.name).suffix) as tmp:
            data = uploaded_file.read()
            tmp.write(data)
            tmp.flush()
            path = Path(tmp.name)
        uploaded_file.seek(0)
        return path

    def _extract_pdf_metadata(self, pdf_path: "Path") -> Dict[str, Any]:
        metadata: Dict[str, Any] = {}

        try:
            with open(pdf_path, "rb") as fh:
                reader = PdfReader(fh)
                info = reader.metadata or {}

                metadata.update(
                    {
                        "title": info.get("/Title", ""),
                        "description": info.get("/Subject", ""),
                        "creator": info.get("/Author", ""),
                        "publisher": info.get("/Producer", ""),
                        "dateIssued": self._format_pdf_date(info.get("/CreationDate")),
                        "language": self._detect_language_from_pdf(info),
                        "keywords_raw": info.get("/Keywords", ""),
                        "page_count": len(reader.pages),
                    }
                )

        except Exception as exc:
            logger.warning("No se pudieron extraer metadatos PDF: %s", exc)

        return metadata

    def _extract_rag_metadata(self, file_record: Optional[Files]) -> Dict[str, Any]:
        if not file_record:
            logger.info("[Metadata] Sin registro de archivo, se omite RAG")
            return {}

        document_uuid = getattr(file_record, "geonode_uuid", None) or getattr(file_record, "document_id", None)
        logger.info(
            "[Metadata] Iniciando RAG para file_id=%s, geonode_uuid=%s",
            getattr(file_record, "pk", None),
            document_uuid,
        )

        rag_metadata: Dict[str, Any] = {}
        context_chunks = self._retrieve_context_chunks(file_record)

        if not context_chunks:
            logger.info(
                "[Metadata] Sin chunks de contexto disponibles para RAG (file_id=%s)",
                getattr(file_record, "pk", None),
            )
            return {}

        for field, questions in self.RAG_QUERIES.items():
            if not questions:
                continue

            context_text = "\n\n".join(text for text, _ in context_chunks[:3])
            prompt = self._build_prompt(field, context_text, questions[0])
            logger.info(
                "[Metadata] Prompt generado para %s: %s",
                field,
                prompt[:200],
            )
            response = self._invoke_llm(prompt)

            if response:
                logger.info("[Metadata] Respuesta LLM %s: %s", field, response)
                processed = self._postprocess_field(field, response)
                if processed:
                    rag_metadata[field] = processed
                    logger.info("[Metadata] Campo RAG procesado %s: %s", field, processed)

        return rag_metadata

    def _retrieve_context_chunks(self, file_record: Files, limit: int = 8) -> List[Tuple[str, float]]:
        base_query = "contenido principal documento metadatos información"
        query_embedding = embedder.embed_query(base_query)

        if query_embedding is None:
            logger.info("[Metadata] Embedder sin respuesta para consulta base")
            return []

        embeddings_qs = (
            DocumentEmbedding.objects.filter(file=file_record)
            .annotate(distance=L2Distance("embedding", query_embedding.tolist()))
            .order_by("distance")[:limit]
        )

        embeddings_list = list(embeddings_qs)
        logger.info(
            "[Metadata] Chunks recuperados para file_id=%s: %s/%s",
            getattr(file_record, "pk", None),
            len(embeddings_list),
            limit,
        )

        return [(item.text, float(item.distance or 0)) for item in embeddings_list]

    def _build_prompt(self, field: str, context: str, question: str) -> str:
        if field == "description":
            return (
                "Basado en el siguiente contexto, redacta un resumen ejecutivo conciso "
                "(120-200 palabras).\n\nCONTEXTO:\n"
                f"{context}\n\nPREGUNTA: {question}\n\nRESPUESTA:"
            )

        if field == "subject":
            return (
                "A partir del contexto, identifica de cinco a diez palabras clave relevantes. "
                "Devuélvelas como una lista separada por comas.\n\nCONTEXTO:\n"
                f"{context}\n\nPREGUNTA: {question}\n\nRESPUESTA:"
            )

        return (
            "Responde la pregunta de forma directa usando solo la información del contexto. "
            "Si no hay datos suficientes, responde 'No disponible'.\n\nCONTEXTO:\n"
            f"{context}\n\nPREGUNTA: {question}\n\nRESPUESTA:"
        )

    def _invoke_llm(self, prompt: str) -> str:
        try:
            print(f"[METADATA] Llamando a Ollama con modelo {self.llm_model}...")
            result = self.ollama_client.generate(
                model=self.llm_model,
                prompt=prompt,
                options={
                    "temperature": 0.1,
                    "top_p": 0.9,
                    "max_tokens": 300,
                },
            )
            response = result.get("response", "").strip()
            print(f"[METADATA] Respuesta de Ollama recibida: {len(response)} caracteres")
            return response
        except Exception as exc:
            logger.warning("Fallo consulta al modelo Ollama: %s", exc)
            print(f"[METADATA] ERROR Ollama: {str(exc)}")
            return ""

    def _postprocess_field(self, field: str, raw_value: str) -> Any:
        if not raw_value:
            return None

        if raw_value.lower() in {"no disponible", "n/a", "no data"}:
            return None

        if field == "dateIssued":
            return self._extract_date(raw_value)
        if field == "resourceType":
            return self._classify_resource_type(raw_value)
        if field == "identifier":
            return self._extract_identifier(raw_value)
        if field == "creator":
            return self._extract_creators(raw_value)
        if field == "subject":
            return self._extract_keywords(raw_value)
        if field == "language":
            return self._normalize_language(raw_value)
        if field == "rights":
            return self._normalize_license(raw_value)

        return raw_value.strip()

    def _extract_date(self, text: str) -> Optional[str]:
        patterns = [
            r"(\d{4})-(\d{1,2})-(\d{1,2})",
            r"(\d{1,2})/(\d{1,2})/(\d{4})",
            r"(\d{1,2})-(\d{1,2})-(\d{4})",
            r"(\d{4})",
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue

            groups = match.groups()
            if len(groups) == 3:
                year, month, day = groups[0], groups[1], groups[2]
                if len(year) == 4:
                    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                return f"{groups[2]}-{groups[1].zfill(2)}-{groups[0].zfill(2)}"

            if len(groups) == 1:
                return f"{groups[0]}-01-01"

        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _classify_resource_type(self, text: str) -> str:
        lowered = text.lower()
        for resource_type, keywords in self.RESOURCE_TYPE_MAPPING.items():
            if any(keyword in lowered for keyword in keywords):
                return resource_type
        return "text"

    def _extract_identifier(self, text: str) -> str:
        doi_match = re.search(r"10\.\d+/[^\s]+", text)
        if doi_match:
            return doi_match.group()

        uuid_match = re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            text.lower(),
        )
        if uuid_match:
            return uuid_match.group()

        return str(uuid.uuid4())

    def _extract_creators(self, text: str) -> List[str]:
        authors: List[str] = []
        patterns = [
            r"(?:autor|author|por|by):\s*([^\.\n]+)",
            r"([A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.MULTILINE)
            for match in matches:
                clean = match.strip().rstrip(",.")
                if 3 < len(clean) < 100 and clean not in authors:
                    authors.append(clean)

        if not authors and text.strip():
            tokens = text.strip().split()
            authors.append(" ".join(tokens[:3]))

        return authors[:5] if authors else ["Autor no especificado"]

    def _extract_keywords(self, text: str) -> List[str]:
        candidates: List[str] = []
        patterns = [
            r"\d+\.\s*([^,\n.]+)",
            r"-\s*([^,\n.]+)",
            r"•\s*([^,\n.]+)",
            r",\s*([^,\n.]+)",
            r":\s*([^,\n.]+)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.MULTILINE)
            candidates.extend(match.strip().lower() for match in matches)

        filtered = [kw for kw in candidates if 3 <= len(kw) <= 30]
        unique = list(dict.fromkeys(filtered))
        return unique[:10]

    def _normalize_language(self, text: str) -> str:
        lowered = text.lower().strip()
        if lowered in {"es", "en", "fr", "pt", "it", "de"}:
            return lowered

        for name, code in self.LANGUAGE_CODES.items():
            if name in lowered:
                return code

        return "es"

    def _normalize_license(self, text: str) -> str:
        lowered = text.lower()
        for license_code, aliases in self.LICENSE_MAPPING.items():
            if any(alias in lowered for alias in aliases):
                return license_code

        if "creative commons" in lowered or "cc" in lowered:
            return "CC-BY"

        text = text.strip()
        return text if text else "Todos los derechos reservados"

    def _merge_metadata(
        self,
        pdf_metadata: Dict[str, Any],
        rag_metadata: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}

        for field in self.METADATA_SCHEMA.keys():
            pdf_value = pdf_metadata.get(field)
            rag_value = rag_metadata.get(field)

            if field == "title":
                merged[field] = self._merge_title(pdf_value, rag_value)
            elif field == "description":
                merged[field] = rag_value or pdf_value or "Descripción no disponible"
            elif field == "creator":
                merged[field] = self._merge_creators(pdf_value, rag_value)
            elif field == "subject":
                merged[field] = self._merge_keywords(pdf_metadata.get("keywords_raw"), rag_value)
            elif field in {"dateIssued", "identifier", "resourceType"}:
                merged[field] = rag_value or pdf_value
            else:
                merged[field] = pdf_value or rag_value

            if self.METADATA_SCHEMA[field]["required"] and not merged.get(field):
                merged[field] = self._default_value(field)

        return merged

    def _merge_title(self, pdf_title: Optional[str], rag_title: Optional[str]) -> str:
        if not pdf_title and not rag_title:
            return "Documento sin título"
        if not pdf_title:
            return rag_title or "Documento sin título"
        if not rag_title:
            return pdf_title
        return rag_title if len(rag_title) > len(pdf_title) else pdf_title

    def _merge_creators(
        self,
        pdf_creator: Optional[str],
        rag_creators: Optional[List[str]],
    ) -> List[str]:
        creators: List[str] = []
        if pdf_creator:
            creators.append(pdf_creator.strip())
        if rag_creators:
            if isinstance(rag_creators, list):
                creators.extend(rag_creators)
            else:
                creators.append(str(rag_creators))

        cleaned: List[str] = []
        for creator in creators:
            creator = creator.strip()
            if creator and creator not in cleaned:
                cleaned.append(creator)

        return cleaned[:5] if cleaned else ["Autor no especificado"]

    def _merge_keywords(
        self,
        pdf_keywords: Optional[str],
        rag_keywords: Optional[List[str]],
    ) -> List[str]:
        keywords: List[str] = []

        if pdf_keywords:
            for kw in re.split(r"[,;]", pdf_keywords):
                kw = kw.strip().lower()
                if kw:
                    keywords.append(kw)

        if rag_keywords:
            if isinstance(rag_keywords, list):
                keywords.extend([kw.lower() for kw in rag_keywords])
            else:
                keywords.append(str(rag_keywords).lower())

        filtered = [kw for kw in keywords if len(kw) > 2]
        unique = list(dict.fromkeys(filtered))
        return unique[:10]

    def _default_value(self, field: str) -> Any:
        defaults = {
            "title": "Documento sin título",
            "description": "Descripción no disponible",
            "dateIssued": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "resourceType": "text",
            "identifier": str(uuid.uuid4()),
            "creator": ["Autor no especificado"],
            "publisher": "Institución no especificada",
            "rights": "Todos los derechos reservados",
            "language": "es",
        }
        return defaults.get(field)

    def _validate_metadata(self, metadata: Dict[str, Any]) -> Dict[str, Any]:
        total_fields = len(self.METADATA_SCHEMA)
        completed = 0
        missing_required: List[str] = []
        missing_optional: List[str] = []

        for field, config in self.METADATA_SCHEMA.items():
            value = metadata.get(field)
            has_value = bool(value)

            if config["required"]:
                if has_value:
                    completed += 1
                else:
                    missing_required.append(config["label"])
            else:
                if has_value:
                    completed += 1
                else:
                    missing_optional.append(config["label"])

        score = round((completed / total_fields) * 100, 2)

        return {
            "quality_score": score,
            "missing_required": missing_required,
            "missing_optional": missing_optional,
        }

    def _map_to_geonode(
        self,
        metadata: Dict[str, Any],
        additional_data: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        # Only include fields that GeoNode 4.x accepts for documents
        # Using simpler field names that are more compatible
        payload: Dict[str, Any] = {}

        # Core metadata fields
        if metadata.get("title"):
            payload["title"] = metadata.get("title")

        if metadata.get("description"):
            payload["abstract"] = metadata.get("description")

        # Keywords - GeoNode 4.4.x requiere que las keywords ya existan en la base de datos
        # Solo se asignan keywords que están previamente registradas en GeoNode
        if metadata.get("subject"):
            subjects = metadata.get("subject")
            keyword_names = subjects if isinstance(subjects, list) else [str(subjects)]

            # Verificar que las keywords existan y obtener sus slugs
            valid_slugs = self._ensure_keywords_exist(keyword_names)

            if valid_slugs:
                # Usar los slugs de las keywords existentes
                payload["keywords"] = valid_slugs
                print(f"[METADATA] {len(valid_slugs)} keywords válidas serán asignadas")
            else:
                print(f"[METADATA] Ninguna keyword válida encontrada, omitiendo campo keywords")

        # Language - should be ISO code
        if metadata.get("language"):
            payload["language"] = metadata.get("language")

        # Attribution/credit field instead of poc
        if metadata.get("creator"):
            creators = metadata.get("creator")
            if isinstance(creators, list):
                payload["attribution"] = ", ".join(creators)
            else:
                payload["attribution"] = str(creators)

        # Only add additional_data if provided
        if additional_data:
            for k, v in additional_data.items():
                if v and k not in payload:  # Don't override existing values
                    payload[k] = v

        return payload

    def _update_geonode_document(
        self,
        document_id: int,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not payload:
            print("[METADATA] ERROR: Sin datos para actualizar")
            return {"success": False, "error": "Sin datos para actualizar"}

        url = f"{self.geonode_base_url}/api/v2/documents/{document_id}/"

        if "title" not in payload or "abstract" not in payload:
            print(f"[METADATA] ERROR: Campos obligatorios faltantes - title: {'title' in payload}, abstract: {'abstract' in payload}")
            return {
                "success": False,
                "error": "Campos obligatorios faltantes para GeoNode",
            }

        logger.info(
            "[Metadata] Llamando GeoNode PATCH %s con payload: %s",
            url,
            payload,
        )
        print(f"[METADATA] Llamando GeoNode PATCH: {url}")
        print(f"[METADATA] Payload: title={payload.get('title')}, abstract_length={len(payload.get('abstract', ''))} chars")

        try:
            response = requests.patch(url, headers=self.http_headers, json=payload, timeout=30)
            response.raise_for_status()
            print(f"[METADATA] GeoNode PATCH exitoso: {response.status_code}")
            return {
                "success": True,
                "status_code": response.status_code,
                "data": response.json(),
            }
        except requests.RequestException as exc:
            logger.warning("Fallo actualización GeoNode: %s", exc)
            print(f"[METADATA] ERROR en PATCH: {str(exc)}")
            if hasattr(exc, 'response') and exc.response is not None:
                print(f"[METADATA] Response status: {exc.response.status_code}, body: {exc.response.text[:500]}")
            return {
                "success": False,
                "error": str(exc),
                "status_code": getattr(exc.response, "status_code", None) if hasattr(exc, 'response') else None,
            }

    def _format_pdf_date(self, pdf_date: Optional[str]) -> Optional[str]:
        if not pdf_date:
            return None

        try:
            if pdf_date.startswith("D:"):
                date_str = pdf_date[2:10]
                return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        except Exception:
            return None

        return None

    def _detect_language_from_pdf(self, pdf_info) -> str:
        lang_field = pdf_info.get("/Language", "") if pdf_info else ""
        return self._normalize_language(lang_field or "es")

    def _get_geonode_keywords(self) -> Dict[str, Dict[str, Any]]:
        """
        Obtiene todas las keywords existentes en GeoNode y las almacena en caché.

        Returns:
            Dict mapeando nombre de keyword (normalizado) a su información completa
        """
        if self._keywords_cache is not None:
            return self._keywords_cache

        logger.info("[Keywords] Obteniendo keywords existentes de GeoNode...")
        print(f"[KEYWORDS] Consultando keywords existentes en GeoNode...")

        self._keywords_cache = {}

        try:
            page = 1
            page_size = 100
            total_keywords = 0

            while True:
                url = f"{self.geonode_base_url}/api/v2/keywords?page={page}&page_size={page_size}"
                response = requests.get(url, headers=self.http_headers, timeout=30)
                response.raise_for_status()
                data = response.json()

                keywords = data.get("keywords", [])
                for kw in keywords:
                    # Normalizar nombre para búsqueda case-insensitive
                    normalized_name = kw["name"].lower().strip()
                    self._keywords_cache[normalized_name] = {
                        "id": kw["id"],
                        "name": kw["name"],
                        "slug": kw["slug"],
                    }
                    total_keywords += 1

                # Verificar si hay más páginas
                if not data.get("links", {}).get("next"):
                    break

                page += 1

            logger.info(f"[Keywords] Cache construido: {total_keywords} keywords")
            print(f"[KEYWORDS] Cache construido con {total_keywords} keywords existentes")

        except Exception as exc:
            logger.warning(f"[Keywords] Error obteniendo keywords de GeoNode: {exc}")
            print(f"[KEYWORDS] ERROR: No se pudo obtener keywords: {str(exc)}")
            # Retornar cache vacío en caso de error
            self._keywords_cache = {}

        return self._keywords_cache

    def _ensure_keywords_exist(self, keyword_names: List[str]) -> List[str]:
        """
        Verifica que las keywords existan en GeoNode.
        En GeoNode 4.4.x, las keywords deben existir previamente.

        Esta función retorna solo las keywords que YA EXISTEN en GeoNode.

        Args:
            keyword_names: Lista de nombres de keywords a verificar

        Returns:
            Lista de slugs de keywords que existen en GeoNode
        """
        if not keyword_names:
            return []

        logger.info(f"[Keywords] Verificando {len(keyword_names)} keywords...")
        print(f"[KEYWORDS] Verificando {len(keyword_names)} keywords propuestas...")

        # Obtener cache de keywords existentes
        existing_keywords = self._get_geonode_keywords()

        matched_slugs = []
        missing_keywords = []

        for kw_name in keyword_names:
            normalized = kw_name.lower().strip()

            if normalized in existing_keywords:
                # Keyword existe, usar su slug
                slug = existing_keywords[normalized]["slug"]
                matched_slugs.append(slug)
                logger.info(f"[Keywords] ✓ '{kw_name}' encontrada con slug '{slug}'")
                print(f"[KEYWORDS] ✓ Keyword '{kw_name}' existe (slug: {slug})")
            else:
                # Keyword no existe
                missing_keywords.append(kw_name)
                logger.warning(f"[Keywords] ✗ '{kw_name}' NO existe en GeoNode")
                print(f"[KEYWORDS] ✗ Keyword '{kw_name}' NO existe en GeoNode (se omitirá)")

        if missing_keywords:
            logger.warning(
                f"[Keywords] {len(missing_keywords)} keywords no encontradas: {missing_keywords[:5]}"
            )
            print(f"[KEYWORDS] ADVERTENCIA: {len(missing_keywords)} keywords no se asignarán porque no existen en GeoNode")
            print(f"[KEYWORDS] Sugerencia: Crear estas keywords manualmente en GeoNode primero")

        logger.info(f"[Keywords] {len(matched_slugs)} keywords válidas de {len(keyword_names)} propuestas")
        print(f"[KEYWORDS] Resultado: {len(matched_slugs)}/{len(keyword_names)} keywords válidas")

        return matched_slugs
