from .splitters_factory import make_splitter
from .embeddings_service import OllamaEmbedder


def make_embedder(config: dict = None):
    """
    Crea una instancia de OllamaEmbedder personalizada según configuración.

    config = {
        "splitter": "recursive" | "character" | "token",
        "chunk_size": 1000,
        "chunk_overlap": 200,
        "separator": "\n",                        # solo para character
        "separators": ["\n\n", "\n", ". ", " "],  # solo para recursive
        "encoding_name": "cl100k_base",           # solo para token
        "model_name": "gpt-3.5-turbo",            # solo para token (opcional)
        "batch_size": 10,
        "max_retries": 3
    }
    """
    # De momento los params: separator, separators, encoding_name y model_name pueden no pasarse
    # , ya que en el archivo spliteers_factory.py al momento se están definiendo  de manera 
    # diferenciada por splitter.
    if config is None:
        config = {}

    splitter_name = config.get("splitter", "recursive")
    # splitter_params = {
    #     "chunk_size": config.get("chunk_size", 1000),
    #     "chunk_overlap": config.get("chunk_overlap", 200),
    # }
    splitter_params = {
        key: config[key]
        for key in [
            "chunk_size",
            "chunk_overlap",
            "separator",
            "separators",
            "encoding_name",
            "model_name",
        ]
        if key in config
    }
    text_splitter = make_splitter(splitter_name, splitter_params)

    return OllamaEmbedder(
        text_splitter=text_splitter,
        batch_size=config.get("batch_size", 10),
        max_retries=config.get("max_retries", 3),
    )
