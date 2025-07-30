import os
from llama_index.vector_stores.postgres import PGVectorStore

vector_store = PGVectorStore.from_params(
    database=os.environ.get('DB_NAME', 'llm'),
    host=os.environ.get('DB_HOST','db'),
    password=os.environ.get('DB_PASSWORD','postgres'),
    port=os.environ.get('DB_PORT','5432'),
    user=os.environ.get('DB_USER', 'postgres'),
    table_name='documentos_vectorizados'
)

