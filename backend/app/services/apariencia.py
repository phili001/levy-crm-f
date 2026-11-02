"""Apariencia de la instalación: nombre, logotipo, color de marca, tipografía
y grosor de los iconos.

Por qué vive en `app_settings` y no en una tabla propia: son dos claves (la
configuración y el logotipo) que se leen enteras y se escriben enteras. Una
tabla con columnas obligaría a una migración por cada opción nueva, y la tabla
clave-valor ya está pensada para crecer así.

Por qué el logotipo va en la base de datos y no en el volumen de adjuntos:
así entra en las copias de seguridad y sobrevive a un redeploy que recree los
volúmenes. Pesa como mucho `LOGO_MAX_BYTES`, que en base64 son ~350 KB en una
sola fila: nada para Postgres.

Lo que NO decide este módulo: los tonos derivados del color de marca (texto
sobre la marca, tono legible sobre el fondo, hover). Esos los calcula el panel
al aplicarlo, porque también los necesita para la vista previa en vivo antes de
guardar. Aquí solo se valida que lo guardado sea un color, una tipografía y un
trazo de la lista: el nombre de la tipografía acaba dentro de una URL de Google
Fonts, así que una lista cerrada no es una preferencia, es la validación.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.app_setting import AppSetting
from app.services.app_settings import get_app_setting, set_app_setting

SETTING_APARIENCIA = "apariencia"
SETTING_APARIENCIA_LOGO = "apariencia_logo"

# Tipografías ofrecidas. La primera es la de fábrica. Todas están en Google
# Fonts con pesos 400-800, que son los que usa el panel.
TIPOGRAFIAS = [
    "Figtree",
    "Inter",
    "Manrope",
    "DM Sans",
    "Plus Jakarta Sans",
    "Nunito Sans",
    "Source Sans 3",
    "Work Sans",
]
TRAZOS = ["fino", "normal", "grueso"]

# Valores de fábrica: los del panel tal como se entrega (cobalto + Figtree).
COLOR_DE_FABRICA = "#2350E6"
PALETA_DE_FABRICA = "cobalto"

LOGO_MAX_BYTES = 256 * 1024
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PALETA = re.compile(r"^[a-z0-9_-]{1,30}$")

# Un SVG es un documento, no una imagen: puede llevar scripts. Se sirve con una
# CSP `sandbox` que los anula aunque alguien abra la URL directamente, pero
# además se rechazan los que traen código a la vista — un logotipo no lo
# necesita, y un fichero así en el panel no lo ha puesto nadie con buena fe.
_SVG_PELIGROSO = re.compile(
    rb"<script|javascript:|<foreignObject|\son[a-z]+\s*=", re.IGNORECASE
)


class AparienciaInvalida(ValueError):
    """Un valor que no se puede guardar; el mensaje va tal cual al panel."""


@dataclass
class Apariencia:
    nombre: str | None
    paleta: str
    color_marca: str
    tipografia: str
    trazo_iconos: str

    def a_dict(self) -> dict:
        return {
            "nombre": self.nombre,
            "paleta": self.paleta,
            "color_marca": self.color_marca,
            "tipografia": self.tipografia,
            "trazo_iconos": self.trazo_iconos,
        }


def de_fabrica() -> Apariencia:
    return Apariencia(
        nombre=None,
        paleta=PALETA_DE_FABRICA,
        color_marca=COLOR_DE_FABRICA,
        tipografia=TIPOGRAFIAS[0],
        trazo_iconos="normal",
    )


def validar(datos: dict) -> Apariencia:
    """Convierte lo que manda el panel en una Apariencia válida o lanza
    AparienciaInvalida con un mensaje legible."""
    nombre = (datos.get("nombre") or "").strip() or None
    if nombre is not None and len(nombre) > 60:
        raise AparienciaInvalida("El nombre no puede pasar de 60 caracteres.")

    color = str(datos.get("color_marca") or "")
    if not _HEX.match(color):
        raise AparienciaInvalida("El color de marca tiene que ser un hexadecimal como #2350E6.")

    paleta = str(datos.get("paleta") or "")
    if not _PALETA.match(paleta):
        raise AparienciaInvalida("Paleta no válida.")

    tipografia = datos.get("tipografia")
    if tipografia not in TIPOGRAFIAS:
        raise AparienciaInvalida("Esa tipografía no está disponible.")

    trazo = datos.get("trazo_iconos")
    if trazo not in TRAZOS:
        raise AparienciaInvalida("El grosor de los iconos tiene que ser fino, normal o grueso.")

    return Apariencia(
        nombre=nombre,
        paleta=paleta,
        color_marca=color.upper(),
        tipografia=tipografia,
        trazo_iconos=trazo,
    )


async def leer(db: AsyncSession) -> Apariencia:
    """La apariencia guardada, o la de fábrica.

    Si lo guardado no se puede leer (JSON roto, una tipografía que se quitó de
    la lista), se cae a la de fábrica en vez de romper el login: un panel con
    los colores de siempre es mejor que un panel que no carga.
    """
    crudo = await get_app_setting(SETTING_APARIENCIA, "", db)
    if not crudo:
        return de_fabrica()
    try:
        return validar(json.loads(crudo))
    except (ValueError, TypeError):
        return de_fabrica()


async def guardar(db: AsyncSession, apariencia: Apariencia) -> None:
    """No hace commit: lo hace el llamante, junto con la auditoría."""
    await set_app_setting(
        SETTING_APARIENCIA, json.dumps(apariencia.a_dict(), ensure_ascii=False), db
    )


def tipo_de_logo(contenido: bytes) -> str:
    """Tipo MIME según los bytes, no según lo que diga el navegador: la
    cabecera Content-Type de una subida la escribe quien sube."""
    if contenido.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if contenido[:4] == b"RIFF" and contenido[8:12] == b"WEBP":
        return "image/webp"
    if contenido.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    cabeza = contenido[:1024].lstrip().lower()
    if cabeza.startswith(b"<svg") or (cabeza.startswith(b"<?xml") and b"<svg" in cabeza):
        if _SVG_PELIGROSO.search(contenido):
            raise AparienciaInvalida("Ese SVG trae código dentro. Expórtalo como imagen simple.")
        return "image/svg+xml"
    raise AparienciaInvalida("El logotipo tiene que ser PNG, WebP, JPG o SVG.")


async def guardar_logo(db: AsyncSession, contenido: bytes) -> str:
    """Valida y guarda el logotipo. Devuelve su huella (sirve de versión en la
    URL para que el navegador no enseñe el anterior de su caché)."""
    if not contenido:
        raise AparienciaInvalida("El fichero está vacío.")
    if len(contenido) > LOGO_MAX_BYTES:
        raise AparienciaInvalida("El logotipo no puede pasar de 256 KB.")
    mime = tipo_de_logo(contenido)
    huella = hashlib.sha256(contenido).hexdigest()[:16]
    await set_app_setting(
        SETTING_APARIENCIA_LOGO,
        json.dumps(
            {"mime": mime, "huella": huella, "datos": base64.b64encode(contenido).decode()}
        ),
        db,
    )
    return huella


async def leer_logo(db: AsyncSession) -> tuple[bytes, str, str] | None:
    """(contenido, mime, huella) o None si no hay logotipo."""
    crudo = await get_app_setting(SETTING_APARIENCIA_LOGO, "", db)
    if not crudo:
        return None
    try:
        guardado = json.loads(crudo)
        return base64.b64decode(guardado["datos"]), guardado["mime"], guardado["huella"]
    except (ValueError, KeyError, TypeError):
        return None


async def huella_del_logo(db: AsyncSession) -> str | None:
    """Solo la huella, sin traer la imagen: la pide el login en cada carga y no
    tiene sentido mover 350 KB para saber si el logotipo cambió."""
    return (
        await db.execute(
            select(text("value::json ->> 'huella'"))
            .select_from(AppSetting)
            .where(AppSetting.key == SETTING_APARIENCIA_LOGO)
        )
    ).scalar_one_or_none()


async def borrar_logo(db: AsyncSession) -> None:
    await db.execute(delete(AppSetting).where(AppSetting.key == SETTING_APARIENCIA_LOGO))


async def restablecer(db: AsyncSession) -> None:
    """Vuelve a la apariencia de fábrica, logotipo incluido."""
    await db.execute(
        delete(AppSetting).where(
            AppSetting.key.in_([SETTING_APARIENCIA, SETTING_APARIENCIA_LOGO])
        )
    )
