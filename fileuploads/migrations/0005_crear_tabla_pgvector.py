from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ("fileuploads", "0004_files_remove_indexado_context_context_and_more"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
            -- Habilitar extensión pgvector
            CREATE EXTENSION IF NOT EXISTS vector;

            -- Crear tabla de vectores
            CREATE TABLE IF NOT EXISTS documentos_vectorizados (
                id SERIAL PRIMARY KEY,
                node_id TEXT UNIQUE NOT NULL,
                text TEXT NOT NULL,
                metadata JSONB,
                embedding VECTOR(384) NOT NULL
            );

            -- Índice para búsquedas rápidas
            CREATE INDEX IF NOT EXISTS idx_vector_cosine
            ON documentos_vectorizados
            USING ivfflat (embedding vector_cosine_ops)
            WITH (lists = 100);
            """,
            reverse_sql="""
            DROP INDEX IF EXISTS idx_vector_cosine;
            DROP TABLE IF EXISTS documentos_vectorizados;
            DROP EXTENSION IF EXISTS vector;
            """,
        )
    ]
