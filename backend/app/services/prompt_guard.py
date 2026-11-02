"""Las reglas del prompt de voz que no se pueden borrar al guardar (Fase 8, US-19 AC3).

El panel existe para que la clínica edite a Sofía sin tocar código: cambiar
cómo saluda, añadir una promoción, ajustar cómo cierra. Pero el prompt de voz
lleva también las reglas que separan a una recepcionista de practicar medicina
sin licencia: no diagnostica, no recomienda medicamentos y no confirma una cita
que el sistema no creó. Un área de texto no sabe eso: seleccionar todo, pegar,
guardar, y esas reglas desaparecen de una línea de teléfono en vivo sin aviso.

La capa fija del código (`SECURITY_GUARD` en `runtime_config`) ya cubre la
inyección de instrucciones y la transparencia de que es un asistente; lo propio
de cada negocio va en el prompt, en la sección cuyo título termina en
"NO TOCAR" (así la marcan los perfiles: `dental_mx.PROMPT_VOZ`, sección 5). Al
guardar, cada regla de esa sección tiene que seguir ahí, tal cual:

- Se pueden AÑADIR reglas a la sección (una instalación vieja puede ganar las
  del perfil nuevo pegándolas), mover la sección o renumerarla.
- No se puede quitar ni reescribir una regla, ni quitarle el "NO TOCAR" al
  título: eso sería borrarla por la puerta de atrás.

Lo que vale es lo guardado ahora, no el perfil: una clínica de otro giro tiene
sus propias reglas, y el perfil solo siembra la primera versión. Si el prompt
guardado no tiene sección protegida, no hay nada que proteger.

Portado de la idea de `agente-voz-ghl/app/services/prompt_guard.py` (curso 02).
Allí el bloque se sustituía por un marcador en el área de texto; aquí el prompt
se edita entero y se rechaza el guardado que pierde una regla, que no pide
cambiar el panel.
"""
from __future__ import annotations

import re

MARCA = "NO TOCAR"

_SEPARADOR = re.compile(r"^\s*={3,}\s*$")
_ENCABEZADO = re.compile(r"^\s*(\d+)\.\s+(\S.*?)\s*$")
_INICIO_DE_REGLA = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


class ReglaProtegidaError(ValueError):
    """El prompt que se quiere guardar perdió una regla protegida."""


def _normaliza(texto: str) -> str:
    return re.sub(r"\s+", " ", texto).strip()


def _secciones(prompt: str) -> list[tuple[str, list[str]]]:
    """(título sin número, líneas del cuerpo) de cada sección del prompt.

    Un encabezado es "N. TÍTULO" justo debajo de una línea de "====": así se
    distinguen de los pasos numerados que hay DENTRO de una sección.
    """
    lineas = prompt.splitlines()
    inicios = [
        i
        for i, linea in enumerate(lineas)
        if i > 0 and _SEPARADOR.match(lineas[i - 1]) and _ENCABEZADO.match(linea)
    ]
    secciones = []
    for n, i in enumerate(inicios):
        # El separador que abre la sección siguiente no es parte de esta.
        fin = inicios[n + 1] - 1 if n + 1 < len(inicios) else len(lineas)
        titulo = _ENCABEZADO.match(lineas[i]).group(2)  # type: ignore[union-attr]
        cuerpo = [linea for linea in lineas[i + 1 : fin] if not _SEPARADOR.match(linea)]
        secciones.append((titulo, cuerpo))
    return secciones


def _reglas(cuerpo: list[str]) -> list[str]:
    """Cada viñeta (con sus líneas de continuación) o párrafo, normalizado:
    reordenar o partir una línea no cuenta como cambiar la regla."""
    reglas: list[str] = []
    actual: list[str] = []
    for linea in cuerpo:
        if not linea.strip() or _INICIO_DE_REGLA.match(linea):
            if actual:
                reglas.append(_normaliza(" ".join(actual)))
            actual = [linea] if linea.strip() else []
        else:
            actual.append(linea)
    if actual:
        reglas.append(_normaliza(" ".join(actual)))
    return [r for r in reglas if r]


def secciones_protegidas(prompt: str) -> dict[str, tuple[str, list[str]]]:
    """Título normalizado → (título tal cual, reglas) de las secciones "NO TOCAR"."""
    return {
        _normaliza(titulo).upper(): (titulo, _reglas(cuerpo))
        for titulo, cuerpo in _secciones(prompt or "")
        if MARCA in titulo.upper()
    }


def comprobar_reglas_protegidas(actual: str, nuevo: str) -> None:
    """Lanza `ReglaProtegidaError` si `nuevo` pierde algo protegido de `actual`."""
    antes = secciones_protegidas(actual)
    if not antes:
        return
    despues = secciones_protegidas(nuevo)
    for clave, (titulo, reglas) in antes.items():
        if clave not in despues:
            raise ReglaProtegidaError(
                f"Falta la sección «{titulo}». Guarda las reglas que el asistente no se "
                "puede saltar por teléfono, así que el prompt no se puede guardar sin ella. "
                "Puedes añadirle reglas, pero no quitarla ni quitarle el «NO TOCAR» del título."
            )
        quedan = set(despues[clave][1])
        for regla in reglas:
            if regla not in quedan:
                muestra = regla if len(regla) <= 160 else regla[:157] + "…"
                raise ReglaProtegidaError(
                    f"Se borró o se cambió una regla de «{titulo}»: «{muestra}». En esa "
                    "sección puedes añadir reglas, pero no quitar ni reescribir las que ya tiene."
                )
