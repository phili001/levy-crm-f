"""Tarea Celery periódica de purga de logs internos (I8 — disco).

Corre una vez al día (ver beat_schedule en app/tasks/__init__.py, 04:10). Las
tablas de observabilidad crecen sin límite y estaban llenando el disco; aquí
borramos lo que ya no aporta:

  - agent_trace_event (modelo AgentTraceEvent): eventos de traza del agente
    para debugging por conversación → se borran a los TRACE_RETENTION_DAYS
    días (30 por defecto: llevan previews de mensajes de cliente EN CLARO, así
    que la ventana es corta a propósito).
  - llm_usage_log (modelo LLMUsage): contadores de tokens/coste por llamada
    → LLM_USAGE_RETENTION_DAYS, 90 días (el budget tracker solo mira el mes
    corriente).
  - audit_log (modelo AuditLog): auditoría de acciones de usuarios → retención
    más larga, AUDIT_RETENTION_DAYS, 365 días.
  - outbound_job (envíos masivos terminados) → OUTBOUND_RETENTION_DAYS.
  - knowledge_gap ya resueltos → RESOLVED_GAP_RETENTION_DAYS.

En TODAS ellas, una retención de 0 (o negativa) significa NO PURGAR, igual que
en las retenciones de audio, adjuntos y correo. Ver `_cutoff_o_desactivada`.

Idempotente (borra por fecha; si no hay nada que borrar, no hace nada) y por
LOTES de 500 con commit por lote, para no aguantar locks largos ni inflar la
WAL con un DELETE gigante. Mismo molde Celery que purge_emails.py
(asyncio.run + retry suave).
"""
from datetime import datetime, timedelta, timezone

from celery import shared_task
from sqlalchemy import delete, select

from app.core.asincrono import ejecutar_async
from app.core.logging import get_logger

logger = get_logger(__name__)

BATCH_SIZE = 500


def _cutoff_o_desactivada(now: datetime, retention_days: int, *, que: str) -> datetime | None:
    """Fecha de corte de una retención, o None si la purga está DESACTIVADA.

    0 (o negativo) = no purgar. Es lo que entiende cualquiera al escribir 0 y lo
    que dice el manual, pero era justo lo contrario de lo que pasaba: sin esta
    comprobación, `now - timedelta(days=0)` da corte = "ahora" y la primera
    pasada BORRABA LA TABLA ENTERA (trazas, uso de LLM, auditoría, envíos y
    huecos resueltos). Mismo criterio que las retenciones de audio y adjuntos
    (services/data_erasure) y la de correo (services/gmail_retention).
    """
    days = int(retention_days or 0)
    if days <= 0:
        logger.info("purge_internal_logs.disabled", tabla=que)
        return None
    return now - timedelta(days=days)


async def _purge_table(model, cutoff: datetime | None) -> int:
    """Borra por lotes las filas de `model` con created_at < cutoff.

    Con `cutoff=None` (retención desactivada) no toca nada y devuelve 0.

    Devuelve el total borrado. Cada lote va en su propia transacción (sesión
    corta) para que un fallo a mitad no pierda el progreso ya hecho.
    """
    from app.db.session import db_session

    if cutoff is None:
        return 0

    total = 0
    while True:
        async with db_session() as db:
            ids = (
                await db.execute(
                    select(model.id).where(model.created_at < cutoff).limit(BATCH_SIZE)
                )
            ).scalars().all()
            if not ids:
                break
            await db.execute(delete(model).where(model.id.in_(ids)))
            await db.commit()
            total += len(ids)
        if len(ids) < BATCH_SIZE:
            break
    return total


async def _purge_internal_logs() -> dict:
    from app.core.config import settings
    from app.models.agent_trace import AgentTraceEvent
    from app.models.audit_log import AuditLog
    from app.models.knowledge_gap import KnowledgeGap
    from app.models.llm_usage import LLMUsage
    from app.models.outbound_job import OutboundJob

    now = datetime.now(timezone.utc)
    deleted_traces = await _purge_table(
        AgentTraceEvent,
        _cutoff_o_desactivada(now, settings.TRACE_RETENTION_DAYS, que="agent_trace_event"),
    )
    deleted_llm_usage = await _purge_table(
        LLMUsage,
        _cutoff_o_desactivada(now, settings.LLM_USAGE_RETENTION_DAYS, que="llm_usage_log"),
    )
    deleted_audit = await _purge_table(
        AuditLog,
        _cutoff_o_desactivada(now, settings.AUDIT_RETENTION_DAYS, que="audit_log"),
    )

    # Envíos masivos terminados: el job (cascade → destinatarios con teléfono)
    # deja de aportar pasada la ventana. Solo estados terminales.
    from app.db.session import db_session

    outbound_deleted = 0
    cutoff = _cutoff_o_desactivada(now, settings.OUTBOUND_RETENTION_DAYS, que="outbound_job")
    if cutoff is not None:
        async with db_session() as db:
            jobs = (
                await db.execute(
                    select(OutboundJob).where(
                        OutboundJob.created_at < cutoff,
                        OutboundJob.status.in_(["done", "failed", "canceled"]),
                    )
                )
            ).scalars().all()
            for j in jobs:
                await db.delete(j)
                outbound_deleted += 1
            if outbound_deleted:
                await db.commit()

    # Huecos de conocimiento YA RESUELTOS (aprobados/descartados): su contenido
    # útil ya vive en la KB o se descartó; la fila conserva la pregunta literal
    # del cliente (PII cifrada) sin necesidad. Los pendientes NO se tocan.
    gaps_deleted = 0
    gap_cutoff = _cutoff_o_desactivada(
        now, settings.RESOLVED_GAP_RETENTION_DAYS, que="knowledge_gap"
    )
    if gap_cutoff is not None:
        async with db_session() as db:
            gaps = (
                await db.execute(
                    select(KnowledgeGap).where(
                        KnowledgeGap.status != "pendiente",
                        KnowledgeGap.created_at < gap_cutoff,
                    )
                )
            ).scalars().all()
            for g in gaps:
                await db.delete(g)
                gaps_deleted += 1
            if gaps_deleted:
                await db.commit()

    # Vigilancia de disco: si el volumen de datos pasa del umbral, aviso en el
    # panel (una vez cada 10 min por el throttle de notify_security). El
    # operador lo ve en "Logs en vivo" y en Salud del sistema → Almacenamiento.
    try:
        import shutil

        usage = shutil.disk_usage(settings.AUDIO_STORAGE_PATH)
        pct = (usage.total - usage.free) / usage.total if usage.total else 0
        if pct >= 0.85:
            from app.services.security_alerts import notify_security

            await notify_security(
                kind="disk_usage",
                title=f"Disco al {pct:.0%} — revisar Salud del sistema → Almacenamiento",
                details={
                    "libre_gb": f"{usage.free / 1e9:.1f}",
                    "total_gb": f"{usage.total / 1e9:.1f}",
                },
                throttle_key="daily",
            )
    except Exception as e:
        logger.warning("purge.disk_check_failed", error=str(e))

    return {
        "agent_trace_event": deleted_traces,
        "llm_usage_log": deleted_llm_usage,
        "audit_log": deleted_audit,
        "outbound_jobs": outbound_deleted,
        "resolved_knowledge_gaps": gaps_deleted,
    }


@shared_task(name="app.tasks.purge_internal_logs.purge_internal_logs", bind=True, max_retries=2)
def purge_internal_logs(self) -> None:
    try:
        result = ejecutar_async(_purge_internal_logs())
        logger.info("purge_internal_logs.done", **(result or {}))
    except Exception as exc:
        logger.error("purge_internal_logs.error", error=str(exc))
        # Reintento suave: no es urgente; el beat de mañana reintenta igualmente.
        raise self.retry(exc=exc, countdown=60)
