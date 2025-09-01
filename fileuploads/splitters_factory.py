from langchain.text_splitter import (
    CharacterTextSplitter,
    RecursiveCharacterTextSplitter,
    TokenTextSplitter,
)
import logging
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


    if name_lower == "recursive" and "separators" not in params:
        params["separators"] = ["\n\n", "\n", ". ", "! ", "? ", " ", ""]

    try:
        splitter = cls(**params)
        logger.info(f"✅ Usando splitter: {name_lower} con parámetros: {params}")
        print(f"✅ Usando splitter: {name_lower} con parámetros: {params}")
        return splitter
    except Exception as e:
        logger.error(f"❌ Error al instanciar splitter '{name_lower}': {e}")
        print(f"❌ Error al instanciar splitter '{name_lower}': {e}")
        raise
