"""La búsqueda por significado encuentra lo que se parece aunque Postgres use el índice.

El fallo (salió al grabar el video 07): la migración inicial creaba el índice
de los embeddings como IVFFlat sobre la tabla vacía. Sus listas se calculan al
crearse, así que sin datos quedaban sin sentido, y cuando Postgres usaba el
índice la búsqueda devolvía uno de cinco fragmentos parecidos, o ninguno. Pasaba
en toda instalación nueva: en «Probar el RAG» y en lo que busca Sofía.

La 0062 lo cambia por HNSW. Aquí se obliga a Postgres a usar el índice
(`enable_seqscan = off`) con fragmentos parecidos entre sí y una pregunta que
no coincide exacto con ninguno, que es el caso real.
"""
from __future__ import annotations

import asyncio
import random
import uuid

import pytest
from sqlalchemy import text


def _db_available() -> bool:
    try:
        async def _check():
            from app.db.session import db_session

            async with db_session() as db:
                await db.execute(text("SELECT 1"))

        asyncio.run(_check())
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)

_TEMA = [random.Random(1).uniform(-1, 1) for _ in range(1536)]


def _parecido(semilla: int) -> str:
    """Un vector del mismo tema con su ruido: se parece a los demás un ~80 %."""
    r = random.Random(semilla)
    return "[" + ",".join(f"{t + r.uniform(-0.6, 0.6):.5f}" for t in _TEMA) + "]"


def test_con_el_indice_forzado_salen_los_cinco_fragmentos_parecidos():
    from app.db.session import db_session

    async def _run():
        async with db_session() as db:
            indices = dict(
                (
                    await db.execute(
                        text(
                            "SELECT indexname, indexdef FROM pg_indexes "
                            "WHERE tablename = 'chunks' AND indexdef ILIKE '%embedding%'"
                        )
                    )
                ).all()
            )
            assert "ix_chunks_embedding_hnsw" in indices, indices
            assert "hnsw" in indices["ix_chunks_embedding_hnsw"]
            assert "ix_chunks_embedding" not in indices, "sigue el IVFFlat viejo"

            # Todo dentro de una transacción que se deshace al final.
            doc_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO documents (id, nombre, formato, tamano_bytes, status, num_chunks) "
                    "VALUES (:id, 'indice forzado', 'txt', 10, 'indexado', 30)"
                ),
                {"id": doc_id},
            )
            for i in range(30):
                await db.execute(
                    text(
                        "INSERT INTO chunks (document_id, contenido, embedding) "
                        "VALUES (:doc, :contenido, CAST(:emb AS vector))"
                    ),
                    {"doc": doc_id, "contenido": f"fragmento parecido {i}", "emb": _parecido(100 + i)},
                )
            pregunta = _parecido(999)
            await db.execute(text("SET LOCAL enable_seqscan = off"))

            plan = "\n".join(
                r[0]
                for r in (
                    await db.execute(
                        text(
                            "EXPLAIN SELECT id FROM chunks "
                            "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 5"
                        ),
                        {"q": pregunta},
                    )
                ).all()
            )
            assert "ix_chunks_embedding_hnsw" in plan, plan

            directos = (
                await db.execute(
                    text("SELECT document_id FROM match_chunks(CAST(:q AS vector), 0.3, 5)"),
                    {"q": pregunta},
                )
            ).all()
            hibridos = (
                await db.execute(
                    text(
                        "SELECT document_id FROM match_chunks_hybrid("
                        "CAST(:q AS vector), '', 5, 0.3, 40, 60, true)"
                    ),
                    {"q": pregunta},
                )
            ).all()
            await db.rollback()

        assert len(directos) == 5 and all(r[0] == doc_id for r in directos), (
            f"«Probar el RAG» devolvió {len(directos)} de 5 con el índice en uso"
        )
        assert len(hibridos) == 5 and all(r[0] == doc_id for r in hibridos), (
            f"la búsqueda de Sofía devolvió {len(hibridos)} de 5 con el índice en uso"
        )

    asyncio.run(_run())
