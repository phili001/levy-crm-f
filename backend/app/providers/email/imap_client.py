"""Buzón de correo por IMAP (entrada) con contraseña de aplicación.

Por qué IMAP y no solo Gmail por OAuth: la clínica típica tiene el correo en su
hosting (Hostinger, cPanel) o en Zoho, y para Gmail basta una contraseña de
aplicación. Configurar OAuth de Google para cada instalación era el paso que
más gente dejaba a medias.

`imaplib` es síncrono: todo va en `asyncio.to_thread`. Las funciones públicas
son síncronas a propósito (fáciles de probar con un doble de `imaplib`); quien
las llama desde async usa `asyncio.to_thread`.

Microsoft: Outlook.com apagó la autenticación básica en septiembre de 2024 y
Microsoft 365 exige OAuth para IMAP. Con esos buzones el modo contraseña no
funciona nunca, así que se detectan ANTES de intentar y se explica.
"""
from __future__ import annotations

import contextlib
import imaplib
import re
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.core.logging import get_logger

logger = get_logger(__name__)

TIMEOUT_SEGUNDOS = 30
# Tope por pasada: con 500 correos de golpe, mejor varias pasadas de 50 que una
# que se come el lock y el timeout del worker.
MAX_POR_PASADA = 50
# Si el servidor cambia UIDVALIDITY (los UID dejan de valer), se relee por
# fecha lo del último día para no perder nada; los repetidos no se duplican.
DIAS_DE_RESINCRONIZACION = 1


@dataclass(frozen=True)
class Servidor:
    host: str
    port: int
    seguridad: str  # "ssl" | "starttls"


@dataclass(frozen=True)
class Preset:
    nombre: str
    imap: Servidor
    smtp: Servidor
    ayuda: str


# `{dominio}` se sustituye por el dominio del correo (cPanel).
PRESETS: dict[str, Preset] = {
    "gmail": Preset(
        "Gmail",
        Servidor("imap.gmail.com", 993, "ssl"),
        Servidor("smtp.gmail.com", 587, "starttls"),
        "Necesitas la verificación en dos pasos activada y una contraseña de "
        "aplicación (myaccount.google.com → Seguridad → Contraseñas de aplicaciones).",
    ),
    "hostinger": Preset(
        "Hostinger",
        Servidor("imap.hostinger.com", 993, "ssl"),
        Servidor("smtp.hostinger.com", 465, "ssl"),
        "Usa la contraseña del buzón que creaste en hPanel → Correos.",
    ),
    "cpanel": Preset(
        "cPanel",
        Servidor("mail.{dominio}", 993, "ssl"),
        Servidor("mail.{dominio}", 465, "ssl"),
        "Usa la contraseña del buzón de cPanel → Cuentas de correo.",
    ),
    "zoho": Preset(
        "Zoho (correo personal)",
        Servidor("imap.zoho.com", 993, "ssl"),
        Servidor("smtp.zoho.com", 465, "ssl"),
        "Activa IMAP en el webmail de Zoho (Configuración → Correo → IMAP) y "
        "crea una contraseña de aplicación si tienes la verificación en dos pasos.",
    ),
    "zoho_pro": Preset(
        "Zoho (dominio propio)",
        Servidor("imappro.zoho.com", 993, "ssl"),
        Servidor("smtppro.zoho.com", 465, "ssl"),
        "Activa IMAP en el webmail de Zoho y usa una contraseña de aplicación.",
    ),
}

DOMINIOS_MICROSOFT = ("outlook.com", "hotmail.com", "live.com", "msn.com")
_PREFIJOS_MICROSOFT = ("hotmail.", "live.", "outlook.")
MX_MICROSOFT = ".protection.outlook.com"

AVISO_MICROSOFT = (
    "Outlook y Microsoft 365 no aceptan contraseña de aplicación: Microsoft "
    "apagó ese acceso. Usa un buzón de tu hosting (o de Gmail o Zoho) para el CRM."
)


class ImapError(Exception):
    """Fallo con un mensaje que se puede enseñar tal cual en el panel."""


@dataclass
class ConfigImap:
    usuario: str
    password: str
    servidor: Servidor
    carpeta: str = "INBOX"


@dataclass
class Correo:
    uid: int
    crudo: bytes


@dataclass
class Pasada:
    uidvalidity: str
    correos: list[Correo] = field(default_factory=list)
    # Hasta dónde se ha leído: el siguiente arranca en `ultimo_uid + 1`.
    ultimo_uid: int = 0
    # True si cambió UIDVALIDITY o era la primera vez: se leyó por fecha.
    resincronizado: bool = False


def dominio_de(correo: str) -> str:
    return correo.rsplit("@", 1)[-1].strip().lower() if "@" in correo else ""


def servidor_de_preset(preset: str, tipo: str, correo: str) -> Servidor | None:
    p = PRESETS.get(preset)
    if not p:
        return None
    s = p.imap if tipo == "imap" else p.smtp
    return Servidor(s.host.replace("{dominio}", dominio_de(correo)), s.port, s.seguridad)


def es_microsoft(correo: str, *, mirar_mx: bool = True) -> bool:
    """¿El buzón es de Outlook.com o de Microsoft 365?

    Por dominio (outlook.com, hotmail.*, live.*, msn.com) y, para dominios
    propios, por el MX: un Microsoft 365 con dominio de la clínica tiene el MX
    en `*.mail.protection.outlook.com`. Si la consulta DNS falla, no se bloquea:
    mejor dejar probar que impedirlo por un DNS caído.
    """
    dom = dominio_de(correo)
    if not dom:
        return False
    if dom in DOMINIOS_MICROSOFT or dom.startswith(_PREFIJOS_MICROSOFT):
        return True
    if not mirar_mx:
        return False
    try:
        import dns.resolver

        respuesta = dns.resolver.resolve(dom, "MX", lifetime=5)
        return any(str(r.exchange).rstrip(".").lower().endswith(MX_MICROSOFT) for r in respuesta)
    except Exception:  # noqa: BLE001 — sin DNS no se decide nada
        return False


def traducir_error(exc: Exception, servicio: str) -> str:
    """El error de IMAP o SMTP convertido en algo que se pueda arreglar."""
    texto = str(exc)
    bajo = texto.lower()
    if "application-specific password" in bajo or "app password" in bajo:
        return (
            f"{servicio}: Google pide una contraseña de aplicación. Activa la "
            "verificación en dos pasos y crea una en myaccount.google.com → "
            "Seguridad → Contraseñas de aplicaciones."
        )
    if "imap" in bajo and ("disabled" in bajo or "not enabled" in bajo or "enable imap" in bajo):
        return f"{servicio}: el acceso IMAP está apagado. Actívalo en la configuración del webmail."
    if "web login required" in bajo or "please log in via your web browser" in bajo:
        return (
            f"{servicio}: el proveedor pide entrar una vez desde el navegador para "
            "desbloquear el acceso. Si es Google Workspace, el administrador puede "
            "tener bloqueadas las contraseñas de aplicación."
        )
    if "authenticationfailed" in bajo or "invalid credentials" in bajo or "535" in bajo or "login failed" in bajo:
        return f"{servicio}: usuario o contraseña incorrectos."
    if isinstance(exc, socket.gaierror):
        return f"{servicio}: no se encuentra el servidor. Revisa el nombre del servidor."
    if isinstance(exc, TimeoutError | ConnectionRefusedError):
        return f"{servicio}: el servidor no responde en ese puerto. Revisa el puerto y el tipo de seguridad."
    if isinstance(exc, ssl.SSLError):
        return f"{servicio}: fallo de cifrado. Prueba el otro tipo de seguridad (SSL o STARTTLS)."
    return f"{servicio}: {texto[:200]}"


def _conectar(cfg: ConfigImap) -> imaplib.IMAP4:
    s = cfg.servidor
    if s.seguridad == "ssl":
        conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(
            s.host, s.port, ssl_context=ssl.create_default_context(), timeout=TIMEOUT_SEGUNDOS
        )
    else:
        conn = imaplib.IMAP4(s.host, s.port, timeout=TIMEOUT_SEGUNDOS)
        conn.starttls(ssl_context=ssl.create_default_context())
    conn.login(cfg.usuario, cfg.password)
    return conn


def _cerrar(conn: imaplib.IMAP4) -> None:
    with contextlib.suppress(Exception):
        conn.logout()


def probar(cfg: ConfigImap) -> None:
    """Entra y abre la carpeta. Lanza ImapError con el motivo legible."""
    if es_microsoft(cfg.usuario):
        raise ImapError(AVISO_MICROSOFT)
    try:
        conn = _conectar(cfg)
    except Exception as exc:  # noqa: BLE001
        raise ImapError(traducir_error(exc, "IMAP")) from exc
    try:
        tipo, _ = conn.select(_carpeta(cfg.carpeta), readonly=True)
        if tipo != "OK":
            raise ImapError(f"IMAP: no existe la carpeta «{cfg.carpeta}».")
    finally:
        _cerrar(conn)


def _carpeta(nombre: str) -> str:
    # Con espacios o acentos hay que ir entre comillas.
    return f'"{nombre}"' if re.search(r"[\s\"]", nombre) else nombre


_RE_UIDVALIDITY = re.compile(rb"UIDVALIDITY (\d+)")


def _uidvalidity(conn: imaplib.IMAP4, carpeta: str) -> str:
    tipo, datos = conn.response("UIDVALIDITY")
    if tipo == "OK" and datos and datos[0]:
        return datos[0].decode() if isinstance(datos[0], bytes) else str(datos[0])
    tipo, datos = conn.status(_carpeta(carpeta), "(UIDVALIDITY)")
    m = _RE_UIDVALIDITY.search(datos[0] if datos else b"")
    return m.group(1).decode() if m else ""


_RE_UIDNEXT = re.compile(rb"UIDNEXT (\d+)")


def _ultimo_uid(conn: imaplib.IMAP4, carpeta: str) -> int:
    """El UID más alto del buzón, sin listarlos todos: `UIDNEXT - 1`. Si el
    servidor no lo da, se cae a buscar todos (lento en buzones grandes)."""
    tipo, datos = conn.status(_carpeta(carpeta), "(UIDNEXT)")
    m = _RE_UIDNEXT.search(datos[0] if tipo == "OK" and datos and isinstance(datos[0], bytes) else b"")
    if m:
        return int(m.group(1)) - 1
    todos = _uids(conn, "ALL")
    return todos[-1] if todos else 0


def _uids(conn: imaplib.IMAP4, criterio: str) -> list[int]:
    tipo, datos = conn.uid("SEARCH", None, criterio)
    if tipo != "OK" or not datos or not datos[0]:
        return []
    return sorted(int(x) for x in datos[0].split())


def traer_nuevos(
    cfg: ConfigImap, uidvalidity: str | None, ultimo_uid: int, ahora: datetime | None = None
) -> Pasada:
    """Los correos llegados desde `ultimo_uid`, sin marcarlos como leídos.

    - Primera vez (sin `uidvalidity` guardado): NO se trae nada. El buzón se
      ancla en su último correo y solo entra lo que llegue desde ese momento.
      Leer hacia atrás metía avisos, recibos y boletines viejos, y a Sofía
      redactando borradores para correos de horas antes (decisión de Carlos
      tras la primera conexión real, 2026-09-23).
    - Si el servidor cambió `UIDVALIDITY` (los UID dejan de significar lo
      mismo: pasa al migrar el buzón o recrear la carpeta), se lee por FECHA el
      último día para no perder lo que entró mientras. Los que ya estaban no se
      duplican: el id de cada correo en el CRM es su `Message-ID`, no el UID.
    """
    try:
        conn = _conectar(cfg)
    except Exception as exc:  # noqa: BLE001
        raise ImapError(traducir_error(exc, "IMAP")) from exc
    try:
        tipo, _ = conn.select(_carpeta(cfg.carpeta), readonly=True)
        if tipo != "OK":
            raise ImapError(f"IMAP: no existe la carpeta «{cfg.carpeta}».")
        validez = _uidvalidity(conn, cfg.carpeta)
        pasada = Pasada(uidvalidity=validez, ultimo_uid=ultimo_uid)
        if not uidvalidity:
            pasada.ultimo_uid = _ultimo_uid(conn, cfg.carpeta)
            return pasada
        if uidvalidity != validez:
            desde = (ahora or datetime.now(timezone.utc)) - timedelta(days=DIAS_DE_RESINCRONIZACION)
            uids = _uids(conn, f"SINCE {desde.strftime('%d-%b-%Y')}")
            pasada.resincronizado = True
        else:
            # `N:*` devuelve siempre el último aunque sea < N: se filtra.
            uids = [u for u in _uids(conn, f"UID {ultimo_uid + 1}:*") if u > ultimo_uid]
        for uid in uids[:MAX_POR_PASADA]:
            tipo, datos = conn.uid("FETCH", str(uid), "(BODY.PEEK[])")
            crudo = next(
                (d[1] for d in datos or [] if isinstance(d, tuple) and len(d) > 1), None
            )
            if tipo == "OK" and crudo:
                pasada.correos.append(Correo(uid=uid, crudo=crudo))
            pasada.ultimo_uid = max(pasada.ultimo_uid, uid)
        if pasada.resincronizado and not uids:
            # Nada en el último día: se ancla al último UID que exista para no
            # releer el buzón entero la próxima vez.
            pasada.ultimo_uid = _ultimo_uid(conn, cfg.carpeta)
        return pasada
    except imaplib.IMAP4.error as exc:
        raise ImapError(traducir_error(exc, "IMAP")) from exc
    finally:
        _cerrar(conn)


def detectar_enviados(cfg: ConfigImap) -> str | None:
    """La carpeta de enviados del buzón (la marcada `\\Sent`), o None."""
    conn = _conectar(cfg)
    try:
        tipo, datos = conn.list()
        if tipo != "OK":
            return None
        for linea in datos or []:
            texto = linea.decode(errors="replace") if isinstance(linea, bytes) else str(linea)
            if "\\Sent" in texto:
                m = re.search(r'"([^"]+)"\s*$', texto) or re.search(r"(\S+)\s*$", texto)
                if m:
                    return m.group(1)
        return None
    finally:
        _cerrar(conn)


def guardar_en_enviados(cfg: ConfigImap, carpeta: str, crudo: bytes) -> None:
    """Copia en Enviados lo que salió por SMTP: Gmail lo hace solo, pero la
    mayoría de hostings no, y el cliente vería su bandeja sin la respuesta."""
    conn = _conectar(cfg)
    try:
        conn.append(
            _carpeta(carpeta),
            "(\\Seen)",
            imaplib.Time2Internaldate(datetime.now(timezone.utc)),
            crudo,
        )
    finally:
        _cerrar(conn)
