"""Una retención puesta a 0 DESACTIVA la purga; no borra la tabla entera.

El fallo: `purge_internal_logs` calculaba el corte con
`now - timedelta(days=settings.X_RETENTION_DAYS)` sin mirar el cero. Con 0 el
corte quedaba en "este instante" y la primera pasada se llevaba por delante
TODA la tabla: trazas del agente, uso de LLM, auditoría, envíos masivos
terminados y huecos de conocimiento resueltos. Y el manual dice justo lo
contrario, que 0 apaga la limpieza — que es además lo que ya hacían las
retenciones de audio y adjuntos (services/data_erasure) y la de correo
(services/gmail_retention).

Sin base de datos: se comprueba la decisión (`_cutoff_o_desactivada`) y que
`_purge_table` con corte None no abre ni una sesión.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.tasks.purge_internal_logs import _cutoff_o_desactivada, _purge_table

AHORA = datetime(2026, 8, 25, 12, 0, tzinfo=UTC)


def test_cero_desactiva_la_purga():
    assert _cutoff_o_desactivada(AHORA, 0, que="agent_trace_event") is None


def test_negativo_tambien_desactiva():
    """Un -1 mal puesto no puede significar "borra también lo de mañana"."""
    assert _cutoff_o_desactivada(AHORA, -1, que="audit_log") is None


def test_una_retencion_normal_corta_por_fecha():
    corte = _cutoff_o_desactivada(AHORA, 30, que="agent_trace_event")
    assert corte is not None
    assert (AHORA - corte).days == 30


def test_el_corte_nunca_es_ahora_mismo():
    """Con la retención a 0, el corte NO puede quedarse en `now`.

    Ese era exactamente el borrado masivo: corte = ahora → `created_at < ahora`
    es verdad para todas las filas de la tabla.
    """
    assert _cutoff_o_desactivada(AHORA, 0, que="llm_usage_log") != AHORA


async def test_purge_table_con_corte_none_no_toca_la_base(monkeypatch):
    """Ni siquiera abre sesión: la purga desactivada no habla con Postgres."""
    from app.db import session as db_mod

    def _explota(*a, **k):  # pragma: no cover - solo salta si hay regresión
        raise AssertionError("la purga desactivada NO debe abrir sesión de BD")

    monkeypatch.setattr(db_mod, "db_session", _explota)

    assert await _purge_table(object(), None) == 0


def test_todas_las_retenciones_internas_pasan_por_el_guardia():
    """Las cinco purgas del fichero, no solo las tres primeras.

    Se lee el fuente a propósito: el fallo original era que outbound_job y
    knowledge_gap calculaban el corte a mano, sin pasar por el guardia.
    """
    import inspect

    from app.tasks import purge_internal_logs as mod

    fuente = inspect.getsource(mod._purge_internal_logs)
    for retencion in (
        "TRACE_RETENTION_DAYS",
        "LLM_USAGE_RETENTION_DAYS",
        "AUDIT_RETENTION_DAYS",
        "OUTBOUND_RETENTION_DAYS",
        "RESOLVED_GAP_RETENTION_DAYS",
    ):
        assert f"settings.{retencion}" in fuente, retencion
    # Y ninguna resta días directamente al margen del guardia.
    assert "timedelta(days=settings." not in fuente
