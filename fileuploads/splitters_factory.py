import logging
from langchain.text_splitter import (
    CharacterTextSplitter,
    RecursiveCharacterTextSplitter,
    TokenTextSplitter,
)

logger = logging.getLogger(__name__)


def make_splitter(name: str = "recursive", params: dict = None):
    if params is None:
        params = {}

    splitter_classes = {
        "character": CharacterTextSplitter,
        "recursive": RecursiveCharacterTextSplitter,
        "token": TokenTextSplitter,
    }

    name_lower = name.lower()
    cls = splitter_classes.get(name_lower)

    if not cls:
        error_msg = f"Splitter '{name}' no reconocido. Usa 'character', 'recursive' o 'token'."
        logger.error(error_msg)
        print(error_msg)
        raise ValueError(error_msg)

    # Configuración por tipo de splitter
    if name_lower == "character":
        params.setdefault("separator", "")  #default: cortar cada carácter

    elif name_lower == "recursive":
        params.setdefault("separators", ["\n\n", "\n", ". ", "! ", "? ", " ", ""])

    elif name_lower == "token":
        # Asegurarse de que 'tiktoken' esté disponible
        try:
            import tiktoken 
        except ImportError:
            error_msg = (
                "❌ Error: TokenTextSplitter requiere el paquete 'tiktoken'. "
                "Instálalo con `pip install tiktoken`."
            )
            logger.error(error_msg)
            print(error_msg)
            raise ImportError(error_msg)

        # Valor por defecto para encoding_name (puedes ajustarlo si usas otro modelo)
        params.setdefault("encoding_name", "cl100k_base")

    # Crear splitter
    try:
        splitter = cls(**params)
        logger.info(f"✅ Usando splitter: {name_lower} con parámetros: {params}")
        print(f"✅ Usando splitter: {name_lower} con parámetros: {params}")
        return splitter
    except Exception as e:
        logger.error(f"❌ Error al instanciar splitter '{name_lower}': {e}")
        print(f"❌ Error al instanciar splitter '{name_lower}': {e}")
        raise
