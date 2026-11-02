"""«Sofía en n8n»: el turno de un agente lo resuelve un flujo de n8n (Fase 11, SPEC-07).

El CRM sigue haciendo lo suyo: recibe por todos los canales, guarda, filtra el
spam, espera las ráfagas, respeta la ventana de 24 h, parte y da formato por
canal, Entrenamiento, borradores de correo, inbox y tomar control. Lo único que
cambia es quién decide qué contestar: en vez de `run_agent`, el turno sale a
`agent.n8n_url` (`delegar_turno`) y lo que n8n conteste sigue el mismo camino
que una respuesta de Sofía.

Dos formas de contestar:
- **Directa**: el nodo «Respond to Webhook» devuelve `{respuesta, archivos?,
  acciones?}` antes de `n8n_espera_s`.
- **Por la API** (`api/agent_api_conversaciones.py`): n8n contesta 200 sin
  `respuesta` y manda luego `POST /agent-api/conversations/{id}/messages`.

Si n8n falla rápido (red, 5xx) se reintenta dos veces con el mismo
`evento_id`. Si sigue sin contestar, o si nada llega por la API a tiempo
(`comprobar_respuesta`), la conversación pasa a una persona con el mensaje
puente del agente, como cuando falta la clave de IA.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.core.url_guard import validate_public_https_url
from app.services.n8n_tools import CABECERA_SECRETO

logger = get_logger(__name__)

REINTENTOS_S = (5, 20)
# Cuánto se espera algo por la API antes de pasar la conversación a una persona.
COMPROBAR_TRAS_ESPERA_S = 120   # la respuesta directa no llegó a tiempo
COMPROBAR_TRAS_ASINCRONA_S = 600  # n8n dijo «contesto después»
MAX_RESPUESTA_BYTES = 256 * 1024

_ROL = {"user": "paciente", "assistant": "sofia", "operator": "operador"}


@dataclass
class DestinoN8n:
    """A dónde y con qué secreto se llama. `AgentRuntime` tiene los mismos campos."""

    n8n_url: str | None
    n8n_secreto: str | None
    n8n_espera_s: int = 25


class N8nNoContesta(RuntimeError):
    """n8n no contestó (o contestó un error) después de los reintentos."""


@dataclass
class TurnoN8n:
    # None = n8n contestará por la API (o la directa no llegó a tiempo).
    texto: str | None
    archivos: list = field(default_factory=list)
    # Si n8n pidió pasar la conversación a una persona y ya se hizo.
    derivada: bool = False
    # Segundos tras los que comprobar que llegó algo por la API.
    comprobar_en_s: int | None = None


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def _contacto(db, conv) -> dict:
    from sqlalchemy import select

    from app.models.contact import Contact, ContactTag
    from app.models.tag import Tag

    c = await db.get(Contact, conv.contact_id)
    if c is None:
        return {}
    etiquetas = (
        await db.execute(
            select(Tag.nombre).join(ContactTag, ContactTag.tag_id == Tag.id).where(ContactTag.contact_id == c.id)
        )
    ).scalars().all()
    return {
        "id": str(c.id),
        "nombre": c.nombre,
        "telefono": c.telefono,
        "email": c.email,
        "etapa": getattr(c.estado, "value", c.estado),
        "etiquetas": sorted(etiquetas),
    }


def _mensaje(m) -> dict:
    from app.services.media_publica import url_publica

    tipo = m.media_type or ("audio" if getattr(m, "audio_url", None) else "texto")
    item: dict[str, Any] = {"id": str(m.id), "tipo": tipo, "texto": m.contenido or "", "fecha": _iso(m.created_at)}
    if getattr(m, "audio_transcript", None):
        item["transcripcion"] = m.audio_transcript
    if m.media_url and m.media_url.startswith("/uploads/"):
        item["archivo_url"] = url_publica(m.media_url)
    return item


def _ventana(conv, ultimo_entrante: datetime | None) -> tuple[bool | None, str | None]:
    from app.core.channel_capabilities import capacidades

    horas = capacidades(conv.canal).ventana_horas
    if not horas:
        return None, None
    if ultimo_entrante is None:
        return False, None
    cierra = ultimo_entrante + timedelta(hours=horas)
    return datetime.now(timezone.utc) < cierra, cierra.isoformat()


async def construir_evento(
    conv, agent_cfg, mensajes: list, historial: list, *, is_training: bool, prueba: bool = False
) -> dict:
    """Lo que recibe n8n en cada turno. `mensajes` son los del paciente que
    dispararon el turno; `historial`, lo anterior (sin sugerencias sin enviar)."""
    from app.db.session import db_session

    async with db_session() as db:
        contacto = await _contacto(db, conv)
    entrantes = [m.created_at for m in [*historial, *mensajes] if getattr(m.rol, "value", m.rol) == "user"]
    abierta, cierra = _ventana(conv, max(entrantes) if entrantes else None)
    base = settings.APP_BASE_URL.rstrip("/")
    return {
        "evento": "mensajes",
        "evento_id": str(uuid.uuid4()),
        "prueba": prueba,
        "conversacion": {
            "id": str(conv.id),
            "canal": conv.canal.value,
            "entrenamiento": bool(is_training),
            "ventana_abierta": abierta,
            "ventana_cierra": cierra,
        },
        "contacto": contacto,
        "mensajes": [_mensaje(m) for m in mensajes],
        "historial": [
            {"rol": _ROL.get(getattr(m.rol, "value", m.rol), "sofia"), "texto": m.contenido or m.audio_transcript or "",
             "fecha": _iso(m.created_at)}
            for m in historial
            if (m.contenido or getattr(m, "audio_transcript", None))
            and getattr(m.rol, "value", m.rol) != "system"
            and not ((m.extra or {}).get("is_draft") and not (m.extra or {}).get("draft_sent"))
        ],
        "agente": {"id": str(agent_cfg.id) if agent_cfg.id else None, "nombre": agent_cfg.name},
        "api": {"base": f"{base}/api/v1/agent-api", "conversacion": f"/conversations/{conv.id}"},
    }


async def _post(url: str, secreto: str, cuerpo: dict, espera: float) -> httpx.Response:
    async with httpx.AsyncClient(timeout=espera, follow_redirects=False) as client:
        return await client.post(
            url, json=cuerpo, headers={CABECERA_SECRETO: secreto, "X-Omnicanal-Evento": "mensajes"}
        )


async def llamar(agent_cfg, evento: dict, *, reintentos: tuple[int, ...] | None = None) -> httpx.Response | None:
    """POST a n8n. Devuelve la respuesta 2xx, o None si la directa no llegó a
    tiempo (puede que n8n siga trabajando y conteste por la API: no se repite).
    Lanza `N8nNoContesta` si n8n falla de verdad después de los reintentos."""
    if not agent_cfg.n8n_url or not agent_cfg.n8n_secreto:
        raise N8nNoContesta("el agente no tiene URL o secreto de n8n")
    ok, motivo = await validate_public_https_url(agent_cfg.n8n_url)
    if not ok:
        raise N8nNoContesta(f"URL de n8n rechazada: {motivo}")
    reintentos = REINTENTOS_S if reintentos is None else reintentos
    ultimo = ""
    for intento, pausa in enumerate((0, *reintentos)):
        if pausa:
            await asyncio.sleep(pausa)
        try:
            r = await _post(agent_cfg.n8n_url, agent_cfg.n8n_secreto, evento, float(agent_cfg.n8n_espera_s))
        except httpx.TimeoutException:
            return None
        except httpx.HTTPError as exc:
            ultimo = f"no se pudo llamar a n8n ({type(exc).__name__})"
            continue
        if 200 <= r.status_code < 300:
            return r
        ultimo = f"n8n respondió HTTP {r.status_code}"
        if r.status_code < 500:
            break  # 3xx/4xx: repetir no lo arregla (secreto, webhook inactivo, Test URL…)
        logger.warning("n8n.motor.reintento", intento=intento + 1, estado=r.status_code)
    raise N8nNoContesta(ultimo or "n8n no contestó")


def _leer(r: httpx.Response) -> Any:
    import json

    crudo = r.content[:MAX_RESPUESTA_BYTES]
    if not crudo.strip():
        return None
    try:
        return json.loads(crudo)
    except (ValueError, UnicodeDecodeError):
        return crudo.decode("utf-8", errors="replace").strip()


async def aplicar_cambios_de_ficha(contact_id: uuid.UUID, cambios: dict) -> list[str]:
    """`{nombre?, email?, etapa?, etiquetas_agregar?}` sobre la ficha. Nunca el
    teléfono (es la llave). Devuelve qué se cambió. Lo usan las acciones de una
    respuesta directa y la API de n8n."""
    from sqlalchemy import select

    from app.agents.tools.lead_stage import etapa_de
    from app.db.session import db_session
    from app.models.contact import Contact, ContactTag
    from app.models.tag import Tag

    hecho: list[str] = []
    async with db_session() as db:
        c = await db.get(Contact, contact_id)
        if c is None:
            return hecho
        nombre = str(cambios.get("nombre") or "").strip()[:200]
        if nombre and nombre != c.nombre:
            c.nombre = nombre
            hecho.append("nombre")
        email = str(cambios.get("email") or "").strip().lower()[:320]
        if email and "@" in email and email != (c.email or ""):
            c.email = email
            hecho.append("email")
        if cambios.get("etapa"):
            etapa = etapa_de(cambios["etapa"])
            if etapa is not None and etapa != c.estado:
                c.estado = etapa
                hecho.append("etapa")
        for nombre_tag in list(cambios.get("etiquetas_agregar") or cambios.get("etiquetas") or [])[:10]:
            nombre_tag = str(nombre_tag).strip()[:60]
            if not nombre_tag:
                continue
            tag = (await db.execute(select(Tag).where(Tag.nombre == nombre_tag))).scalar_one_or_none()
            if tag is None:
                tag = Tag(nombre=nombre_tag)
                db.add(tag)
                await db.flush()
            ya = (
                await db.execute(
                    select(ContactTag).where(ContactTag.contact_id == c.id, ContactTag.tag_id == tag.id)
                )
            ).scalar_one_or_none()
            if ya is None:
                db.add(ContactTag(contact_id=c.id, tag_id=tag.id))
                hecho.append(f"etiqueta:{nombre_tag}")
        await db.commit()
    return hecho


async def delegar_turno(conv, agent_cfg, mensajes: list, historial: list, *, is_training: bool) -> TurnoN8n:
    """El turno a n8n. Lanza `N8nNoContesta` si n8n falla de verdad. Deja una
    línea en «Actividad del agente» con cuánto tardó y cómo contestó."""
    import time

    from app.services.trace_logger import TraceLevel, log_turno_n8n

    evento = await construir_evento(conv, agent_cfg, mensajes, historial, is_training=is_training)
    inicio = time.perf_counter()

    def ms() -> int:
        return int((time.perf_counter() - inicio) * 1000)

    try:
        r = await llamar(agent_cfg, evento)
    except N8nNoContesta as e:
        await log_turno_n8n(forma="sin respuesta", latency_ms=ms(), detalle=str(e),
                            conversation_id=conv.id, level=TraceLevel.error)
        raise
    if r is None:
        logger.info("n8n.motor.sin_respuesta_directa", conversation_id=str(conv.id))
        await log_turno_n8n(forma=f"sin respuesta directa en {agent_cfg.n8n_espera_s} s",
                            latency_ms=ms(), detalle="se espera por la API", conversation_id=conv.id,
                            level=TraceLevel.warn)
        return TurnoN8n(texto=None, comprobar_en_s=COMPROBAR_TRAS_ESPERA_S)
    turno = _interpretar(_leer(r))
    await log_turno_n8n(forma=_forma(turno), latency_ms=ms(), conversation_id=conv.id)
    return await _aplicar_acciones(conv, turno)


def _forma(turno: _Lectura) -> str:
    if turno.acciones.get("pasar_a_humano"):
        return "pasa a una persona"
    if turno.texto is None and not turno.archivos:
        return "contesta después por la API"
    return "respuesta directa"


@dataclass
class _Lectura:
    texto: str | None
    archivos: list = field(default_factory=list)
    acciones: dict = field(default_factory=dict)


def _interpretar(datos: Any) -> _Lectura:
    """La respuesta directa de n8n: texto suelto o `{respuesta, archivos, acciones}`."""
    if isinstance(datos, str):
        return _Lectura(texto=datos)
    if not isinstance(datos, dict):
        return _Lectura(texto=None)
    texto = datos.get("respuesta")
    texto = str(texto).strip() if texto is not None else None
    archivos = datos.get("archivos") if isinstance(datos.get("archivos"), list) else []
    acciones = datos.get("acciones") if isinstance(datos.get("acciones"), dict) else {}
    return _Lectura(texto=texto or None, archivos=archivos, acciones=acciones)


async def _aplicar_acciones(conv, lectura: _Lectura) -> TurnoN8n:
    texto, archivos, acciones = lectura.texto, lectura.archivos, lectura.acciones
    cambios = {k: acciones.get(k) for k in ("etiquetas", "etapa") if acciones.get(k)}
    cambios.update(acciones.get("ficha") or {})
    if cambios:
        await aplicar_cambios_de_ficha(conv.contact_id, cambios)

    if acciones.get("pasar_a_humano"):
        from app.agents.tools.human_handoff import _derivar_a_humano

        await _derivar_a_humano(
            conv.id,
            str(acciones["pasar_a_humano"])[:200],
            {"mensaje_al_cliente": texto or None},
        )
        return TurnoN8n(texto="", derivada=True)

    if not texto and not archivos:
        return TurnoN8n(texto=None, comprobar_en_s=COMPROBAR_TRAS_ASINCRONA_S)
    return TurnoN8n(texto=texto or "", archivos=archivos)


async def comprobar_respuesta(conversation_id: uuid.UUID, desde: datetime) -> bool:
    """¿Llegó algo de n8n (o de una persona) desde `desde`? Si no, y la
    conversación sigue en manos del bot, pasa a una persona con el mensaje
    puente del agente. Devuelve True si derivó."""
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.conversation import Conversation, ConversationStatus
    from app.models.message import Message, MessageRole
    from app.services.runtime_logs import push_runtime_log

    async with db_session() as db:
        conv = await db.get(Conversation, conversation_id)
        if conv is None or conv.status != ConversationStatus.bot:
            return False
        algo = (
            await db.execute(
                select(Message.id)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.rol.in_([MessageRole.assistant, MessageRole.operator]),
                    Message.created_at >= desde,
                )
                .limit(1)
            )
        ).first()
    if algo:
        return False
    await push_runtime_log(
        level="error",
        event="n8n.sin_respuesta",
        message="n8n no contestó a tiempo: la conversación pasa a una persona",
        conversation_id=str(conversation_id),
    )
    from app.agents.tools.human_handoff import _derivar_a_humano

    await _derivar_a_humano(conversation_id, "n8n no contestó a tiempo", {})
    return True


async def ids_de_sofia(conversation_id: uuid.UUID) -> set[uuid.UUID]:
    """Los mensajes de Sofía que ya hay: lo que aparezca después es de este envío."""
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message, MessageRole

    async with db_session() as db:
        return set(
            (
                await db.execute(
                    select(Message.id).where(
                        Message.conversation_id == conversation_id, Message.rol == MessageRole.assistant
                    )
                )
            ).scalars().all()
        )


async def marcar_nuevos_de_n8n(conversation_id: uuid.UUID, antes: set[uuid.UUID]) -> None:
    """Marca como de n8n los mensajes de Sofía que no estaban en `antes`. Por
    id y no por hora: `created_at` es la hora de la transacción que lo guardó,
    que puede haber empezado antes de que se mandara nada."""
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message, MessageRole

    async with db_session() as db:
        filas = (
            await db.execute(
                select(Message).where(
                    Message.conversation_id == conversation_id, Message.rol == MessageRole.assistant
                )
            )
        ).scalars().all()
        for m in filas:
            if m.id not in antes and (m.extra or {}).get("motor") != "n8n":
                m.extra = {**(m.extra or {}), "motor": "n8n"}
        await db.commit()
