"""Recordatorios y seguimientos (SPEC-18, tramo 2): lo que Sofía manda sola.

Dos avisos, cada uno con su interruptor en Asistente → Seguimientos (apagados
de fábrica):

- **Recordatorio**: `horas_antes` de la cita (24 por defecto). Solo a citas que
  se agendaron antes de ese momento: a quien agenda para dentro de tres horas
  no se le recuerda nada.
- **No llegó**: `horas_despues` de que alguien marque «No asistió» en la Agenda
  (1 por defecto), solo de los últimos 7 días. El CRM no sabe quién llegó, así
  que no se marca solo: mandarlo a ciegas le escribiría a gente que sí vino.

Y un tercero, **retomar** a quien preguntó y no agendó, que no lleva plantilla
(sale dentro de la ventana de 24 h) y vive en `services/retomar.py`; aquí solo
se configura y se lanza en la misma vuelta.

Por dónde sale cada aviso:

1. Si la ficha tiene WhatsApp de verdad (`+52…` o su BSUID) → la plantilla que
   se eligió, que es lo único que Meta deja mandar con la ventana cerrada.
2. Si no, y la cita se agendó por Instagram, Messenger o correo → el mismo
   texto, como mensaje normal, solo si la ventana de 24 h sigue abierta (en
   Instagram y Messenger). NUNCA con la etiqueta HUMAN_AGENT: Meta la prohíbe
   para mensajes automáticos.
3. Si no hay forma, se apunta en la ficha: «no se le pudo avisar».

El teléfono que el paciente DECLARÓ en Instagram (`telefono_declarado`) no se
usa a propósito: su respuesta por WhatsApp abriría una ficha nueva, porque esa
ficha todavía no está unida a su WhatsApp.

Cada aviso se apunta EN LA CITA dentro de la misma transacción que lo manda
(`UPDATE … WHERE … IS NULL`): dos workers o un reintento no pueden mandarlo dos
veces. Si WhatsApp falla se suelta y se reintenta en la siguiente vuelta, hasta
3 veces; la clave de idempotencia (`recordatorio:<cita>`) evita el doble envío
si el worker muere entre mandar y guardar.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field
from sqlalchemy import and_, select, text

from app.core.logging import get_logger
from app.db.session import db_session
from app.models.appointment import ESTADO_NO_ASISTIO, ESTADOS_ACTIVOS, Appointment
from app.models.contact import Contact
from app.models.contact_activity import ContactActivity
from app.models.conversation import Conversation, ConversationCanal
from app.models.message import Message, MessageRole

logger = get_logger(__name__)

SETTING_SEGUIMIENTOS = "seguimientos"

RECORDATORIO = "recordatorio"
INASISTENCIA = "inasistencia"
RETOMAR = "retomar"

# De dónde puede salir cada variable de la plantilla. Conjunto cerrado: nunca se
# leen campos arbitrarios de la ficha o de la cita.
DATOS: dict[str, str] = {
    "paciente": "Nombre del paciente (solo el nombre)",
    "negocio": "Nombre del negocio",
    "cita_fecha": "Fecha de la cita («martes 28 de octubre»)",
    "cita_hora": "Hora de la cita («10:30»)",
    "cita_servicio": "Servicio de la cita",
}

# Cuántas veces se intenta un aviso que WhatsApp rechaza antes de darlo por
# perdido (una por vuelta del beat, cada 10 min).
MAX_INTENTOS = 3
# Un aviso que ya no llega a tiempo no se manda: un recordatorio a 20 minutos
# de la cita llega cuando el paciente ya va en camino (o ya no va).
MARGEN_MINIMO = timedelta(minutes=30)
# «No llegó» solo de la última semana: encender el aviso no le escribe a toda la
# gente que faltó el año pasado.
VENTANA_INASISTENCIA = timedelta(days=7)
# Instagram y Messenger: el mensaje normal solo con la ventana de 24 h abierta.
# Media hora de margen: la ventana se cuenta desde el último mensaje del
# paciente y el envío no es instantáneo.
VENTANA_META_HORAS = 23.5

ACTIVIDAD = {
    RECORDATORIO: ("recordatorio_enviado", "recordatorio_omitido"),
    INASISTENCIA: ("inasistencia_enviada", "inasistencia_omitida"),
}
COLUMNA = {RECORDATORIO: "recordatorio_enviado_at", INASISTENCIA: "inasistencia_enviada_at"}


class AvisoConfig(BaseModel):
    activo: bool = False
    # Recordatorio: horas antes de la cita. No llegó: horas después de marcarla.
    horas: int = Field(default=24, ge=0, le=72)
    plantilla: str | None = Field(default=None, max_length=512)
    idioma: str = Field(default="es_MX", max_length=20)
    # `"1"` → `"paciente"`: de dónde sale cada variable de la plantilla.
    variables: dict[str, str] = Field(default_factory=dict)


class RetomarConfig(BaseModel):
    """Retomar a quien preguntó y no agendó (tramo 3): sin plantilla, porque
    sale dentro de la ventana de 24 h."""

    activo: bool = False
    # Horas que lleva el paciente sin escribir. Menos de 23: pasado ese punto la
    # ventana está por cerrarse (ver `services/retomar.py`).
    horas: int = Field(default=20, ge=1, le=22)


class SeguimientosConfig(BaseModel):
    recordatorio: AvisoConfig = Field(default_factory=lambda: AvisoConfig(horas=24))
    inasistencia: AvisoConfig = Field(default_factory=lambda: AvisoConfig(horas=1))
    retomar: RetomarConfig = Field(default_factory=RetomarConfig)
    # Lo que va en la variable «Nombre del negocio».
    negocio: str = Field(default="", max_length=80)
    # Nunca de noche: solo se manda entre estas horas, en la hora del negocio.
    hora_desde: int = Field(default=8, ge=0, le=23)
    hora_hasta: int = Field(default=20, ge=1, le=24)


async def leer_config() -> SeguimientosConfig:
    from app.services.app_settings import get_app_setting

    crudo = await get_app_setting(SETTING_SEGUIMIENTOS, "")
    if not crudo:
        return SeguimientosConfig()
    try:
        return SeguimientosConfig.model_validate(json.loads(crudo))
    except Exception:  # noqa: BLE001 — una configuración rota no tumba el beat
        logger.warning("seguimientos.config_ilegible")
        return SeguimientosConfig()


def problemas_de_config(cfg: SeguimientosConfig) -> list[str]:
    """Lo que impide guardar la configuración, en palabras."""
    fallos: list[str] = []
    for nombre, aviso in ((RECORDATORIO, cfg.recordatorio), (INASISTENCIA, cfg.inasistencia)):
        de = "del recordatorio" if nombre == RECORDATORIO else "de «no llegó»"
        if aviso.activo and not aviso.plantilla:
            fallos.append(f"Elige la plantilla {de}.")
        for n, dato in aviso.variables.items():
            if dato not in DATOS:
                fallos.append(f"La variable {{{{{n}}}}} {de} sale de un dato que no existe.")
    if cfg.recordatorio.activo and cfg.recordatorio.horas < 1:
        fallos.append("El recordatorio sale al menos 1 hora antes de la cita.")
    if cfg.hora_desde >= cfg.hora_hasta:
        fallos.append("La hora de inicio de los envíos tiene que ser antes que la de fin.")
    usa_negocio = any(
        "negocio" in a.variables.values() for a in (cfg.recordatorio, cfg.inasistencia) if a.activo
    )
    if usa_negocio and not cfg.negocio.strip():
        fallos.append("Escribe el nombre del negocio: una de las plantillas lo usa.")
    return fallos


# --- Los datos de la cita para las variables -----------------------------------

def fecha_legible(cuando: datetime) -> str:
    from app.services.runtime_config import _DIAS, _MESES

    return f"{_DIAS[cuando.weekday()]} {cuando.day} de {_MESES[cuando.month - 1]}"


def valores_de(cita: Appointment, contacto: Contact, cfg: SeguimientosConfig, tz) -> dict[str, str]:
    local = cita.starts_at.astimezone(tz)
    nombre = (contacto.nombre or "").strip().split()
    return {
        # Solo el nombre de pila: «Hola Ana», no «Hola Ana María López Pérez».
        # Sin nombre, «paciente»: Meta no deja mandar una variable vacía.
        "paciente": nombre[0].capitalize() if nombre else "paciente",
        "negocio": cfg.negocio.strip(),
        "cita_fecha": fecha_legible(local),
        "cita_hora": local.strftime("%H:%M"),
        "cita_servicio": cita.servicio,
    }


class Omitido(Exception):
    """El aviso no se puede mandar a esta persona; el motivo va a su ficha."""


def variables_para(nombres: list[str], mapa: dict[str, str], valores: dict[str, str]) -> list[str]:
    """Los valores en el orden de la plantilla, o `Omitido` si falta alguno."""
    salida = []
    for n in nombres:
        dato = mapa.get(n)
        if not dato:
            raise Omitido(f"falta decir de dónde sale la variable {{{{{n}}}}} en Seguimientos")
        valor = valores.get(dato, "").strip()
        if not valor:
            raise Omitido(f"la variable {{{{{n}}}}} ({DATOS.get(dato, dato)}) está vacía")
        salida.append(valor)
    return salida


# --- Por dónde sale --------------------------------------------------------------

def whatsapp_de(contacto: Contact) -> str | None:
    """El identificador de WhatsApp de la ficha (`+52…` o `wa:<bsuid>`), o None."""
    tel = (contacto.telefono or "").strip()
    if tel.startswith("+") or tel.startswith("wa:"):
        return tel
    return None


async def _freno(canal: str) -> str | None:
    """Por qué no se manda nada por este canal ahora, o None.

    Respeta lo mismo que Sofía al contestar: el asistente apagado, el canal en
    pausa (también la del tope de gasto) y el modo Entrenamiento, donde no sale
    nada sin que alguien lo apruebe.
    """
    from app.services.agent_pause import is_agent_paused, is_channel_paused, is_channel_training

    if await is_agent_paused():
        return "el asistente está apagado"
    if await is_channel_paused(canal):
        return f"el canal {canal} está en pausa"
    if await is_channel_training(canal):
        return f"el canal {canal} está en Entrenamiento"
    return None


async def _baja_o_bloqueo(db, destino: str) -> str | None:
    from app.models.outbound_optout import OutboundOptOut
    from app.services.agent_guardrails import is_phone_blocked

    baja = (
        await db.execute(select(OutboundOptOut.id).where(OutboundOptOut.phone == destino))
    ).scalar_one_or_none()
    if baja is not None:
        return "pidió no recibir mensajes (baja)"
    if await is_phone_blocked(destino):
        return "el número está bloqueado"
    return None


def problema_de_plantilla(plantilla, aviso: AvisoConfig, cfg: SeguimientosConfig) -> str | None:
    """Por qué con esta plantilla no sale ningún aviso, o None si está lista.

    Lo comprueba también el panel al guardar, para decirlo en el momento.
    """
    from app.api.admin import _template_status_problem

    if plantilla is None:
        return f"la plantilla «{aviso.plantilla}» ({aviso.idioma}) no está en tu cuenta de WhatsApp"
    estado = _template_status_problem(plantilla.status)
    if estado:
        return estado
    if plantilla.header_format not in (None, "", "text") or plantilla.header_variables or plantilla.button_variables:
        return "la plantilla lleva un archivo o variables en la cabecera o los botones: elige otra"
    faltan = [n for n in plantilla.variable_names if not aviso.variables.get(n)]
    if faltan:
        return "falta decir de dónde sale " + ", ".join(f"{{{{{n}}}}}" for n in faltan)
    if "negocio" in aviso.variables.values() and not cfg.negocio.strip():
        return "falta el nombre del negocio"
    return None


# --- El barrido ------------------------------------------------------------------

@dataclass
class Resultado:
    enviados: int = 0
    omitidos: int = 0
    pendientes: int = 0


def en_horario(ahora_local: datetime, cfg: SeguimientosConfig) -> bool:
    return cfg.hora_desde <= ahora_local.hour < cfg.hora_hasta


async def candidatas(tipo: str, aviso: AvisoConfig, ahora: datetime) -> list[uuid.UUID]:
    if tipo == RECORDATORIO:
        momento = Appointment.starts_at - timedelta(hours=aviso.horas)
        condicion = and_(
            Appointment.estado.in_(ESTADOS_ACTIVOS),
            Appointment.recordatorio_enviado_at.is_(None),
            Appointment.starts_at > ahora + MARGEN_MINIMO,
            Appointment.starts_at <= ahora + timedelta(hours=aviso.horas),
            Appointment.created_at <= momento,
        )
    else:
        condicion = and_(
            Appointment.estado == ESTADO_NO_ASISTIO,
            Appointment.inasistencia_enviada_at.is_(None),
            Appointment.updated_at <= ahora - timedelta(hours=aviso.horas),
            Appointment.starts_at >= ahora - VENTANA_INASISTENCIA,
        )
    async with db_session() as db:
        filas = await db.execute(
            select(Appointment.id).where(condicion).order_by(Appointment.starts_at).limit(200)
        )
        return [f[0] for f in filas]


async def _contar_fallo(tipo: str, cita_id: uuid.UUID) -> int:
    from app.core.redis import get_redis

    clave = f"seguimientos:fallos:{tipo}:{cita_id}"
    try:
        redis = get_redis()
        n = int(await redis.incr(clave))
        await redis.expire(clave, 3 * 24 * 3600)
        return n
    except Exception:  # noqa: BLE001 — sin Redis, se da por perdido a la primera
        return MAX_INTENTOS


async def _apuntar_resultado(db, cita_id: uuid.UUID, tipo: str, dato: dict) -> None:
    """Deja en `appointments.avisos` cómo acabó el aviso (lo enseña la Agenda).

    SQL directo y no el ORM: tocar la fila con el ORM movería `updated_at`, que
    es la hora de referencia de «no llegó».
    """
    await db.execute(
        text(
            "UPDATE appointments SET avisos = avisos || "
            "jsonb_build_object(CAST(:tipo AS text), CAST(:dato AS jsonb)) WHERE id = :id"
        ),
        {"tipo": tipo, "dato": json.dumps(dato, ensure_ascii=False), "id": cita_id},
    )


async def avisar(tipo: str, cita_id: uuid.UUID, cfg: SeguimientosConfig, plantilla, tz) -> str:
    """Manda un aviso de una cita. Devuelve «enviado», «omitido» o «pendiente».

    `plantilla` es la de la cuenta (`WhatsappTemplateOut`), ya comprobada como
    aprobada: su texto también es el del mensaje normal por Instagram o correo.
    """
    from app.services.channel_sender import last_inbound_age_hours, send_text_to_conversation
    from app.services.contact_activity import record_activity
    from app.services.delivery_status import marcar_enviado
    from app.services.plantilla_saliente import (
        conversacion_whatsapp,
        mandar_plantilla,
        texto_rellenado,
    )

    aviso = getattr(cfg, tipo)
    hecho, omitido = ACTIVIDAD[tipo]
    columna = COLUMNA[tipo]
    async with db_session() as db:
        cita = await db.get(Appointment, cita_id)
        if cita is None:
            return "omitido"
        contacto = await db.get(Contact, cita.contact_id)
        destino = whatsapp_de(contacto) if contacto else None
        conv: Conversation | None = None
        if destino is None and cita.conversation_id and cita.canal in (
            ConversationCanal.instagram_dm,
            ConversationCanal.messenger,
            ConversationCanal.email,
        ):
            conv = await db.get(Conversation, cita.conversation_id)
        canal = "whatsapp" if destino else (conv.canal.value if conv else None)

        # Con el canal en pausa no se apunta nada: sale cuando vuelva, si aún
        # llega a tiempo.
        if canal:
            freno = await _freno(canal)
            if freno:
                logger.info("seguimientos.en_pausa", tipo=tipo, motivo=freno)
                return "pendiente"

        # Se apunta primero, en esta misma transacción: otro worker que llegue a
        # la vez espera al commit y ya no la encuentra libre.
        tomada = (
            await db.execute(
                text(
                    f"UPDATE appointments SET {columna} = now() "  # noqa: S608 — columna de una lista fija
                    f"WHERE id = :id AND {columna} IS NULL RETURNING id"
                ),
                {"id": cita_id},
            )
        ).first()
        if tomada is None:
            return "omitido"

        try:
            if not canal or contacto is None:
                raise Omitido("no tiene WhatsApp y la cita no vino por un canal al que se le pueda escribir")
            valores = valores_de(cita, contacto, cfg, tz)
            variables = variables_para(plantilla.variable_names, aviso.variables, valores)
            extra = {"kind": tipo, "cita_id": str(cita.id), "sent_by": "sistema"}
            if destino:
                motivo = await _baja_o_bloqueo(db, destino)
                if motivo:
                    raise Omitido(motivo)
                conv = await conversacion_whatsapp(db, contacto, destino)
                msg = await mandar_plantilla(
                    db,
                    conv=conv,
                    destino=destino,
                    plantilla=plantilla,
                    variables=variables,
                    rol=MessageRole.assistant,
                    extra=extra,
                    idempotency_key=f"{tipo}:{cita.id}",
                )
            else:
                assert conv is not None
                if conv.canal != ConversationCanal.email:
                    edad = await last_inbound_age_hours(conv)
                    if edad is None or edad > VENTANA_META_HORAS:
                        raise Omitido(
                            "la ventana de 24 h de su canal está cerrada y no tiene WhatsApp"
                        )
                contenido = texto_rellenado(plantilla, variables)
                ext_id = await send_text_to_conversation(conv, contenido)
                msg = Message(
                    conversation_id=conv.id,
                    rol=MessageRole.assistant,
                    contenido=contenido,
                    extra=marcar_enviado(extra, ext_id, conv.canal),
                )
                db.add(msg)
                conv.last_message_at = datetime.now(timezone.utc)
        except Omitido as exc:
            await _apuntar_resultado(db, cita_id, tipo, {"resultado": "omitido", "motivo": str(exc)})
            await record_activity(
                db, cita.contact_id, omitido, meta={"cita_id": str(cita.id), "motivo": str(exc)}
            )
            await db.commit()
            return "omitido"
        except Exception as exc:  # noqa: BLE001 — EnvioFallido, ventana cerrada, red…
            await db.rollback()
            intentos = await _contar_fallo(tipo, cita_id)
            logger.warning("seguimientos.fallo", tipo=tipo, intento=intentos, error=str(exc)[:300])
            if intentos < MAX_INTENTOS:
                return "pendiente"
            motivo = f"no salió tras {intentos} intentos: {exc}"[:300]
            async with db_session() as db2:
                await db2.execute(
                    text(f"UPDATE appointments SET {columna} = now() WHERE id = :id"),  # noqa: S608
                    {"id": cita_id},
                )
                await _apuntar_resultado(db2, cita_id, tipo, {"resultado": "omitido", "motivo": motivo})
                await record_activity(
                    db2, cita.contact_id, omitido, meta={"cita_id": str(cita_id), "motivo": motivo}
                )
                await db2.commit()
            return "omitido"

        await _apuntar_resultado(
            db, cita_id, tipo, {"resultado": "enviado", "canal": conv.canal.value}
        )
        await record_activity(
            db, cita.contact_id, hecho, meta={"cita_id": str(cita.id), "canal": conv.canal.value}
        )
        await db.commit()
        conv_id, msg_id = conv.id, msg.id

    try:
        from app.core.events import event_bus, inbox_channel

        await event_bus.publish(
            inbox_channel(),
            "message.new",
            {"conversation_id": str(conv_id), "message_id": str(msg_id), "rol": "assistant"},
        )
    except Exception:  # noqa: BLE001 — el panel se entera al recargar
        pass
    return "enviado"


async def barrer(ahora: datetime | None = None) -> dict[str, Resultado]:
    """Una vuelta del beat: los avisos que tocan ahora. Sin nada encendido, no hace nada."""
    from app.agents.tools.schedule_config import get_schedule_config
    from app.services.agenda import avisar_panel
    from app.services.plantilla_saliente import plantilla_de_la_cuenta

    cfg = await leer_config()
    ahora = ahora or datetime.now(timezone.utc)
    resultados: dict[str, Resultado] = {}
    activos = [t for t in (RECORDATORIO, INASISTENCIA) if getattr(cfg, t).activo]
    if not activos and not cfg.retomar.activo:
        return resultados
    horario = await get_schedule_config()
    if not en_horario(ahora.astimezone(horario.tz), cfg):
        return resultados

    for tipo in activos:
        aviso: AvisoConfig = getattr(cfg, tipo)
        res = resultados.setdefault(tipo, Resultado())
        ids = await candidatas(tipo, aviso, ahora)
        if not ids:
            continue
        try:
            plantilla = await plantilla_de_la_cuenta(aviso.plantilla or "", aviso.idioma)
        except Exception as exc:  # noqa: BLE001 — sin WhatsApp no se apunta nada: sale luego
            logger.warning("seguimientos.sin_lista_de_plantillas", tipo=tipo, error=str(exc)[:200])
            res.pendientes += len(ids)
            continue
        problema = problema_de_plantilla(plantilla, aviso, cfg)
        if problema:
            # Un problema de configuración se queda en espera, sin apuntar
            # nada: si se marcara cada cita como «no se pudo», arreglar la
            # configuración ya no le devolvería el aviso a nadie.
            logger.warning("seguimientos.plantilla_no_lista", tipo=tipo, motivo=problema)
            res.pendientes += len(ids)
            continue
        for cita_id in ids:
            r = await avisar(tipo, cita_id, cfg, plantilla, horario.tz)
            if r == "enviado":
                res.enviados += 1
            elif r == "omitido":
                res.omitidos += 1
            else:
                res.pendientes += 1
    if any(r.enviados for t, r in resultados.items() if t != RETOMAR):
        await avisar_panel()
    if cfg.retomar.activo:
        from app.services.retomar import candidatas as conversaciones_quietas
        from app.services.retomar import retomar_una

        res = resultados.setdefault(RETOMAR, Resultado())
        for conv_id in await conversaciones_quietas(cfg.retomar.horas, ahora):
            r = await retomar_una(conv_id, cfg.retomar.horas)
            if r == "enviado":
                res.enviados += 1
            elif r == "omitido":
                res.omitidos += 1
            else:
                res.pendientes += 1
    return resultados


# --- Registro para el panel -----------------------------------------------------

# Del seguimiento solo los que salieron: «Sofía decidió no escribir» pasa en
# casi todas las conversaciones que se quedan quietas y taparía lo demás. Esos
# quedan en la actividad de cada ficha, con su motivo.
TIPOS_REGISTRO = (*(t for par in ACTIVIDAD.values() for t in par), "seguimiento_enviado")


async def registro(limite: int = 50) -> list[dict]:
    """Los últimos avisos (mandados y no mandados), lo más reciente arriba."""
    async with db_session() as db:
        filas = (
            await db.execute(
                select(ContactActivity, Contact.nombre)
                .join(Contact, Contact.id == ContactActivity.contact_id)
                .where(ContactActivity.tipo.in_(TIPOS_REGISTRO))
                .order_by(ContactActivity.created_at.desc())
                .limit(limite)
            )
        ).all()
    return [
        {
            "tipo": act.tipo,
            "contact_id": str(act.contact_id),
            "contacto": nombre,
            "cuando": act.created_at,
            "canal": (act.meta or {}).get("canal"),
            "motivo": (act.meta or {}).get("motivo"),
        }
        for act, nombre in filas
    ]

