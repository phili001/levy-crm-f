"""Qué canales de la bandeja ve cada rol, y qué eventos en vivo le llegan.

Un solo criterio, porque la bandeja se lee por más de un camino: la lista y el
detalle por HTTP, y el WebSocket en tiempo real. Si cada camino decide por su
cuenta, uno se queda atrás. Pasó: por HTTP el revisor solo veía Instagram,
pero el WebSocket le mandaba los avisos de WhatsApp, con el teléfono dentro
(SPEC-21, A1).

El revisor existe para la revisión de la app en Meta: solo ve Instagram.
Admin y cliente ven todo.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.core.logging import get_logger
from app.db.session import db_session
from app.models.conversation import Conversation, ConversationCanal
from app.models.user import User, UserRole

logger = get_logger(__name__)

# Único canal que ve el rol revisor. Su cuenta existe para que Meta compruebe
# el chatbot de Instagram; el resto de conversaciones (WhatsApp, web, correo,
# llamadas) son de clientes que no pintan nada en esa revisión.
CANAL_DEL_REVISOR = ConversationCanal.instagram_dm.value

# Conversaciones cuyo canal recuerda un socket del revisor. Una conversación no
# cambia de canal, así que no hay que invalidar nada: el tope solo evita que un
# socket de una hora crezca sin límite.
_TOPE_CANALES_RECORDADOS = 1000


def canal_fijo(user: User) -> str | None:
    """El único canal que puede ver este usuario, o None si los ve todos."""
    if user.role == UserRole.revisor:
        return CANAL_DEL_REVISOR
    return None


def puede_ver_canal(user: User, canal: ConversationCanal | str | None) -> bool:
    """¿Puede este usuario ver una conversación de este canal?"""
    fijo = canal_fijo(user)
    if fijo is None:
        return True
    valor = canal.value if isinstance(canal, ConversationCanal) else canal
    return valor == fijo


class FiltroDeEventos:
    """Decide qué eventos de la bandeja se le reenvían a un socket.

    A quien ve todos los canales le pasa todo, sin tocar la base. A quien tiene
    un canal fijo solo le pasan los eventos de conversaciones de ese canal: el
    canal se busca una vez por conversación. Un evento sin conversación (la
    agenda, las fusiones) no le pasa: no son pantallas suyas.
    """

    def __init__(self, user: User) -> None:
        self._user = user
        self._canales: dict[uuid.UUID, str] = {}

    async def deja_pasar(self, evento: dict) -> bool:
        if canal_fijo(self._user) is None:
            return True
        payload = evento.get("payload")
        conv_id = payload.get("conversation_id") if isinstance(payload, dict) else None
        if not conv_id:
            return False
        try:
            conv_uuid = uuid.UUID(str(conv_id))
        except ValueError:
            return False
        canal = await self._canal_de(conv_uuid)
        return canal is not None and puede_ver_canal(self._user, canal)

    async def _canal_de(self, conv_id: uuid.UUID) -> str | None:
        if conv_id in self._canales:
            return self._canales[conv_id]
        try:
            async with db_session() as db:
                canal = (
                    await db.execute(select(Conversation.canal).where(Conversation.id == conv_id))
                ).scalar_one_or_none()
        except Exception as e:  # noqa: BLE001 — sin saber el canal, no se reenvía
            logger.warning("bandeja_acceso.canal_desconocido", error=str(e))
            return None
        if canal is None:
            return None
        if len(self._canales) >= _TOPE_CANALES_RECORDADOS:
            self._canales.pop(next(iter(self._canales)))
        self._canales[conv_id] = canal.value
        return canal.value
