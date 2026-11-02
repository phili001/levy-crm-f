"""Proponer unir las fichas del mismo paciente (Fase 10.1).

El mismo paciente escribe por WhatsApp, pregunta por Instagram y llama por
teléfono. Instagram, Messenger, el chat web y el correo no dan su teléfono, así
que cada canal le abría una ficha. Cuando la persona DICE su teléfono (Sofía lo
guarda en `telefono_declarado`) o escribe desde un correo que ya figura en otra
ficha, aquí se busca la coincidencia y se deja una propuesta
(`merge_suggestions`) para que la vea el equipo: «¿Es el mismo paciente?».

Nunca se fusiona sola. Un teléfono escrito en un chat no está verificado:
cualquiera puede dar el de otra persona, y una fusión automática le entregaría
la ficha (historial, citas, notas) de otro paciente. La única fusión sin persona
sigue siendo la de WhatsApp por el identificador de Meta
(`services/conversation.py`), que sí la verifica el canal.

La entrada pública es `proponer_para`: abre su propia sesión, confirma y avisa
al panel. Es best-effort: proponer no puede costar un mensaje ni tumbar la
herramienta de Sofía, así que nunca lanza.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.contact import Contact
from app.models.merge_suggestion import MOTIVO_EMAIL, MOTIVO_TELEFONO, MergeSuggestion

logger = get_logger(__name__)

_E164 = re.compile(r"\+\d{10,15}")
# Prefijos de celular que México retiró en 2019 (se marcaban delante de los
# 10 dígitos). Hay quien los sigue dictando.
_PREFIJOS_MOVIL_MX = ("044", "045")


def telefono_declarado_e164(texto: str | None) -> str | None:
    """El teléfono que dijo la persona, en E.164, o None si no lo parece.

    Regla MX: el producto se instala para clínicas de México, donde se marca a
    10 dígitos desde 2019. Un número de 10 dígitos sin prefijo internacional es
    mexicano («998 123 4567» → +529981234567), y con 044/045 delante también. El
    resto pasa por la normalización de siempre (`api/contacts.normalize_phone`),
    que además le quita el 1 a los celulares viejos (+521… → +52…). Así el
    número dicho coincide con el que guarda WhatsApp o la llamada.
    """
    from app.api.contacts import is_channel_identifier, normalize_phone

    s = (texto or "").strip()
    if not s or is_channel_identifier(s):
        return None
    digitos = re.sub(r"\D", "", s)
    if not s.startswith(("+", "00")):
        if len(digitos) == 10:
            s = "+52" + digitos
        elif len(digitos) == 13 and digitos.startswith(_PREFIJOS_MOVIL_MX):
            s = "+52" + digitos[3:]
    e164 = normalize_phone(s)
    return e164 if _E164.fullmatch(e164) else None


async def proponer_para(
    contact_id: uuid.UUID,
    *,
    telefono_declarado: bool = False,
    telefono: bool = False,
    email: bool = False,
) -> int:
    """Busca coincidencias de esta ficha y deja las propuestas. Nunca lanza.

    - `telefono_declarado`: la ficha acaba de guardar el teléfono que dijo la
      persona; ¿es el de otra ficha?
    - `telefono`: nace una ficha con teléfono de verdad (WhatsApp, llamada);
      ¿alguien lo había dicho en otro canal?
    - `email`: la ficha es de correo, o Sofía le acaba de guardar un correo;
      ¿hay otra ficha con esa dirección?

    Devuelve cuántas propuestas nuevas dejó (0 si falla: queda en los logs).
    """
    from app.db.session import db_session

    try:
        async with db_session() as db:
            ficha = (
                await db.execute(select(Contact).where(Contact.id == contact_id))
            ).scalar_one_or_none()
            if ficha is None:
                return 0
            nuevas = 0
            if telefono_declarado:
                nuevas += await _por_telefono_declarado(db, ficha)
            if telefono:
                nuevas += await _por_telefono(db, ficha)
            if email:
                nuevas += await _por_email(db, ficha)
            await db.commit()
    except Exception as e:  # noqa: BLE001 — una propuesta nunca cuesta un mensaje
        logger.warning("identity_linking.fallo", contact_id=str(contact_id), error=str(e))
        from app.services.runtime_logs import push_runtime_log

        await push_runtime_log(
            level="warn",
            event="contacts.fusion_propuesta.fallo",
            message=(
                "No se pudo comprobar si esta ficha es el mismo paciente que otra. "
                "Si lo es, se pueden unir a mano desde Contactos."
            ),
            contact_id=str(contact_id),
        )
        return 0
    if nuevas:
        await avisar_panel()
    return nuevas


async def avisar_panel() -> None:
    """`fusion.changed` por el bus de la bandeja: el aviso «¿Es el mismo
    paciente?» aparece (o desaparece) sin recargar. Sin datos en el evento:
    cada pantalla vuelve a pedir lo suyo."""
    try:
        from app.core.events import event_bus, inbox_channel

        await event_bus.publish(inbox_channel(), "fusion.changed", {})
    except Exception as e:  # noqa: BLE001 — sin aviso, el panel se entera al recargar
        logger.warning("identity_linking.aviso_panel_fallo", error=str(e))


async def _por_telefono_declarado(db: AsyncSession, ficha: Contact) -> int:
    if not ficha.telefono_declarado:
        return 0
    otra = (
        await db.execute(
            select(Contact).where(
                Contact.telefono == ficha.telefono_declarado, Contact.id != ficha.id
            )
        )
    ).scalar_one_or_none()
    if otra is None:
        return 0
    return await _proponer(
        db, absorbe=ficha, queda=otra, motivo=MOTIVO_TELEFONO, dato=ficha.telefono_declarado
    )


async def _por_telefono(db: AsyncSession, ficha: Contact) -> int:
    from app.api.contacts import is_channel_identifier

    if not ficha.telefono or is_channel_identifier(ficha.telefono):
        return 0
    otras = (
        await db.execute(
            select(Contact).where(
                Contact.telefono_declarado == ficha.telefono, Contact.id != ficha.id
            )
        )
    ).scalars().all()
    nuevas = 0
    for otra in otras:
        nuevas += await _proponer(
            db, absorbe=otra, queda=ficha, motivo=MOTIVO_TELEFONO, dato=ficha.telefono
        )
    return nuevas


async def _por_email(db: AsyncSession, ficha: Contact) -> int:
    """Se queda siempre la ficha que NO es la del correo: la de correo no tiene
    teléfono ni ningún otro dato que la otra no pueda heredar."""
    if ficha.telefono.startswith("email:"):
        direccion = ficha.telefono.split(":", 1)[1].strip().lower()
        if not direccion:
            return 0
        otras = (
            await db.execute(
                select(Contact).where(
                    func.lower(Contact.email) == direccion,
                    Contact.id != ficha.id,
                    ~Contact.telefono.startswith("email:"),
                )
            )
        ).scalars().all()
        nuevas = 0
        for otra in otras:
            nuevas += await _proponer(
                db, absorbe=ficha, queda=otra, motivo=MOTIVO_EMAIL, dato=direccion
            )
        return nuevas
    direccion = (ficha.email or "").strip().lower()
    if not direccion:
        return 0
    la_de_correo = (
        await db.execute(
            select(Contact).where(
                func.lower(Contact.telefono) == f"email:{direccion}", Contact.id != ficha.id
            )
        )
    ).scalar_one_or_none()
    if la_de_correo is None:
        return 0
    return await _proponer(
        db, absorbe=la_de_correo, queda=ficha, motivo=MOTIVO_EMAIL, dato=direccion
    )


async def _proponer(
    db: AsyncSession, *, absorbe: Contact, queda: Contact, motivo: str, dato: str
) -> int:
    """Deja la propuesta si ese par no la tiene ya (en ningún sentido, ni
    descartada: el índice único de la 0061 va sobre el par sin orden).

    Una fusión que se negaría (dos cuentas de canal que guardar, ver
    `contact_merge.claves_tras_fusion`) no se propone: sería un aviso en «Para
    hacer» que nadie puede resolver.
    """
    from app.services.contact_merge import FusionImposible, claves_tras_fusion

    try:
        claves_tras_fusion(queda, absorbe)
    except FusionImposible:
        logger.info(
            "identity_linking.no_se_propone",
            absorbe=str(absorbe.id),
            queda=str(queda.id),
            motivo="dos cuentas de canal",
        )
        return 0
    creada = (
        await db.execute(
            insert(MergeSuggestion)
            .values(
                id=uuid.uuid4(),
                contact_id=absorbe.id,
                candidate_id=queda.id,
                motivo=motivo,
                dato=dato[:320],
            )
            .on_conflict_do_nothing()
            .returning(MergeSuggestion.id)
        )
    ).scalar_one_or_none()
    if creada is None:
        return 0
    logger.info(
        "identity_linking.propuesta", absorbe=str(absorbe.id), queda=str(queda.id), motivo=motivo
    )
    return 1
