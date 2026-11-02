"""API para agentes EXTERNOS del operador (/api/v1/agent-api).

Pensada para los asistentes propios del operador: monitorizar métricas
agregadas, revisar/editar la base de conocimiento y mejorar los prompts de los
agentes cuando el operador se lo pide.

Modelo de seguridad (deliberadamente estrecho):
  - Autenticación por token de agente (`Authorization: Bearer <token>`),
    guardado hasheado (SHA-256). Revocable, con caducidad opcional y ámbitos.
  - Router SEPARADO del panel. Con los ámbitos de siempre no hay ninguna ruta
    hacia contenido de conversaciones, contactos, credenciales, usuarios ni
    configuración de conexiones (ver la excepción de n8n más abajo).
  - `monitor:read` devuelve solo AGREGADOS (conteos, costes, salud) — nunca
    contenido de mensajes ni datos personales de clientes.
  - `interactions:read` devuelve solo METADATA de conversaciones (id, canal,
    estado, tiempos) — sin contenido y con el contacto solo como UUID.
  - Escrituras reversibles: KB con versionado (document_versions) y prompts
    con historial (agent_prompt_history). Todo auditado con el token que lo hizo.
  - Rate limit por token (ventana fija en Redis) además del de IP global.

Excepción a propósito (Fase 11, «Sofía en n8n»): `conversations:read`,
`conversations:reply` y `contacts:write` SÍ tocan contenido. Viven en
`api/agent_api_conversaciones.py` y existen para que un flujo de n8n que hace
de cerebro de un agente pueda leer el hilo, contestar y actualizar la ficha.
Ningún token los tiene salvo que el administrador los marque.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import get_db
from app.models.agent_token import AgentToken
from app.services.audit import record_audit

logger = get_logger(__name__)

# Cuelga de /api/v1 (main.py) → rutas finales /api/v1/agent-api/…
router = APIRouter(prefix="/agent-api", tags=["agent_api"])

# Prefijo con el que se emiten los tokens de agente. Es solo una etiqueta
# legible: sirve para reconocer de un vistazo que una cadena es un token de
# esta API (y es lo que se guarda en `token_prefix` para listarlos en el panel).
# NO se usa para validar: la validación es siempre por hash, de modo que los
# tokens emitidos por versiones anteriores —que llevan otro prefijo y no se
# pueden reemitir, porque el token en claro no se guarda— siguen funcionando
# tras actualizar. Cambiar este valor solo afecta a los tokens NUEVOS.
TOKEN_PREFIX = "agt_"
RATE_LIMIT_PER_MINUTE = 120


def hash_agent_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _rate_limit(token_id: uuid.UUID) -> None:
    """Ventana fija de 1 minuto por token, compartida entre workers (Redis).
    Best-effort: si Redis no está, no bloqueamos (queda el rate limit por IP)."""
    try:
        from app.core.redis import get_redis

        r = get_redis()
        minute = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        key = f"agent_token:rl:{token_id}:{minute}"
        n = await r.incr(key)
        if n == 1:
            await r.expire(key, 90)
        if n > RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Rate limit del token: máx {RATE_LIMIT_PER_MINUTE} peticiones/minuto.",
            )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_api.ratelimit.error", error=str(e))


async def _authenticate(authorization: str | None, db: AsyncSession) -> AgentToken:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"Falta el token (Authorization: Bearer {TOKEN_PREFIX}…)",
        )
    raw = authorization.removeprefix("Bearer ").strip()
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido")
    # A propósito NO se filtra por prefijo: se busca directamente el hash. Así
    # los tokens emitidos con un prefijo anterior siguen siendo válidos.
    token = (
        await db.execute(select(AgentToken).where(AgentToken.token_hash == hash_agent_token(raw)))
    ).scalar_one_or_none()
    if not token or not token.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido o revocado")
    if token.expires_at and token.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token caducado")

    await _rate_limit(token.id)

    # last_used_at con granularidad de minuto (evita un UPDATE por request).
    now = datetime.now(timezone.utc)
    if not token.last_used_at or (now - token.last_used_at).total_seconds() > 60:
        token.last_used_at = now
        await db.commit()
    return token


def require_scope(scope: str):
    """Dependency factory: autentica el token y exige el ámbito indicado."""

    async def dep(
        authorization: str | None = Header(default=None),
        db: AsyncSession = Depends(get_db),
    ) -> AgentToken:
        token = await _authenticate(authorization, db)
        if scope not in token.scope_set():
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"El token no tiene el ámbito requerido ({scope}).",
            )
        return token

    return dep


async def _audit_token(
    db: AsyncSession,
    token: AgentToken,
    action: str,
    entity: str,
    entity_id: uuid.UUID | None,
    after: dict | None = None,
) -> None:
    """Auditoría de acciones del token (user_id=None; la identidad va en after)."""
    try:
        await record_audit(
            db,
            user_id=None,
            action=action,
            entity=entity,
            entity_id=entity_id,
            after={**(after or {}), "agent_token": token.name, "agent_token_id": str(token.id)},
        )
        await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_api.audit.error", error=str(e))


# ── monitor:read — métricas AGREGADAS (sin contenido de mensajes ni PII) ─────


@router.get("/monitor/overview")
async def monitor_overview(
    range: str = "today",
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("monitor:read")),
) -> dict:
    """Visión agregada: conversaciones, handoffs, volumen por rol, canales
    pausados y uso/coste LLM del rango. Reutiliza las tools del agente interno
    (que ya solo devuelven agregados y datos enmascarados)."""
    from app.agents.internal.tools import (
        get_agent_usage,
        get_dashboard_stats,
        get_message_volume,
        list_paused_channels,
    )

    ctx = {"db": db, "actor_user_id": None}
    args = {"range": range}
    return {
        "stats": await get_dashboard_stats(args, ctx),
        "message_volume": await get_message_volume(args, ctx),
        "llm_usage": await get_agent_usage(args, ctx),
        "paused_channels": await list_paused_channels({}, ctx),
    }


@router.get("/monitor/llm-costs")
async def monitor_llm_costs(
    range: str = "7d",
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("monitor:read")),
) -> dict:
    """Coste/uso LLM del rango: desglose por agente, por modelo y serie por día.

    Reutiliza EXACTAMENTE la query del dashboard admin (admin.agent_usage), así
    los números cuadran con el panel. Solo agregados (tokens, llamadas, coste
    estimado) — nunca contenido. Rangos: 24h | 7d | 30d | month.
    """
    from app.api.admin import agent_usage as admin_agent_usage

    summary = await admin_agent_usage(range_=range, db=db, _=None)  # type: ignore[arg-type]
    return summary.model_dump()


@router.get("/monitor/channels")
async def monitor_channels(
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("monitor:read")),
) -> dict:
    """Estado operativo de cada canal: activo/pausado, última entrada recibida
    (proxy del último webhook) y errores recientes del runtime.

    Los errores se devuelven SANEADOS (solo ts/level/event/canal): las líneas
    del buffer de logs pueden llevar campos libres y aquí no se exponen. La
    lógica vive en `services/estado_canales.py`: la comparte con
    `GET /admin/channels/estado`, que es lo que mira `omni.py connect`.
    """
    from app.services.estado_canales import estado_de_canales

    return await estado_de_canales(db)


async def _worker_seconds_since_heartbeat(redis_client) -> int | None:
    """Segundos desde el último latido del worker (None si no hay marca)."""
    import time as _time

    from app.tasks.ping import WORKER_HEARTBEAT_KEY

    try:
        raw = await redis_client.get(WORKER_HEARTBEAT_KEY)
    except Exception:  # noqa: BLE001
        return None
    if not raw:
        return None
    try:
        return max(0, int(_time.time()) - int(raw))
    except (TypeError, ValueError):
        return None


async def _celery_backlog() -> int | None:
    """Tareas esperando en la cola por defecto del BROKER (best-effort).

    Conexión propia y de usar y tirar: el broker es otra base de datos de Redis
    distinta de la de la aplicación, así que no vale el cliente cacheado.
    """
    import redis.asyncio as _redis_async

    client = None
    try:
        client = _redis_async.from_url(
            settings.CELERY_BROKER_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        return int(await client.llen("celery"))  # type: ignore[misc]
    except Exception:  # noqa: BLE001
        return None
    finally:
        if client is not None:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass


@router.get("/monitor/health")
async def monitor_health(
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("monitor:read")),
) -> dict:
    """Salud operativa: BD, Redis y backlog de colas.

    Misma comprobación que el healthcheck público (/health) más el backlog:
    profundidad de la cola Celery (best-effort) y envíos masivos en curso.
    """
    from sqlalchemy import func, text

    from app.models.outbound_job import JOB_STATUS_QUEUED, JOB_STATUS_RUNNING, OutboundJob

    db_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_api.health.db", error=str(e))

    redis_ok = False
    worker_last_seen_s: int | None = None
    try:
        from app.core.redis import get_redis

        r = get_redis()
        await r.ping()
        redis_ok = True
        worker_last_seen_s = await _worker_seconds_since_heartbeat(r)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent_api.health.redis", error=str(e))

    # OJO: el backlog vive en el BROKER (CELERY_BROKER_URL, otra base de datos
    # de Redis), no en REDIS_URL. Consultar el Redis de la aplicación devuelve
    # siempre 0, y entonces el panel da "todo ok" aunque la cola esté llena y el
    # worker lleve horas caído: justo el fallo que esta pantalla debe detectar.
    celery_backlog = await _celery_backlog()

    outbound_pending: int | None = None
    if db_ok:
        try:
            outbound_pending = (
                await db.execute(
                    select(func.count())
                    .select_from(OutboundJob)
                    .where(OutboundJob.status.in_([JOB_STATUS_QUEUED, JOB_STATUS_RUNNING]))
                )
            ).scalar_one()
        except Exception:  # noqa: BLE001
            outbound_pending = None

    # El worker se da por caído si no ha latido en tres ciclos de beat. Sin
    # esto, un worker muerto era invisible: el backend respondía "ok" porque
    # BD y Redis son suyos, no del worker.
    from app.tasks.ping import WORKER_HEARTBEAT_TTL_S

    worker_ok = worker_last_seen_s is not None and worker_last_seen_s <= WORKER_HEARTBEAT_TTL_S
    healthy = db_ok and redis_ok and worker_ok
    return {
        "status": "ok" if healthy else "degraded",
        "db": "ok" if db_ok else "fail",
        "redis": "ok" if redis_ok else "fail",
        "worker": "ok" if worker_ok else "fail",
        "worker_last_seen_seconds": worker_last_seen_s,
        "queues": {
            "celery_backlog": celery_backlog,
            "outbound_jobs_pending": outbound_pending,
        },
    }


# ── interactions:read — metadata de conversaciones (sin contenido ni PII) ────
#
# SOLO metadata operativa: id, canal, estado, tiempos y si está asignada.
# Nunca contenido de mensajes, ni nombre/teléfono/email del contacto (el
# contacto viaja únicamente como UUID), ni resumen, ni asunto de email, ni
# URL de grabación de voz.


def _interaction_meta(conv, now: datetime) -> dict:
    """Proyección de metadata limpia de una Conversation (sin PII)."""
    from app.models.conversation import ConversationStatus

    if conv.status == ConversationStatus.humano:
        status_since = conv.derivada_a_humano_at or conv.started_at
    elif conv.status == ConversationStatus.cerrada:
        status_since = conv.ended_at or conv.last_message_at or conv.started_at
    else:
        status_since = conv.started_at
    seconds_in_status = (
        max(0, int((now - status_since).total_seconds())) if status_since else None
    )
    return {
        "id": str(conv.id),
        "canal": conv.canal.value,
        "status": conv.status.value,
        "created_at": conv.started_at.isoformat() if conv.started_at else None,
        "last_activity_at": conv.last_message_at.isoformat() if conv.last_message_at else None,
        "seconds_in_status": seconds_in_status,
        "assigned": conv.asignada_a is not None,
        "contact_id": str(conv.contact_id),
    }


@router.get("/interactions")
async def list_interactions(
    status_filter: str | None = Query(default=None, alias="status"),
    canal: str | None = None,
    contact_id: uuid.UUID | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("interactions:read")),
) -> dict:
    """Lista conversaciones con SOLO metadata operativa (sin contenido ni PII).

    Filtros: status (bot | humano | cerrada), canal (whatsapp | web |
    instagram_dm | email | retell_voice), contact_id (UUID del contacto) y
    limit 1–100 (por defecto 20). Ordenadas por última actividad descendente.
    El query param del estado se llama `status` (alias del parámetro interno).

    El filtro por `contact_id` existe para poder pedir las conversaciones de un
    contacto concreto sin depender de que entren dentro del `limit`: sin él, la
    única forma de encontrar una conversación conocida era paginar la lista
    entera, y las que no tienen actividad (last_message_at nulo) caen al final.
    """
    from app.models.conversation import Conversation, ConversationCanal, ConversationStatus

    limit = max(1, min(int(limit or 20), 100))
    conds = []
    if status_filter is not None:
        if status_filter not in {s.value for s in ConversationStatus}:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"status inválido. Válidos: {sorted(s.value for s in ConversationStatus)}",
            )
        conds.append(Conversation.status == ConversationStatus(status_filter))
    if canal is not None:
        if canal not in {c.value for c in ConversationCanal}:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"canal inválido. Válidos: {sorted(c.value for c in ConversationCanal)}",
            )
        conds.append(Conversation.canal == ConversationCanal(canal))
    if contact_id is not None:
        conds.append(Conversation.contact_id == contact_id)

    stmt = select(Conversation)
    if conds:
        stmt = stmt.where(*conds)
    stmt = stmt.order_by(Conversation.last_message_at.desc().nulls_last()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()

    now = datetime.now(timezone.utc)
    return {
        "count": len(rows),
        "interactions": [_interaction_meta(c, now) for c in rows],
    }


@router.get("/interactions/{conversation_id}/metadata")
async def interaction_metadata(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("interactions:read")),
) -> dict:
    """Metadata de UNA conversación (igual de limpia que el listado).

    Añade: ended_at, derivada_a_humano_at, archived/quarantined y conteo de
    mensajes por rol. Sin contenido, sin datos del contacto (solo su UUID).
    """
    from sqlalchemy import func

    from app.models.conversation import Conversation
    from app.models.message import Message

    conv = (
        await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    ).scalar_one_or_none()
    if not conv:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversación no encontrada")

    msg_rows = (
        await db.execute(
            select(Message.rol, func.count())
            .where(Message.conversation_id == conversation_id)
            .group_by(Message.rol)
        )
    ).all()
    messages_by_rol = {r[0].value: int(r[1]) for r in msg_rows}

    now = datetime.now(timezone.utc)
    meta = _interaction_meta(conv, now)
    meta.update(
        {
            "ended_at": conv.ended_at.isoformat() if conv.ended_at else None,
            "derivada_a_humano_at": (
                conv.derivada_a_humano_at.isoformat() if conv.derivada_a_humano_at else None
            ),
            "archived": conv.archived_at is not None,
            "quarantined": conv.quarantined_at is not None,
            "messages_by_rol": messages_by_rol,
            "message_count": sum(messages_by_rol.values()),
        }
    )
    return meta


# ── kb:read ──────────────────────────────────────────────────────────────────


@router.get("/kb/documents")
async def kb_documents(
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("kb:read")),
) -> dict:
    from app.agents.internal.tools import kb_list_documents

    return await kb_list_documents({}, {"db": db})


@router.get("/kb/documents/{document_id}")
async def kb_document_content(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("kb:read")),
) -> dict:
    """Contenido completo: texto del archivo (txt/md) o texto extraído de los
    chunks (pdf/docx/csv/xlsx)."""
    from app.api.knowledge_base import _read_document_text
    from app.models.chunk import Chunk
    from app.models.document import Document

    doc = (await db.execute(select(Document).where(Document.id == document_id))).scalar_one_or_none()
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")
    if doc.formato.value in {"txt", "md"}:
        contenido = _read_document_text(doc)
    else:
        chunks = (
            await db.execute(
                select(Chunk.contenido).where(Chunk.document_id == document_id).order_by(Chunk.id)
            )
        ).scalars().all()
        contenido = "\n\n".join(chunks)
    return {
        "document_id": str(doc.id),
        "nombre": doc.nombre,
        "formato": doc.formato.value,
        "editable": doc.formato.value in {"txt", "md"},
        "contenido": contenido,
    }


class KBSearchIn(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(5, ge=1, le=8)


@router.post("/kb/search")
async def kb_search_endpoint(
    body: KBSearchIn,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("kb:read")),
) -> dict:
    from app.agents.internal.tools import kb_search

    return await kb_search({"query": body.query, "top_k": body.top_k}, {"db": db})


# ── kb:write ─────────────────────────────────────────────────────────────────


class KBCreateIn(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=200)
    contenido: str = Field(..., min_length=1, max_length=200_000)


@router.post("/kb/documents")
async def kb_create_document(
    body: KBCreateIn,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("kb:write")),
) -> dict:
    from app.api.knowledge_base import create_text_document

    doc = await create_text_document(db, body.titulo, body.contenido, token.created_by)
    await _audit_token(
        db, token, "kb.note.created", "document", doc.id, {"nombre": doc.nombre}
    )
    return {
        "document_id": str(doc.id),
        "nombre": doc.nombre,
        "status": doc.status.value,
        "num_chunks": doc.num_chunks,
    }


class KBUpdateIn(BaseModel):
    contenido: str = Field(..., min_length=1, max_length=200_000)


@router.put("/kb/documents/{document_id}")
async def kb_update_document(
    document_id: uuid.UUID,
    body: KBUpdateIn,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("kb:write")),
) -> dict:
    """Edita un documento txt/md. La versión anterior queda en el historial
    (reversible desde el panel) y se reindexa al momento."""
    from app.api.knowledge_base import _apply_document_content, _get_editable_document

    contenido = body.contenido.strip()
    if not contenido:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El contenido no puede estar vacío")
    doc = await _get_editable_document(db, document_id)
    await _apply_document_content(db, doc, contenido, token.created_by)
    await _audit_token(
        db, token, "kb.document.edited", "document", doc.id, {"nombre": doc.nombre}
    )
    return {
        "document_id": str(doc.id),
        "nombre": doc.nombre,
        "status": doc.status.value,
        "num_chunks": doc.num_chunks,
    }


# ── prompts:write — leer y mejorar prompts de los agentes ────────────────────


@router.get("/agents")
async def agents_list(
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("prompts:write")),
) -> dict:
    from app.models.agent import Agent

    rows = (await db.execute(select(Agent).order_by(Agent.created_at))).scalars().all()
    return {
        "agents": [
            {
                "agent_id": str(a.id),
                "name": a.name,
                "is_active": a.is_active,
                "model_name": a.model_name,
                "prompt_system": a.prompt_system,
            }
            for a in rows
        ],
    }


class PromptUpdateIn(BaseModel):
    prompt_system: str = Field(..., min_length=1, max_length=50_000)


@router.put("/agents/{agent_id}/prompt")
async def update_agent_prompt(
    agent_id: uuid.UUID,
    body: PromptUpdateIn,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("prompts:write")),
) -> dict:
    """Reemplaza el prompt del agente. El anterior queda en agent_prompt_history
    (restaurable desde el panel de Agentes). Las reglas de seguridad del sistema
    (SECURITY_GUARD) NO forman parte del prompt: no se pueden quitar por aquí. Y
    en el agente de voz, tampoco las de su sección "NO TOCAR", igual que en el
    panel: si no, un asistente externo sería la puerta de atrás."""
    from app.api.admin import _proteger_reglas_de_voz, _record_agent_prompt
    from app.models.agent import Agent

    prompt = body.prompt_system.strip()
    if not prompt:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El prompt no puede estar vacío")

    a = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one_or_none()
    if not a:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Agente no encontrado")
    if prompt != a.prompt_system and a.kind == "voice":
        _proteger_reglas_de_voz(a.prompt_system, prompt)
    if prompt != a.prompt_system:
        _record_agent_prompt(db, a.id, a.prompt_system, a.model_name, token.created_by)
        a.prompt_system = prompt
        await db.commit()
        await db.refresh(a)
        await _audit_token(db, token, "agent.prompt_update", "agent", a.id, {"name": a.name})
    return {"agent_id": str(a.id), "name": a.name, "prompt_system": a.prompt_system}


# ── classifier:write — afinar el clasificador de no deseados ─────────────────
#
# Mismo trato que los prompts: el operador delega en su agente el trabajo de
# afinar QUÉ se descarta. Lo que se toca aquí son las instrucciones del
# clasificador y sus reglas duras; NO se toca el modelo, ni el proveedor, ni la
# acción sobre Gmail, que son decisiones de coste y de buzón y siguen siendo
# solo del panel.
#
# Encender o apagar el clasificador tampoco se hace por aquí: apagarlo dejaría
# pasar todo el spam sin que nadie se entere hasta verlo en la bandeja.


class ClassifierOut(BaseModel):
    model_config = {"protected_namespaces": ()}

    enabled: bool
    channels: list[str]
    instructions: str
    model_name: str | None = None


@router.get("/classifier", response_model=ClassifierOut)
async def classifier_get(
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("classifier:write")),
) -> ClassifierOut:
    from app.api.admin import _get_or_create_classifier

    cfg = await _get_or_create_classifier(db)
    return ClassifierOut(
        enabled=bool(cfg.enabled),
        channels=list(cfg.channels or []),
        instructions=cfg.instructions or "",
        model_name=cfg.model_name,
    )


class ClassifierUpdateIn(BaseModel):
    instructions: str = Field(..., min_length=1, max_length=20_000)


@router.put("/classifier", response_model=ClassifierOut)
async def classifier_update(
    body: ClassifierUpdateIn,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("classifier:write")),
) -> ClassifierOut:
    """Reemplaza las instrucciones del clasificador. Surten efecto al momento
    (no hay caché) y quedan auditadas con el token que las cambió."""
    from app.api.admin import _get_or_create_classifier

    instrucciones = body.instructions.strip()
    if not instrucciones:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Las instrucciones no pueden ir vacías"
        )
    cfg = await _get_or_create_classifier(db)
    anteriores = cfg.instructions or ""
    if instrucciones != anteriores:
        cfg.instructions = instrucciones
        cfg.updated_by = token.created_by
        await db.commit()
        await db.refresh(cfg)
        await _audit_token(
            db,
            token,
            "classifier.config_updated",
            "classifier_config",
            cfg.id,
            {"chars_antes": len(anteriores), "chars_despues": len(instrucciones)},
        )
    return ClassifierOut(
        enabled=bool(cfg.enabled),
        channels=list(cfg.channels or []),
        instructions=cfg.instructions or "",
        model_name=cfg.model_name,
    )


class ClassifierRuleIn(BaseModel):
    campo: str = Field(..., description="remitente | dominio | asunto")
    valor: str = Field(..., min_length=1, max_length=255)
    canal: str | None = Field(default=None, max_length=30)
    nota: str | None = Field(default=None, max_length=255)


def _regla_out(r) -> dict:
    return {
        "rule_id": str(r.id),
        "enabled": r.enabled,
        "campo": r.campo,
        "valor": r.valor,
        "canal": r.canal,
        "nota": r.nota,
        "hits": r.hits,
    }


@router.get("/classifier/rules")
async def classifier_rules_list(
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("classifier:write")),
) -> dict:
    from app.models.classifier_rule import ClassifierRule

    rows = (
        (await db.execute(select(ClassifierRule).order_by(ClassifierRule.created_at.desc())))
        .scalars()
        .all()
    )
    return {"rules": [_regla_out(r) for r in rows]}


@router.post("/classifier/rules", status_code=201)
async def classifier_rule_create(
    body: ClassifierRuleIn,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("classifier:write")),
) -> dict:
    """Alta de una regla dura (lo que encaja va a Descartados sin pasar por el
    modelo). Misma validación que el panel: nada de dominios de un solo trozo
    ni asuntos de dos letras, que barrerían el buzón entero."""
    from app.api.admin import _valida_valor
    from app.models.classifier_rule import CAMPOS, ClassifierRule
    from app.services.classifier_rules import invalidar_cache

    if body.campo not in CAMPOS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Campo no válido. Los que hay: {', '.join(CAMPOS)}.",
        )
    valor = _valida_valor(body.campo, body.valor)
    if body.campo == "asunto" and body.canal not in (None, "email"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Las reglas de asunto solo tienen sentido en email.",
        )
    existe = (
        await db.execute(
            select(ClassifierRule).where(
                ClassifierRule.campo == body.campo,
                ClassifierRule.valor == valor,
                ClassifierRule.canal.is_(None)
                if body.canal is None
                else ClassifierRule.canal == body.canal,
            )
        )
    ).scalar_one_or_none()
    if existe:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esa regla ya existe.")
    rule = ClassifierRule(
        campo=body.campo,
        valor=valor,
        canal=body.canal,
        nota=(body.nota or None),
        enabled=True,
        created_by=token.created_by,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    # La caché de reglas la miran todos los workers en cada entrante: sin
    # tirarla, la regla nueva tardaría hasta cinco minutos en aplicarse.
    await invalidar_cache()
    await _audit_token(
        db,
        token,
        "classifier.rule_created",
        "classifier_rule",
        rule.id,
        {"campo": rule.campo, "valor": rule.valor, "canal": rule.canal},
    )
    return _regla_out(rule)


@router.delete("/classifier/rules/{rule_id}")
async def classifier_rule_delete(
    rule_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    token: AgentToken = Depends(require_scope("classifier:write")),
) -> dict:
    """Borra una regla. Va en el mismo ámbito que el alta a propósito: quien
    puede poner una regla que silencia gente tiene que poder quitarla."""
    from app.models.classifier_rule import ClassifierRule
    from app.services.classifier_rules import invalidar_cache

    rule = (
        await db.execute(select(ClassifierRule).where(ClassifierRule.id == rule_id))
    ).scalar_one_or_none()
    if not rule:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Regla no encontrada")
    datos = {"campo": rule.campo, "valor": rule.valor, "canal": rule.canal}
    await db.delete(rule)
    await db.commit()
    await invalidar_cache()
    await _audit_token(db, token, "classifier.rule_deleted", "classifier_rule", rule_id, datos)
    return {"ok": True, "deleted": datos}
