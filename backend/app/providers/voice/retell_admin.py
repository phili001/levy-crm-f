"""Alta del agente de Retell desde el CRM (Fase 8, US-19).

Hasta ahora la clínica creaba el agente a mano en la consola de Retell y pegaba
dos URLs. Aquí el CRM lo crea él solo, apuntando a su propio WebSocket y a su
webhook, lo publica y, si se pide, le compra un número de EE. UU. o le ata uno
que ya esté en la cuenta. Portado de `agente-voz-ghl/app/services/
retell_service.py` (curso 02), con `httpx` como el resto de proveedores y
escrito contra la API de Retell de septiembre de 2026 (spec 2026-09-14).

Lo que no es obvio y ya costó llamadas mudas:

- Un agente recién creado es un BORRADOR (v0). El número se ata a la última
  versión publicada, así que sin publicar no contesta nadie: se publica al
  crearlo.
- Una versión publicada es de solo lectura. Cambiar algo es crear un borrador a
  partir de la publicada, cambiar ESE borrador y publicarlo. El
  `POST /publish-agent/{id}` que usaba el CRM está obsoleto desde el
  20-jul-2026; ahora es `POST /publish-agent-version/{id}` con la versión.
- El número se ata con `agent_version: "latest_published"` para que siga a cada
  publicación. Atado a una versión fija, se quedaría en la vieja al cambiar algo.
- `es-MX` no existe en Retell: el español de México es `es-419`.
- Retell no cuelga solo: si nadie habla, por defecto aguanta 10 minutos
  facturando. Hay que ponerle un tope de silencio.
- Importar un número (Twilio) solo ata las llamadas de ENTRADA. Las salientes
  van aparte (`outbound_agents`) y el CRM no las hace.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

IDIOMA = "es-419"

# Voz de la plataforma, mexicana y femenina: la de Sofía en el curso 02.
VOZ_POR_DEFECTO = "retell-Andrea"

# Un poco por debajo de 1.0: quien llama dicta teléfonos y correos, y al ritmo
# normal Sofía repite los dígitos más rápido de lo que la persona los comprueba.
VELOCIDAD_VOZ = 0.9

# Con 10 s sin que nadie hable, Retell le pide a Sofía un "¿Sigues ahí?"
# (`reminder_required`, que el CRM ya contesta). Diez segundos después, si sigue
# el silencio, cuelga. Con el mismo valor para las dos cosas, la llamada podía
# cortarse justo cuando tocaba preguntar.
SILENCIO_PARA_COLGAR_MS = 20_000

# Una llamada que agenda una cita dura de 3 a 5 minutos; diez es el techo para
# una muy enredada, no un objetivo.
DURACION_MAXIMA_MS = 600_000

_TIMEOUT_S = 20.0

# Prefijo internacional → códigos ISO que Retell usa en las listas de países de
# un número. Los de América y España, que es donde está quien usa el CRM. El +1
# no distingue EE. UU. de Canadá sin una tabla de ladas: se permiten los dos.
_PAISES_POR_PREFIJO: dict[str, tuple[str, ...]] = {
    "1": ("US", "CA"),
    "34": ("ES",),
    "51": ("PE",),
    "52": ("MX",),
    "53": ("CU",),
    "54": ("AR",),
    "56": ("CL",),
    "57": ("CO",),
    "58": ("VE",),
    "502": ("GT",),
    "503": ("SV",),
    "504": ("HN",),
    "505": ("NI",),
    "506": ("CR",),
    "507": ("PA",),
    "591": ("BO",),
    "593": ("EC",),
    "595": ("PY",),
    "598": ("UY",),
}


def paises_de(telefono: str) -> tuple[str, ...]:
    """Países (ISO) de un teléfono en E.164, o () si el prefijo no está en la tabla."""
    digitos = telefono.strip().lstrip("+")
    for largo in (3, 2, 1):
        if digitos[:largo] in _PAISES_POR_PREFIJO:
            return _PAISES_POR_PREFIJO[digitos[:largo]]
    return ()


class RetellAdminError(RuntimeError):
    """Retell contestó con error (o no contestó). `status` 0 = sin respuesta."""

    def __init__(self, mensaje: str, *, status: int = 0) -> None:
        super().__init__(mensaje)
        self.status = status


@dataclass
class Comprobacion:
    """Una cosa comprobada leyendo de vuelta, no fiándose del 200 al crearla."""

    nombre: str
    ok: bool
    detalle: str


@dataclass
class AgenteListo:
    agent_id: str
    version: int
    creado: bool


def _error_de(r: httpx.Response) -> RetellAdminError:
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = {}
    detalle = ""
    if isinstance(cuerpo, dict):
        detalle = str(cuerpo.get("message") or cuerpo.get("error") or "")
    detalle = detalle or r.text[:200]
    if r.status_code == 401:
        return RetellAdminError(
            "Retell no acepta la API key (401): cópiala otra vez de Retell → Settings → API Keys.",
            status=401,
        )
    return RetellAdminError(f"Retell respondió {r.status_code}: {detalle}".strip(), status=r.status_code)


def _atado_a(agent_id: str) -> list[dict]:
    """Un solo agente de entrada que sigue a su última versión publicada."""
    return [{"agent_id": agent_id, "agent_version": "latest_published", "weight": 1}]


def _coincide(actual: dict, campos: dict) -> bool:
    """Si la versión publicada ya tiene esos valores. Los objetos (el motor de
    respuesta) se comparan por las claves que se piden, no enteros: Retell les
    añade campos propios."""
    for clave, valor in campos.items():
        if isinstance(valor, dict):
            dentro = actual.get(clave)
            if not isinstance(dentro, dict):
                return False
            if any(dentro.get(k) != v for k, v in valor.items()):
                return False
        elif actual.get(clave) != valor:
            return False
    return True


class RetellAdmin:
    """Una API key = la cuenta de Retell de la clínica."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        from app.core.config import settings

        self._api_key = api_key
        # `RETELL_BASE_URL` solo cambia para apuntar al simulador de pruebas.
        self._base_url = (base_url or settings.RETELL_BASE_URL).rstrip("/")
        # Inyectable para las pruebas (httpx.MockTransport).
        self._transport = transport

    async def _pedir(
        self,
        metodo: str,
        ruta: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        admite_404: bool = False,
    ) -> dict | None:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=_TIMEOUT_S,
                transport=self._transport,
            ) as cliente:
                r = await cliente.request(metodo, ruta, json=json, params=params)
        except httpx.HTTPError as e:
            raise RetellAdminError(f"No se pudo contactar con Retell: {e}") from e
        if r.status_code == 404 and admite_404:
            return None
        if r.status_code >= 400:
            raise _error_de(r)
        if not r.content:
            return {}
        try:
            cuerpo = r.json()
        except ValueError:
            return {}
        return cuerpo if isinstance(cuerpo, dict) else {"items": cuerpo}

    # ------------------------------------------------------------- agentes
    async def crear_agente(self, campos: dict) -> dict:
        return await self._pedir("POST", "/create-agent", json=campos) or {}

    async def agente(self, agent_id: str, *, version: int | str | None = None) -> dict | None:
        """El agente, o None si no existe en esta cuenta."""
        params = {"version": version} if version is not None else None
        return await self._pedir(
            "GET", f"/get-agent/{quote(agent_id, safe='')}", params=params, admite_404=True
        )

    async def versiones(self, agent_id: str) -> list[dict]:
        ruta = f"/list-agent-versions/{quote(agent_id, safe='')}"
        todas: list[dict] = []
        params: dict = {"limit": 1000}
        # Un agente acumula una versión por cambio publicado; mil por página
        # sobra, pero se sigue la paginación por si acaso (con tope).
        for _ in range(10):
            pagina = await self._pedir("GET", ruta, params=params) or {}
            todas.extend(v for v in pagina.get("items") or [] if isinstance(v, dict))
            if not pagina.get("has_more") or not pagina.get("pagination_key"):
                break
            params = {"limit": 1000, "pagination_key": pagina["pagination_key"]}
        return todas

    async def crear_borrador(self, agent_id: str, base: int) -> int:
        cuerpo = await self._pedir(
            "POST",
            f"/create-agent-version/{quote(agent_id, safe='')}",
            json={"base_version": base},
        )
        try:
            return int((cuerpo or {})["version"])
        except (KeyError, TypeError, ValueError) as e:
            raise RetellAdminError("Retell creó el borrador pero no dijo qué versión es.") from e

    async def cambiar_borrador(self, agent_id: str, version: int, campos: dict) -> None:
        await self._pedir(
            "PATCH",
            f"/update-agent/{quote(agent_id, safe='')}",
            params={"version": version},
            json=campos,
        )

    async def publicar(self, agent_id: str, version: int) -> None:
        await self._pedir(
            "POST",
            f"/publish-agent-version/{quote(agent_id, safe='')}",
            json={"version": version},
        )

    async def publicar_cambio(self, agent_id: str, campos: dict) -> int:
        """Deja `campos` en la versión que atiende las llamadas y devuelve cuál es.

        Se parte SIEMPRE de la última publicada, nunca de un borrador suelto: lo
        que alguien dejara a medias en la consola no sale a producción de rebote.
        Si la publicada ya tiene esos valores no se crea nada (volver a guardar
        el canal no amontona versiones). Un agente que nunca se publicó —creado a
        mano y olvidado, o un alta que se cortó— se arregla publicando su último
        borrador con los cambios.
        """
        versiones = await self.versiones(agent_id)
        publicadas = [int(v["version"]) for v in versiones if v.get("is_published")]
        if publicadas:
            base = max(publicadas)
            actual = await self.agente(agent_id, version=base) or {}
            if _coincide(actual, campos):
                return base
            version = await self.crear_borrador(agent_id, base)
        elif versiones:
            version = max(int(v["version"]) for v in versiones)
        else:
            raise RetellAdminError(f"El agente {agent_id} no tiene ninguna versión en Retell.")
        if campos:
            await self.cambiar_borrador(agent_id, version, campos)
        await self.publicar(agent_id, version)
        logger.info("retell.agente.publicado", agent_id=agent_id, version=version)
        return version

    # ------------------------------------------------------------- números
    async def numero(self, telefono: str) -> dict | None:
        """El número tal como lo tiene Retell, o None si no está en la cuenta."""
        return await self._pedir(
            "GET", f"/get-phone-number/{quote(telefono, safe='')}", admite_404=True
        )

    async def comprar_numero(
        self, agent_id: str, *, nickname: str, area_code: int | None = None
    ) -> dict:
        """Compra un número de EE. UU. (Retell no vende de México) ya atado."""
        cuerpo: dict = {
            "country_code": "US",
            "inbound_agents": _atado_a(agent_id),
            "nickname": nickname,
        }
        if area_code:
            cuerpo["area_code"] = area_code
        return await self._pedir("POST", "/create-phone-number", json=cuerpo) or {}

    async def atar_numero(
        self, telefono: str, agent_id: str, *, termination_uri: str | None = None
    ) -> dict:
        cuerpo: dict = {"inbound_agents": _atado_a(agent_id)}
        if termination_uri:
            cuerpo["termination_uri"] = termination_uri
        return (
            await self._pedir(
                "PATCH", f"/update-phone-number/{quote(telefono, safe='')}", json=cuerpo
            )
            or {}
        )

    async def importar_numero(
        self, telefono: str, *, termination_uri: str, agent_id: str, nickname: str
    ) -> dict:
        """Da de alta en Retell un número que vive en una troncal SIP (Twilio).

        Retell identifica los números por el número mismo: importar dos veces es
        un error, así que si ya está se actualiza, que es lo que se quería.
        """
        if await self.numero(telefono) is not None:
            return await self.atar_numero(telefono, agent_id, termination_uri=termination_uri)
        return (
            await self._pedir(
                "POST",
                "/import-phone-number",
                json={
                    "phone_number": telefono,
                    "termination_uri": termination_uri,
                    "inbound_agents": _atado_a(agent_id),
                    "nickname": nickname,
                },
            )
            or {}
        )

    async def cambiar_paises_de_salida(self, telefono: str, paises: list[str]) -> dict:
        """Los países a los que el número puede llamar (y pasar llamadas)."""
        return (
            await self._pedir(
                "PATCH",
                f"/update-phone-number/{quote(telefono, safe='')}",
                json={"allowed_outbound_country_list": paises},
            )
            or {}
        )


def campos_de_la_casa(*, ws_url: str, webhook_url: str) -> dict:
    """Lo que el CRM fija siempre en su agente. Sin el motor no contesta el CRM;
    sin el webhook, las llamadas llegan sin duración ni grabación; sin la URL
    firmada, la grabación es pública."""
    return {
        "response_engine": {"type": "custom-llm", "llm_websocket_url": ws_url},
        "language": IDIOMA,
        "webhook_url": webhook_url,
        "opt_in_signed_url": True,
        "end_call_after_silence_ms": SILENCIO_PARA_COLGAR_MS,
        "max_call_duration_ms": DURACION_MAXIMA_MS,
    }


async def asegurar_agente(
    admin: RetellAdmin,
    *,
    agent_id: str | None,
    ws_url: str,
    webhook_url: str,
    voice_id: str | None,
    nombre: str,
) -> AgenteListo:
    """Deja un agente del CRM publicado y apuntando a esta instalación.

    Con `agent_id` (uno que ya creó el CRM) se reutiliza: instalar dos veces no
    puede dejar dos agentes. Si ya no existe en Retell (lo borraron), se crea
    otro. La voz solo se cambia si se pide.
    """
    campos = campos_de_la_casa(ws_url=ws_url, webhook_url=webhook_url)
    if agent_id and await admin.agente(agent_id) is not None:
        if voice_id:
            campos["voice_id"] = voice_id
        version = await admin.publicar_cambio(agent_id, campos)
        return AgenteListo(agent_id=agent_id, version=version, creado=False)

    nuevo = await admin.crear_agente(
        {
            **campos,
            "agent_name": nombre,
            "voice_id": voice_id or VOZ_POR_DEFECTO,
            "voice_speed": VELOCIDAD_VOZ,
        }
    )
    nuevo_id = str(nuevo.get("agent_id") or "")
    if not nuevo_id:
        raise RetellAdminError("Retell creó el agente pero no devolvió su id.")
    version = int(nuevo.get("version") or 0)
    await admin.publicar(nuevo_id, version)
    logger.info("retell.agente.creado", agent_id=nuevo_id, version=version)
    return AgenteListo(agent_id=nuevo_id, version=version, creado=True)


async def verificar_agente(
    admin: RetellAdmin, agent_id: str, *, ws_url: str, webhook_url: str
) -> list[Comprobacion]:
    """Lee la versión que atiende las llamadas y comprueba lo que la haría fallar."""
    try:
        publicado = await admin.agente(agent_id, version="latest_published")
    except RetellAdminError as e:
        if e.status not in (400, 422):
            raise
        publicado = None
    if not publicado:
        return [
            Comprobacion(
                "agente_publicado",
                False,
                "Retell no tiene una versión publicada de este agente: el número no contestaría.",
            )
        ]
    motor = publicado.get("response_engine") or {}
    motor_url = str(motor.get("llm_websocket_url") or motor.get("type") or "sin motor")
    idioma = publicado.get("language")
    webhook = str(publicado.get("webhook_url") or "sin webhook")
    firmadas = bool(publicado.get("opt_in_signed_url"))
    return [
        Comprobacion("agente_publicado", True, f"versión {publicado.get('version')}"),
        Comprobacion(
            "contesta_el_crm",
            motor.get("type") == "custom-llm" and motor.get("llm_websocket_url") == ws_url,
            motor_url,
        ),
        Comprobacion("idioma_es_419", idioma == IDIOMA, str(idioma)),
        Comprobacion("webhook_del_crm", webhook == webhook_url, webhook),
        Comprobacion(
            "grabaciones_firmadas",
            firmadas,
            "enlace firmado y con caducidad" if firmadas else "la grabación sería un enlace público",
        ),
    ]


async def verificar_numero(
    admin: RetellAdmin, telefono: str, agent_id: str
) -> list[Comprobacion]:
    numero = await admin.numero(telefono)
    if numero is None:
        return [Comprobacion("numero_en_retell", False, f"{telefono} no está en la cuenta de Retell")]
    atados = [
        str(a.get("agent_id"))
        for a in numero.get("inbound_agents") or []
        if isinstance(a, dict) and a.get("agent_id")
    ]
    # Campo antiguo de un solo agente, que alguna respuesta todavía trae.
    antiguo = numero.get("inbound_agent_id")
    if antiguo and str(antiguo) not in atados:
        atados.append(str(antiguo))
    return [
        Comprobacion("numero_en_retell", True, telefono),
        Comprobacion(
            "numero_atado_al_agente",
            agent_id in atados,
            ", ".join(atados) or "sin agente de entrada",
        ),
    ]


async def permitir_llamar_a(admin: RetellAdmin, telefono: str, recepcion: str) -> Comprobacion:
    """Que el número pueda pasar la llamada al teléfono de recepción.

    Retell solo deja llamar a los países de la lista del número
    (`allowed_outbound_country_list`; vacía = todos). Los números dados de alta
    en el curso 02 traen solo México: con recepción en EE. UU., Retell rechazaba
    la transferencia («Transfer destination is not supported») y Sofía decía
    «te paso» sin que sonara nada (V-5 en vivo). Se añade el país de recepción,
    y solo ese: la lista es una defensa contra el fraude de llamadas.
    """
    try:
        numero = await admin.numero(telefono)
        if numero is None:
            return Comprobacion(
                "recepcion_permitida", False, f"{telefono} no está en la cuenta de Retell"
            )
        lista = [str(p) for p in numero.get("allowed_outbound_country_list") or []]
        if not lista:
            return Comprobacion("recepcion_permitida", True, "puede llamar a cualquier país")
        paises = paises_de(recepcion)
        if not paises:
            return Comprobacion(
                "recepcion_permitida",
                False,
                f"no sé de qué país es {recepcion}: en Retell, el número solo llama a "
                + ", ".join(lista),
            )
        faltan = [p for p in paises if p not in lista]
        if not faltan:
            return Comprobacion("recepcion_permitida", True, ", ".join(lista))
        await admin.cambiar_paises_de_salida(telefono, lista + faltan)
    except RetellAdminError as e:
        return Comprobacion("recepcion_permitida", False, str(e))
    logger.info("retell.numero.paises", telefono=telefono, anadidos=faltan)
    return Comprobacion(
        "recepcion_permitida", True, "se añadió " + ", ".join(faltan) + " a los países del número"
    )
