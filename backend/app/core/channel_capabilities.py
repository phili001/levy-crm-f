"""Cómo se comporta cada canal: cuánto se espera, cómo se escribe y cuánto cabe (Fase 10.2).

Antes el ritmo lo ponía el AGENTE (`agents.buffer_seconds`), y el agente de
texto es uno solo para WhatsApp, Instagram, Messenger, el chat web y el
correo. Resultado: el chat web esperaba 8 segundos antes de contestar, igual
que WhatsApp, aunque en una web nadie manda los mensajes en ráfaga, y el correo
también esperaba sin motivo. Ahora cada canal tiene lo suyo, en esta tabla:

- `buffer_seconds`: cuánto se espera desde el último mensaje antes de
  contestar, para juntar los que la persona manda seguidos. En WhatsApp,
  Instagram y Messenger nunca baja de `settings.MIN_TEXT_BUFFER_SECONDS` (6):
  por debajo, el agente contesta a cada mensaje suelto de una ráfaga.
- `formato`: `whatsapp` (la negrita es `*así*`) o `plano`. Instagram, Messenger,
  el chat web y el correo no pintan markdown: los asteriscos se verían tal cual.
- `max_text_bytes`: lo que admite cada mensaje, en bytes de UTF-8 (un emoji
  ocupa cuatro). Instagram corta a 1000, Messenger a 2000 y WhatsApp a 4096.
- `ventana_horas` / `ventana_humano_horas`: las ventanas de Meta para contestar
  (WhatsApp 24 h; Instagram y Messenger 24 h, o 7 días con HUMAN_AGENT).
- `style_hint`: una línea para Sofía sobre cómo se escribe en ese canal (la
  pega `runtime_config.assemble_system_prompt`).

El buffer y la línea de estilo de un canal se pueden cambiar sin tocar código,
en `channels.config["capacidades"]` de su fila: `{"buffer_seconds": 5}`. No hay
pantalla a propósito: el valor de fábrica es el que enseña el curso.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

FORMATO_WHATSAPP = "whatsapp"
FORMATO_PLANO = "plano"
FORMATO_VOZ = "voz"


@dataclass(frozen=True)
class Capacidades:
    buffer_seconds: int
    formato: str
    style_hint: str = ""
    # El piso de `MIN_TEXT_BUFFER_SECONDS` solo vale donde la gente escribe en
    # ráfagas (WhatsApp, Instagram, Messenger).
    piso_buffer: bool = False
    max_text_bytes: int | None = None
    ventana_horas: int | None = None
    ventana_humano_horas: int | None = None
    # Qué archivos puede mandarle Sofía (o una herramienta de n8n) al paciente
    # por este canal: `image`, `audio`, `video`, `document` (Fase 11). Vacío =
    # ninguno; la herramienta se lo dice a Sofía en vez de fallar callada.
    archivos_salientes: frozenset[str] = frozenset()


# Por valor de `conversations.canal`.
CAPACIDADES: dict[str, Capacidades] = {
    "whatsapp": Capacidades(
        buffer_seconds=8,
        formato=FORMATO_WHATSAPP,
        piso_buffer=True,
        max_text_bytes=4096,
        ventana_horas=24,
        archivos_salientes=frozenset({"image", "audio", "video", "document"}),
        style_hint=(
            "Estás en WhatsApp. Mensajes cortos y naturales, como en un chat. Para "
            "resaltar algo usa *asteriscos simples*; nada de títulos ni tablas."
        ),
    ),
    "instagram_dm": Capacidades(
        buffer_seconds=8,
        formato=FORMATO_PLANO,
        piso_buffer=True,
        max_text_bytes=1000,
        ventana_horas=24,
        ventana_humano_horas=24 * 7,
        # Instagram no admite documentos en los mensajes directos.
        archivos_salientes=frozenset({"image", "audio", "video"}),
        style_hint=(
            "Estás en Instagram. Mensajes muy cortos (una o dos frases) y en texto "
            "plano: sin asteriscos, títulos ni listas largas."
        ),
    ),
    "messenger": Capacidades(
        buffer_seconds=8,
        formato=FORMATO_PLANO,
        piso_buffer=True,
        max_text_bytes=2000,
        ventana_horas=24,
        ventana_humano_horas=24 * 7,
        archivos_salientes=frozenset({"image", "audio", "video", "document"}),
        style_hint=(
            "Estás en Messenger. Mensajes cortos y en texto plano: sin asteriscos, "
            "títulos ni listas largas."
        ),
    ),
    "web": Capacidades(
        buffer_seconds=3,
        formato=FORMATO_PLANO,
        archivos_salientes=frozenset({"image", "audio", "video", "document"}),
        style_hint=(
            "Estás en el chat de la página web. Respuestas breves y directas, en "
            "texto plano: sin asteriscos ni títulos."
        ),
    ),
    "email": Capacidades(
        buffer_seconds=0,
        formato=FORMATO_PLANO,
        style_hint=(
            "Estás respondiendo un correo. Escribe como un correo: saludo, uno a "
            "tres párrafos claros y una despedida breve (la firma se añade sola). "
            "Texto plano, sin asteriscos ni títulos."
        ),
    ),
    # La voz tiene su propia capa en el prompt (`api/voice.py`) y no pasa por
    # el buffer: la llamada está en vivo.
    "retell_voice": Capacidades(buffer_seconds=0, formato=FORMATO_VOZ),
}

# El tipo de conexión del chat web se llama distinto que su canal de
# conversación (`channels.type` = webchat, `conversations.canal` = web).
_ALIAS = {"webchat": "web"}
_TIPO_DE_CONEXION = {"web": "webchat"}

_POR_DEFECTO = Capacidades(buffer_seconds=8, formato=FORMATO_PLANO, piso_buffer=True)


def _clave(canal) -> str:
    valor = getattr(canal, "value", canal) or ""
    return _ALIAS.get(str(valor), str(valor))


def capacidades(canal) -> Capacidades:
    """Las de fábrica de un canal (acepta el enum o su valor)."""
    return CAPACIDADES.get(_clave(canal), _POR_DEFECTO)


async def capacidades_de(canal) -> Capacidades:
    """Las de un canal con lo que su fila de `channels` cambie encima.

    Solo se pueden cambiar `buffer_seconds` y `style_hint`. Un valor que no
    tiene sentido se ignora (y queda en el log) en vez de romper el turno.
    """
    base = capacidades(canal)
    try:
        from sqlalchemy import select

        from app.db.session import db_session
        from app.models.channel import Channel, ChannelType

        clave = _clave(canal)
        tipo = ChannelType(_TIPO_DE_CONEXION.get(clave, clave))
        async with db_session() as db:
            configs = (
                await db.execute(
                    select(Channel.config).where(Channel.type == tipo, Channel.enabled.is_(True))
                )
            ).scalars().all()
    except Exception as e:  # noqa: BLE001 — sin la fila, valen las de fábrica
        logger.warning("channel_capabilities.lectura_fallida", canal=str(canal), error=str(e))
        return base
    for config in configs:
        cambios = (config or {}).get("capacidades") or {}
        if not isinstance(cambios, dict):
            continue
        buffer = cambios.get("buffer_seconds")
        if isinstance(buffer, int) and not isinstance(buffer, bool) and 0 <= buffer <= 60:
            base = replace(base, buffer_seconds=buffer)
        elif buffer is not None:
            logger.warning("channel_capabilities.buffer_invalido", canal=str(canal))
        estilo = cambios.get("style_hint")
        if isinstance(estilo, str):
            base = replace(base, style_hint=estilo.strip())
    return base


async def buffer_del_canal(canal) -> int:
    """Segundos de espera antes de contestar en ese canal, ya con el piso."""
    cap = await capacidades_de(canal)
    segundos = max(0, cap.buffer_seconds)
    if cap.piso_buffer:
        segundos = max(segundos, settings.MIN_TEXT_BUFFER_SECONDS)
    return segundos


# --- Formato --------------------------------------------------------------

_TITULO = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+", re.MULTILINE)
_NEGRITA = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
# Un solo asterisco alrededor de algo («*así*»), pero no el de una lista
# («* uno») ni el de una cuenta («5*3»).
_ENFASIS = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_ENLACE = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")


def _enlace_legible(m: re.Match) -> str:
    texto, url = m.group(1).strip(), m.group(2)
    return url if texto == url else f"{texto} ({url})"


def formatear_para_canal(texto: str, canal) -> str:
    """El texto del modelo, con el formato que entiende el canal.

    Los modelos escriben markdown (`**negrita**`, `### títulos`,
    `[enlaces](…)`). WhatsApp solo entiende `*negrita*`, y el resto de canales
    de texto nada: sin esto, el paciente veía los asteriscos dobles y las
    almohadillas tal cual. La voz no pasa por aquí.
    """
    formato = capacidades(canal).formato
    if not texto or formato == FORMATO_VOZ:
        return texto
    salida = _TITULO.sub("", texto)
    salida = _ENLACE.sub(_enlace_legible, salida)
    if formato == FORMATO_WHATSAPP:
        return _NEGRITA.sub(r"*\2*", salida)
    salida = _NEGRITA.sub(r"\2", salida)
    return _ENFASIS.sub(r"\1", salida)
