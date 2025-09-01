import ollama
import numpy as np
from langdetect import detect
from typing import List, Tuple, Dict, Any
import time
import logging
from langchain.text_splitter import RecursiveCharacterTextSplitter
import re
from django.utils import timezone
from datetime import timedelta
from .splitters_factory import make_splitter

logger = logging.getLogger(__name__)


class OllamaEmbedder:
    def __init__(
        self,
        model_name="nomic-embed-text",
        host="http://host.docker.internal:11434",
        max_chunk_size=512,  # Tamaño máximo por chunk
        chunk_overlap=50,  # Overlap entre chunks
        batch_size=10,  # Número de chunks por batch
        max_retries=3,
    ):  # Reintentos en caso de error
        self.model_name = model_name
        self.host = host
        self.client = ollama.Client(host=host)
        self.max_chunk_size = max_chunk_size
        self.chunk_overlap = chunk_overlap
        self.batch_size = batch_size
        self.max_retries = max_retries

        # Configurar text splitter adaptativo
        # self.text_splitter = RecursiveCharacterTextSplitter(
        #     chunk_size=max_chunk_size,
        #     chunk_overlap=chunk_overlap,
        #     length_function=len,
        #     separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""],
        # )
        self.text_splitter = make_splitter(
            "recursive",
            {
                "chunk_size": max_chunk_size,
                "chunk_overlap": chunk_overlap,
                "length_function": len,
            },
        )

        # Cache para embeddings repetidos
        self._embedding_cache = {}

        # Gestión de cache automática
        self.last_cleanup = timezone.now()
        self.cleanup_interval = timedelta(hours=2)
        self.max_cache_size_mb = 50

    def _estimate_tokens(self, text: str) -> int:
        """Estima el número de tokens en un texto"""
        # Estimación aproximada: 1 token ≈ 4 caracteres en español
        return len(text) // 4

    def _get_text_hash(self, text: str) -> str:
        """Genera hash del texto para cache"""
        import hashlib

        return hashlib.md5(text.encode()).hexdigest()

    def detect_language(self, text: str) -> str:
        """Detecta idioma del texto con mejor manejo de errores"""
        try:
            # Usar más texto para mejor detección
            sample_text = text[:2000] if len(text) > 2000 else text
            # Limpiar texto de caracteres especiales
            clean_text = re.sub(r"[^\w\s]", " ", sample_text)
            if len(clean_text.strip()) < 10:
                return "es"  # Default si texto muy corto
            return detect(clean_text)
        except Exception as e:
            logger.warning(f"Error detectando idioma: {e}")
            return "es"  # Default a español

    def _optimize_chunk_size(
        self, text_length: int, language: str = "es"
    ) -> Dict[str, int]:
        """Optimiza el tamaño de chunk según la longitud del texto y idioma"""

        # Configuraciones por idioma
        lang_configs = {
            "es": {"base_chunk": 512, "min_chunk": 200, "max_chunk": 800},
            "en": {"base_chunk": 600, "min_chunk": 250, "max_chunk": 900},
            "fr": {"base_chunk": 480, "min_chunk": 180, "max_chunk": 750},
            "default": {"base_chunk": 512, "min_chunk": 200, "max_chunk": 800},
        }

        config = lang_configs.get(language, lang_configs["default"])

        # Ajustar según tamaño del texto
        if text_length < 1000:
            # Texto corto: usar chunks pequeños
            chunk_size = max(config["min_chunk"], text_length // 2)
            overlap = min(50, chunk_size // 4)
        elif text_length < 5000:
            # Texto medio: usar configuración base
            chunk_size = config["base_chunk"]
            overlap = self.chunk_overlap
        elif text_length < 20000:
            # Texto largo: chunks más grandes
            chunk_size = min(config["max_chunk"], config["base_chunk"] + 100)
            overlap = max(self.chunk_overlap, chunk_size // 8)
        else:
            # Texto muy largo: chunks máximos con más overlap
            chunk_size = config["max_chunk"]
            overlap = max(100, chunk_size // 6)

        return {
            "chunk_size": chunk_size,
            "overlap": overlap,
            "estimated_chunks": text_length // (chunk_size - overlap) + 1,
        }

    def _smart_text_splitting(self, text: str, language: str = "es") -> List[str]:
        """División inteligente del texto según su tamaño y estructura"""

        text_length = len(text)

        # Obtener configuración optimizada
        config = self._optimize_chunk_size(text_length, language)

        # Crear splitter adaptativo
        # adaptive_splitter = RecursiveCharacterTextSplitter(
        #     chunk_size=config["chunk_size"],
        #     chunk_overlap=config["overlap"],
        #     length_function=len,
        #     separators=["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""],
        # )

        adaptive_splitter = make_splitter(
            "recursive",
            {
                "chunk_size": config["chunk_size"],
                "chunk_overlap": config["overlap"],
                "length_function": len,
            },
        )

        chunks = adaptive_splitter.split_text(text)

        # Post-procesamiento de chunks
        processed_chunks = []
        for i, chunk in enumerate(chunks):
            # Limpiar chunk
            clean_chunk = chunk.strip()

            # Filtrar chunks muy cortos o muy largos
            if len(clean_chunk) < 50:
                # Intentar combinar con el chunk anterior si es muy corto
                if (
                    processed_chunks
                    and len(processed_chunks[-1]) + len(clean_chunk)
                    < config["chunk_size"]
                ):
                    processed_chunks[-1] += " " + clean_chunk
                continue
            elif len(clean_chunk) > config["chunk_size"] * 1.5:
                # Dividir chunks excesivamente largos
                sub_chunks = self._force_split_large_chunk(
                    clean_chunk, config["chunk_size"]
                )
                processed_chunks.extend(sub_chunks)
            else:
                processed_chunks.append(clean_chunk)

        logger.info(
            f"Texto de {text_length} chars dividido en {len(processed_chunks)} chunks "
            f"(estimado: {config['estimated_chunks']})"
        )

        return processed_chunks

    def _force_split_large_chunk(self, chunk: str, max_size: int) -> List[str]:
        """Fuerza la división de chunks excesivamente largos"""
        if len(chunk) <= max_size:
            return [chunk]

        # Intentar dividir por oraciones
        sentences = re.split(r"[.!?]\s+", chunk)
        sub_chunks = []
        current_chunk = ""

        for sentence in sentences:
            if len(current_chunk) + len(sentence) <= max_size:
                current_chunk += sentence + ". "
            else:
                if current_chunk:
                    sub_chunks.append(current_chunk.strip())
                current_chunk = sentence + ". "

        if current_chunk:
            sub_chunks.append(current_chunk.strip())

        return sub_chunks

    def embed_texts_batch(
        self, texts: List[str], show_progress: bool = True
    ) -> List[np.ndarray]:
        """Genera embeddings para una lista de textos en lotes con reintentos"""

        if not texts:
            return []

        all_embeddings = []
        total_batches = (len(texts) + self.batch_size - 1) // self.batch_size

        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            batch_num = i // self.batch_size + 1

            if show_progress:
                logger.info(
                    f"Procesando lote {batch_num}/{total_batches} ({len(batch)} textos)"
                )

            batch_embeddings = []
            for text in batch:
                # Verificar cache
                text_hash = self._get_text_hash(text)
                if text_hash in self._embedding_cache:
                    batch_embeddings.append(self._embedding_cache[text_hash])
                    continue

                # Generar embedding con reintentos
                embedding = self._embed_with_retry(text)
                if embedding is not None:
                    # Guardar en cache
                    self._embedding_cache[text_hash] = embedding
                    batch_embeddings.append(embedding)
                else:
                    logger.error(
                        f"Falló embedding para texto de {len(text)} caracteres"
                    )
                    # Usar embedding cero como fallback
                    batch_embeddings.append(np.zeros(768))

            all_embeddings.extend(batch_embeddings)

            # Pausa entre lotes para no sobrecargar Ollama
            if batch_num < total_batches:
                time.sleep(0.5)

        logger.info(f"Generados {len(all_embeddings)} embeddings exitosamente")
        return all_embeddings

    def _embed_with_retry(self, text: str) -> np.ndarray:
        """Genera embedding con manejo de errores y reintentos"""

        for attempt in range(self.max_retries):
            try:
                # Verificar longitud del texto
                if len(text) > 8000:  # Límite conservador
                    logger.warning(f"Texto muy largo ({len(text)} chars), truncando...")
                    text = text[:8000]

                response = self.client.embeddings(model=self.model_name, prompt=text)

                embedding = np.array(response["embedding"])

                # Validar embedding
                if embedding.size == 0 or np.all(embedding == 0):
                    raise ValueError("Embedding vacío o inválido")

                return embedding

            except Exception as e:
                logger.warning(f"Intento {attempt + 1} falló: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2**attempt)  # Backoff exponencial
                else:
                    logger.error(f"Falló después de {self.max_retries} intentos")
                    return None

    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """Genera embeddings para una lista de textos (interfaz compatible)"""
        embeddings_list = self.embed_texts_batch(texts)
        return np.array(embeddings_list)

    def embed_query(self, query: str) -> np.ndarray:
        """Genera embedding para una consulta de búsqueda"""
        # Para queries, usar directamente sin división en chunks
        embedding = self._embed_with_retry(query)
        return embedding if embedding is not None else np.zeros(768)

    def embed_document_smart(
        self, text: str, filename: str = "documento"
    ) -> Tuple[List[str], List[np.ndarray], Dict[str, Any]]:
        """
        Procesa un documento completo con división inteligente y embeddings optimizados

        Returns:
            Tuple[chunks, embeddings, metadata]
        """

        if not text or len(text.strip()) < 10:
            logger.warning("Texto vacío o muy corto")
            return [], [], {}

        # Detectar idioma
        language = self.detect_language(text)

        # División inteligente
        chunks = self._smart_text_splitting(text, language)

        if not chunks:
            logger.warning("No se generaron chunks válidos")
            return [], [], {}

        # Generar embeddings
        embeddings = self.embed_texts_batch(chunks)

        # Metadata
        metadata = {
            "filename": filename,
            "language": language,
            "total_chars": len(text),
            "num_chunks": len(chunks),
            "avg_chunk_size": np.mean([len(chunk) for chunk in chunks]),
            "processing_time": time.time(),
        }

        logger.info(
            f"Documento '{filename}' procesado: {len(chunks)} chunks, idioma: {language}"
        )

        return chunks, embeddings, metadata

    def should_cleanup_cache(self) -> bool:
        """Determina si es necesario limpiar el cache"""
        # Por tiempo
        time_based = timezone.now() - self.last_cleanup > self.cleanup_interval

        # Por tamaño
        cache_stats = self.get_cache_stats()
        size_based = cache_stats["memory_usage_mb"] > self.max_cache_size_mb

        return time_based or size_based

    def cleanup_cache(self) -> bool:
        """Limpia el cache de embeddings si es necesario"""
        try:
            if self.should_cleanup_cache():
                cache_stats_before = self.get_cache_stats()
                self.clear_cache()
                print(f"[INFO] Cache limpiado. Antes: {cache_stats_before}")
                self.last_cleanup = timezone.now()
                return True
            return False
        except Exception as e:
            print(f"[ERROR] Error limpiando cache: {str(e)}")
            return False

    def clear_cache(self):
        """Limpia el cache de embeddings"""
        self._embedding_cache.clear()
        logger.info("Cache de embeddings limpiado")

    def get_cache_stats(self) -> Dict[str, int]:
        """Retorna estadísticas del cache"""
        return {
            "cached_embeddings": len(self._embedding_cache),
            "memory_usage_mb": sum(emb.nbytes for emb in self._embedding_cache.values())
            / 1024
            / 1024,
        }


# Instancia global mejorada
embedder = OllamaEmbedder(
    max_chunk_size=512,
    chunk_overlap=50,
    batch_size=5,  # Reducido para mejor estabilidad
    max_retries=3,
)
