import ollama
import numpy as np
from langdetect import detect
from typing import List, Tuple

class OllamaEmbedder:
    #def __init__(self, model_name='mxbai-embed-large', host='http://host.docker.internal:11434'):  #1024 dimensiones
    def __init__(self, model_name='nomic-embed-text', host='http://host.docker.internal:11434'):  #768 dimensiones
        self.model_name = model_name
        self.host = host
        self.client = ollama.Client(host=host)
    
    def detect_language(self, text: str) -> str:
        try:
            return detect(text[:1000])  # Usamos los primeros 1000 caracteres para eficiencia
        except:
            return 'es'  # Default a español
    
    def embed_texts(self, texts: List[str]) -> np.ndarray:
        """Genera embeddings para una lista de textos"""
        embeddings = []
        for text in texts:
            response = self.client.embeddings(
                model=self.model_name,
                prompt=text
            )
            embeddings.append(response['embedding'])
        return np.array(embeddings)
    
    def embed_query(self, query: str) -> np.ndarray:
        """Genera embedding para una consulta de búsqueda"""
        response = self.client.embeddings(
            model=self.model_name,
            prompt=query
        )
        return np.array(response['embedding'])

# Instancia global para reutilizar la conexión
embedder = OllamaEmbedder()