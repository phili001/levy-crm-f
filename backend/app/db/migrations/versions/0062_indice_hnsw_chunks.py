"""La búsqueda por significado deja de perder resultados: índice HNSW en vez de IVFFlat.

La migración inicial creaba `ix_chunks_embedding` como IVFFlat con
`lists = 100` sobre la tabla `chunks` VACÍA. IVFFlat reparte los vectores en
listas alrededor de unos centros que calcula AL CREARSE, con lo que haya en la
tabla; sin nada, los centros no significan nada, y cada fragmento que llega
después cae en una lista cualquiera. Al buscar, Postgres solo mira la lista más
cercana a la pregunta (`ivfflat.probes = 1`): devolvía uno de cinco fragmentos
que sí se parecían, y muchas veces ninguno. Pasaba en TODA instalación nueva,
en «Probar el RAG» (`match_chunks`) y en la mitad vectorial de
`match_chunks_hybrid`, la que usa Sofía. Salió al grabar el video 07: en el CRM
del curso hubo que borrar el índice a mano para que «Probar» encontrara algo.

HNSW no depende de lo que haya en la tabla al crearse: cada fragmento se
engancha a su vecindario al insertarse. Cabe la dimensión de los embeddings
(1536, el límite de HNSW es 2000) y pgvector lo trae desde la 0.5.0.

`DROP INDEX IF EXISTS`: en el CRM del curso el índice viejo ya no existe.

El `downgrade()` vuelve a dejar el IVFFlat tal cual estaba, con su defecto: es
lo que había antes de esta revisión.

Revision ID: 0062_indice_hnsw_chunks
Revises: 0061_unificacion_fichas
"""
import sqlalchemy as sa
from alembic import op

revision = "0062_indice_hnsw_chunks"
down_revision = "0061_unificacion_fichas"
branch_labels = None
depends_on = None

PGVECTOR_MINIMO = (0, 5, 0)


def _version(texto: str) -> tuple[int, ...]:
    partes = []
    for p in texto.split("."):
        digitos = "".join(ch for ch in p if ch.isdigit())
        partes.append(int(digitos or 0))
    return tuple(partes)


def upgrade() -> None:
    version = op.get_bind().execute(
        sa.text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    ).scalar()
    if version is None or _version(version) < PGVECTOR_MINIMO:
        # Mejor parar aquí con un mensaje claro que dejar la base sin índice o
        # con la búsqueda rota. Las imágenes pgvector/pgvector:pg16 y pg17 de
        # los compose traen una versión de sobra.
        raise RuntimeError(
            f"pgvector {version or 'sin instalar'} no tiene índices HNSW (hace falta 0.5.0 "
            "o más). Usa la imagen pgvector/pgvector:pg16 del docker-compose."
        )
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
        "ON chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding "
        "ON chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
    )
