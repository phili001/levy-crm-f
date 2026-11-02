"""Crear plantillas de WhatsApp desde el panel (SPEC-18, tramo 1).

Viene del CRM del curso 01 (`templates/generate`, `templates/submit` y
`template-form.ts`), con tres cambios:

- No hay borradores en la base. YCloud es donde vive cada plantilla y el panel
  ya la lista en vivo; lo que se escribe se queda en el navegador hasta que se
  manda a aprobación.
- La IA arma la plantilla entera (nombre, categoría, cuerpo, ejemplos, pie y
  botones), no solo el cuerpo, y con el modelo del Agente de Texto.
- Lo que Meta rechaza siempre (variables saltadas o pegadas al principio o al
  final, respuestas rápidas mezcladas con enlaces) se dice ANTES de mandarla:
  la revisión de Meta tarda hasta 24 h y un rechazo obliga a cambiar de nombre.

Solo YCloud, que es el proveedor del curso. Con la API de Meta las plantillas
se crean en el Administrador de WhatsApp de Meta y aquí solo se listan.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger

logger = get_logger(__name__)

# El gasto de «Generar con IA» sale con su nombre en Consumo, separado del de
# Sofía: si alguien genera cincuenta plantillas, no parece que Sofía gastó más.
FUENTE_DEL_GASTO = "plantillas"

IDIOMAS = ("es_MX", "es", "es_ES", "en_US")
CATEGORIAS = ("UTILITY", "MARKETING")

NOMBRE_RE = re.compile(r"^[a-z0-9_]{1,512}$")
VARIABLE_RE = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")

MAX_CUERPO = 1024
MAX_CABECERA = 60
MAX_PIE = 60
MAX_BOTONES = 3
MAX_TEXTO_BOTON = 25
MAX_EJEMPLO = 200


class VariablePlantilla(BaseModel):
    n: int = Field(ge=1, le=20)
    # Lo que es ese dato, para quien rellena la plantilla al mandarla
    # («Nombre del paciente»). Se guarda en `whatsapp_template_var`.
    etiqueta: str = Field(default="", max_length=60)
    # Meta exige un ejemplo de cada variable para revisar la plantilla.
    ejemplo: str = Field(default="", max_length=MAX_EJEMPLO)
    # Campo de la ficha con el que se autorrellena al mandarla, o None.
    campo: str | None = None


class BotonPlantilla(BaseModel):
    tipo: Literal["respuesta", "enlace", "telefono"]
    texto: str = Field(max_length=MAX_TEXTO_BOTON)
    # La URL del enlace o el teléfono; vacío en las respuestas rápidas.
    valor: str = ""


class PlantillaIn(BaseModel):
    nombre: str = Field(max_length=512)
    categoria: Literal["UTILITY", "MARKETING"] = "UTILITY"
    idioma: str = "es_MX"
    cabecera: str = Field(default="", max_length=MAX_CABECERA)
    cuerpo: str = Field(max_length=MAX_CUERPO)
    variables: list[VariablePlantilla] = Field(default_factory=list, max_length=20)
    pie: str = Field(default="", max_length=MAX_PIE)
    botones: list[BotonPlantilla] = Field(default_factory=list, max_length=MAX_BOTONES)


def sanear_nombre(texto: str) -> str:
    """«Recordatorio de cita» → `recordatorio_de_cita` (lo que acepta Meta)."""
    sin_acentos = unicodedata.normalize("NFD", texto)
    sin_acentos = "".join(c for c in sin_acentos if unicodedata.category(c) != "Mn")
    limpio = re.sub(r"[^a-z0-9_]+", "_", sin_acentos.lower().strip())
    return re.sub(r"_+", "_", limpio).strip("_")[:512]


def variables_del_cuerpo(cuerpo: str) -> list[str]:
    """Lo que hay dentro de cada `{{…}}` del cuerpo, en orden de aparición."""
    return [m.strip() for m in VARIABLE_RE.findall(cuerpo or "")]


def problemas(p: PlantillaIn, *, campos_permitidos: frozenset[str] = frozenset()) -> list[str]:
    """Lo que impediría que Meta (o YCloud) la acepte, en palabras. [] = lista.

    `campos_permitidos`: los campos de la ficha que se pueden enlazar a una
    variable. Fuera de esos, el enlace se rechaza (nunca leer atributos
    arbitrarios del contacto).
    """
    fallos: list[str] = []
    if not NOMBRE_RE.match(p.nombre or ""):
        fallos.append("El nombre solo puede llevar minúsculas, números y guion bajo (_).")
    if p.idioma not in IDIOMAS:
        fallos.append(f"Idioma no admitido: {p.idioma}.")

    cuerpo = (p.cuerpo or "").strip()
    if not cuerpo:
        fallos.append("El mensaje no puede ir vacío.")
    usadas = variables_del_cuerpo(cuerpo)
    if any(not u.isdigit() for u in usadas):
        fallos.append("Las variables van con número: {{1}}, {{2}}…")
    numeros = sorted({int(u) for u in usadas if u.isdigit()})
    if numeros and numeros != list(range(1, len(numeros) + 1)):
        fallos.append("Las variables tienen que ir seguidas desde {{1}}, sin saltarse ninguna.")
    if cuerpo.startswith("{{") or cuerpo.endswith("}}"):
        fallos.append(
            "El mensaje no puede empezar ni terminar con una variable: Meta lo rechaza. "
            "Pon texto antes y después (aunque sea un punto)."
        )
    if re.search(r"\}\}\s*\{\{", cuerpo):
        fallos.append("Dos variables no pueden ir pegadas: pon texto entre ellas.")

    por_numero = {v.n: v for v in p.variables}
    for n in numeros:
        v = por_numero.get(n)
        if v is None or not v.ejemplo.strip():
            fallos.append(f"Falta el ejemplo de {{{{{n}}}}}: Meta lo pide para revisarla.")
    for v in p.variables:
        if v.campo and v.campo not in campos_permitidos:
            fallos.append(f"No se puede enlazar «{v.campo}» a una variable.")

    for parte, texto in (("La cabecera", p.cabecera), ("El pie", p.pie)):
        if "{{" in (texto or ""):
            fallos.append(f"{parte} no lleva variables.")

    tipos = [b.tipo for b in p.botones]
    if tipos and "respuesta" in tipos and len(set(tipos)) > 1:
        # Meta exige las respuestas rápidas juntas, al principio o al final.
        primera = tipos.index("respuesta")
        ultima = len(tipos) - 1 - tipos[::-1].index("respuesta")
        if any(t != "respuesta" for t in tipos[primera : ultima + 1]):
            fallos.append("Las respuestas rápidas tienen que ir juntas, sin un enlace en medio.")
    for b in p.botones:
        if not b.texto.strip():
            fallos.append("Cada botón necesita su texto.")
        if b.tipo == "enlace" and not re.match(r"^https://\S+$", b.valor or ""):
            fallos.append(f"El enlace del botón «{b.texto}» tiene que empezar con https://.")
        if b.tipo == "telefono" and not re.match(r"^\+\d{8,15}$", (b.valor or "").replace(" ", "")):
            fallos.append(f"El teléfono del botón «{b.texto}» va con lada: +52…")
    if tipos.count("enlace") > 2:
        fallos.append("Como mucho dos botones de enlace.")
    if tipos.count("telefono") > 1:
        fallos.append("Como mucho un botón de teléfono.")
    return fallos


def a_ycloud(p: PlantillaIn) -> dict:
    """La plantilla en el formato de `POST /whatsapp/templates` (sin `wabaId`).

    Es el `buildYCloudPayload` del 01: mayúsculas en tipo y categoría, y un
    `example` en el cuerpo si lleva variables, que sin él Meta no la revisa.
    """
    componentes: list[dict] = []
    if p.cabecera.strip():
        componentes.append({"type": "HEADER", "format": "TEXT", "text": p.cabecera.strip()})
    cuerpo: dict = {"type": "BODY", "text": p.cuerpo.strip()}
    numeros = sorted({int(u) for u in variables_del_cuerpo(p.cuerpo) if u.isdigit()})
    if numeros:
        ejemplos = {v.n: v.ejemplo.strip() for v in p.variables}
        cuerpo["example"] = {"body_text": [[ejemplos.get(n, "") for n in numeros]]}
    componentes.append(cuerpo)
    if p.pie.strip():
        componentes.append({"type": "FOOTER", "text": p.pie.strip()})
    if p.botones:
        botones: list[dict] = []
        for b in p.botones:
            if b.tipo == "respuesta":
                botones.append({"type": "QUICK_REPLY", "text": b.texto.strip()})
            elif b.tipo == "enlace":
                botones.append({"type": "URL", "text": b.texto.strip(), "url": b.valor.strip()})
            else:
                botones.append(
                    {
                        "type": "PHONE_NUMBER",
                        "text": b.texto.strip(),
                        "phone_number": b.valor.replace(" ", ""),
                    }
                )
        componentes.append({"type": "BUTTONS", "buttons": botones})
    return {
        "name": p.nombre,
        "language": p.idioma,
        "category": p.categoria,
        "components": componentes,
    }


# --- «Generar con IA» -------------------------------------------------------

class GeneracionFallida(RuntimeError):
    """El modelo no contestó o no devolvió una plantilla que se pueda leer."""


_INSTRUCCIONES_IA = """Eres experto en plantillas de WhatsApp Business y en las políticas de Meta.
Escribes en español de México, con tuteo, claro y cálido, sin exceso de emojis (uno como mucho).

Devuelve SOLO un objeto JSON, sin explicaciones ni bloques de código, con esta forma:
{
  "nombre": "minusculas_con_guion_bajo",
  "categoria": "UTILITY" o "MARKETING",
  "cabecera": "texto corto opcional, sin variables, o vacío",
  "cuerpo": "el mensaje, con variables {{1}}, {{2}}…",
  "variables": [{"n": 1, "etiqueta": "Nombre del paciente", "ejemplo": "Ana"}],
  "pie": "texto corto opcional, sin variables, o vacío",
  "botones": [{"tipo": "respuesta", "texto": "Confirmo"}]
}

Reglas de Meta que no se pueden romper:
- UTILITY solo si avisa de algo que la persona pidió o acordó (una cita, un pedido). Ofertas, promociones o "¿sigues interesado?" son MARKETING.
- En MARKETING, el pie ofrece la baja: "Responde BAJA si no quieres más mensajes."
- Variables numeradas y seguidas desde {{1}}; nunca al principio ni al final del cuerpo; nunca dos pegadas.
- Un ejemplo realista por variable, sin datos reales de nadie.
- Cuerpo de 1024 caracteres como mucho; cabecera y pie de 60.
- Hasta 3 botones. "tipo" es "respuesta" (respuesta rápida), "enlace" (con "valor": URL https) o "telefono" (con "valor": +52…). Las respuestas rápidas van juntas. Texto de botón de 25 caracteres como mucho.
- El nombre describe el propósito (recordatorio_cita, reagendar_cita), sin fechas.
"""


def _leer_json(texto: str) -> dict:
    """El objeto JSON de la respuesta del modelo, aunque venga entre ```."""
    limpio = (texto or "").strip()
    limpio = re.sub(r"^```(?:json)?\s*|\s*```$", "", limpio)
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    if inicio < 0 or fin <= inicio:
        raise GeneracionFallida("El modelo no devolvió una plantilla. Intenta de nuevo.")
    try:
        datos = json.loads(limpio[inicio : fin + 1])
    except json.JSONDecodeError as exc:
        raise GeneracionFallida("El modelo devolvió una plantilla ilegible. Intenta de nuevo.") from exc
    if not isinstance(datos, dict):
        raise GeneracionFallida("El modelo no devolvió una plantilla. Intenta de nuevo.")
    return datos


def plantilla_de_la_ia(datos: dict, *, idioma: str) -> PlantillaIn:
    """Lo que devolvió el modelo, recortado a lo que cabe, como `PlantillaIn`.

    Se recorta en vez de rechazar: es un borrador que la persona revisa en el
    editor, y los avisos de `problemas` le dicen qué falta.
    """
    def texto(clave: str, tope: int) -> str:
        valor = datos.get(clave)
        return str(valor).strip()[:tope] if isinstance(valor, str | int | float) else ""

    variables = []
    for v in datos.get("variables") or []:
        if isinstance(v, dict) and str(v.get("n", "")).isdigit() and 1 <= int(v["n"]) <= 20:
            variables.append(
                VariablePlantilla(
                    n=int(v["n"]),
                    etiqueta=str(v.get("etiqueta") or "")[:60],
                    ejemplo=str(v.get("ejemplo") or "")[:MAX_EJEMPLO],
                )
            )
    botones = []
    for b in (datos.get("botones") or [])[:MAX_BOTONES]:
        if isinstance(b, dict) and b.get("tipo") in ("respuesta", "enlace", "telefono"):
            botones.append(
                BotonPlantilla(
                    tipo=b["tipo"],
                    texto=str(b.get("texto") or "")[:MAX_TEXTO_BOTON],
                    valor=str(b.get("valor") or "")[:2000],
                )
            )
    categoria = str(datos.get("categoria") or "").upper()
    try:
        return PlantillaIn(
            nombre=sanear_nombre(texto("nombre", 512)) or "plantilla_nueva",
            categoria=categoria if categoria in CATEGORIAS else "UTILITY",
            idioma=idioma,
            cabecera=texto("cabecera", MAX_CABECERA),
            cuerpo=texto("cuerpo", MAX_CUERPO),
            variables=variables,
            pie=texto("pie", MAX_PIE),
            botones=botones,
        )
    except ValidationError as exc:
        raise GeneracionFallida("El modelo devolvió una plantilla incompleta. Intenta de nuevo.") from exc


async def generar_con_ia(descripcion: str, *, categoria: str | None, idioma: str) -> PlantillaIn:
    """Pide al modelo del Agente de Texto una plantilla para `descripcion`.

    Lleva el bloque «Sobre el negocio» para que la plantilla hable como la
    clínica y no como una genérica. Lanza `GeneracionFallida` si no se puede.
    """
    from app.providers.llm import resolve_llm_provider
    from app.providers.llm.base import LLMMessage
    from app.services.runtime_config import _HUECO, get_runtime_for_channel, render_common_block

    agente = await get_runtime_for_channel("whatsapp")
    if agente is None:
        raise GeneracionFallida("No hay un agente de texto con modelo configurado.")
    llm = await resolve_llm_provider(
        agente.llm_provider_id, agente.fallback_provider_id, agente.fallback_model
    )
    try:
        negocio = _HUECO.sub("(sin configurar)", await render_common_block())
    except Exception:  # noqa: BLE001 — sin el bloque, la plantilla sale genérica
        negocio = ""
    pedido = f"Plantilla para: {descripcion.strip()}"
    if categoria in CATEGORIAS:
        pedido += f"\nCategoría que quiere la persona: {categoria}."
    try:
        respuesta = await llm.complete(
            messages=[
                LLMMessage(role="system", content=_INSTRUCCIONES_IA + ("\n" + negocio if negocio else "")),
                LLMMessage(role="user", content=pedido),
            ],
            model=agente.model_name,
            temperature=0.4,
            max_tokens=900,
            source=FUENTE_DEL_GASTO,
        )
    except Exception as exc:  # noqa: BLE001 — el motivo va al registro, no al panel
        logger.warning("plantillas.ia.fallo", error=str(exc)[:300])
        raise GeneracionFallida(
            "El modelo no contestó. Revisa tu proveedor en Canales → Proveedores LLM e intenta de nuevo."
        ) from exc
    return plantilla_de_la_ia(_leer_json(respuesta.content or ""), idioma=idioma)
