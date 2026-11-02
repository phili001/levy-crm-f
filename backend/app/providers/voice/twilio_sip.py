"""La troncal SIP de Twilio que lleva un número de México hasta Retell (Fase 8).

Retell solo vende números de EE. UU. y Canadá. Un número mexicano se compra en
Twilio y llega a Retell por una troncal SIP (Elastic SIP Trunking):

    quien llama ──▶ número de Twilio ──▶ troncal SIP ──▶ sip:sip.retellai.com ──▶ Sofía

Portado de `agente-voz-ghl/app/services/twilio_service.py` (curso 02), con
`httpx` en vez del SDK de Twilio, como el resto de proveedores. Cuatro cosas
tienen que existir en la troncal, y el número tiene que estar colgado de ella:

  1. La troncal, con un dominio `<prefijo>.pstn.twilio.com` único en TODO
     Twilio (no solo en tu cuenta: "sofia" ya lo tiene alguien).
  2. La originación: a dónde manda la troncal las llamadas que ENTRAN. Sin ella
     el número suena, la llamada muere en la troncal y quien llama oye silencio.
  3. La lista de IPs de Retell, para que Retell pueda sacar llamadas por ella
     (transferencias y salientes).
  4. Las transferencias permitidas (SIP REFER, también a teléfonos normales),
     para que Sofía pueda pasar la llamada a recepción (Fase 8.2).

Todo es idempotente: volver a lanzarlo encuentra lo que ya existe en vez de
duplicarlo. Si el número ya cuelga de una troncal que solo apunta a Retell (la
del curso 02, por ejemplo) se usa esa; si apunta a otro sitio no se toca y el
número se pasa a la troncal propia de esta instalación, porque un número solo
puede ir a un sitio.

El Account SID y el Auth Token se usan solo durante el alta y NO se guardan: el
CRM no los necesita para atender llamadas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

from app.core.logging import get_logger
from app.providers.voice.retell_admin import Comprobacion

logger = get_logger(__name__)

API_BASE = "https://api.twilio.com/2010-04-01"
TRUNKING_BASE = "https://trunking.twilio.com/v1"

# La única dirección SIP de Retell (docs.retellai.com/deploy/custom-telephony).
# La antigua `5t4n6j0wnrl.sip.livekit.cloud` deja de funcionar el 30-sep-2026.
RETELL_SIP_URI = "sip:sip.retellai.com"

# Rangos desde los que Retell manda tráfico SIP, según su guía de telefonía
# propia (consultada el 2026-09-25). La guía de Twilio solo menciona el
# primero, pero Retell también sale por los otros: con uno solo, una
# transferencia podía fallar según por dónde saliera.
RETELL_SIP_CIDRS = (
    "18.98.16.120/30",
    "3.42.144.0/23",
    "153.57.128.0/18",
    "143.223.88.0/21",
    "161.115.160.0/19",
)

_NOMBRE_ACL = "Retell SBC"
_TIMEOUT_S = 20.0


class TwilioSipError(RuntimeError):
    def __init__(self, mensaje: str, *, status: int = 0) -> None:
        super().__init__(mensaje)
        self.status = status


@dataclass
class TroncalLista:
    trunk_sid: str
    # Es también la `termination_uri` que Retell necesita al importar el número.
    dominio: str
    creada: bool
    # El número cambió de troncal (o no estaba en ninguna).
    movido: bool


def prefijo_para(host: str) -> str:
    """`crm.clinica.mx` → `crm-clinica-mx`: único por instalación y válido
    como dominio de Twilio (letras, dígitos y guiones)."""
    limpio = re.sub(r"[^a-z0-9-]+", "-", host.lower()).strip("-")
    return (limpio or "sofia-voz")[:63]


def _error_de(r: httpx.Response) -> TwilioSipError:
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = {}
    detalle = str(cuerpo.get("message") or "") if isinstance(cuerpo, dict) else ""
    if r.status_code == 401:
        return TwilioSipError(
            "Twilio no acepta el Account SID o el Auth Token (401): cópialos otra vez de la "
            "portada de la consola de Twilio.",
            status=401,
        )
    return TwilioSipError(
        f"Twilio respondió {r.status_code}: {detalle or r.text[:200]}".strip(), status=r.status_code
    )


def _todas_a_retell(originaciones: list[dict]) -> bool:
    return all("retellai.com" in str(o.get("sip_url") or "") for o in originaciones)


class TwilioSip:
    """Una cuenta de Twilio (Account SID + Auth Token)."""

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        api_base: str = API_BASE,
        trunking_base: str = TRUNKING_BASE,
    ) -> None:
        self._sid = account_sid.strip()
        self._auth = httpx.BasicAuth(self._sid, auth_token.strip())
        self._transport = transport
        self._cuenta = f"{api_base.rstrip('/')}/Accounts/{self._sid}"
        self._trunking = trunking_base.rstrip("/")

    async def _pedir(
        self, metodo: str, url: str, *, datos: dict | None = None, params: dict | None = None
    ) -> dict:
        try:
            async with httpx.AsyncClient(
                auth=self._auth, timeout=_TIMEOUT_S, transport=self._transport
            ) as cliente:
                r = await cliente.request(metodo, url, data=datos, params=params)
        except httpx.HTTPError as e:
            raise TwilioSipError(f"No se pudo contactar con Twilio: {e}") from e
        if r.status_code >= 400:
            raise _error_de(r)
        try:
            cuerpo = r.json()
        except ValueError:
            return {}
        return cuerpo if isinstance(cuerpo, dict) else {}

    async def numero(self, telefono: str) -> dict:
        cuerpo = await self._pedir(
            "GET",
            f"{self._cuenta}/IncomingPhoneNumbers.json",
            params={"PhoneNumber": telefono, "PageSize": 5},
        )
        encontrados = cuerpo.get("incoming_phone_numbers") or []
        if not encontrados:
            raise TwilioSipError(
                f"El número {telefono} no está en esta cuenta de Twilio. Revisa que el Account "
                "SID sea el de la cuenta donde lo compraste."
            )
        return encontrados[0]

    async def troncal(self, trunk_sid: str) -> dict:
        return await self._pedir("GET", f"{self._trunking}/Trunks/{trunk_sid}")

    async def buscar_troncal(self, dominio: str) -> dict | None:
        cuerpo = await self._pedir("GET", f"{self._trunking}/Trunks", params={"PageSize": 1000})
        return next(
            (t for t in cuerpo.get("trunks") or [] if t.get("domain_name") == dominio), None
        )

    async def crear_troncal(self, dominio: str, nombre: str) -> dict:
        try:
            return await self._pedir(
                "POST",
                f"{self._trunking}/Trunks",
                datos={"FriendlyName": nombre[:64], "DomainName": dominio},
            )
        except TwilioSipError as e:
            if e.status in (400, 409) and "already" in str(e).lower():
                raise TwilioSipError(
                    f"El dominio {dominio} ya lo usa otra cuenta de Twilio (tiene que ser único "
                    "en todo Twilio). Elige otro prefijo para la troncal.",
                    status=e.status,
                ) from e
            raise

    async def originaciones(self, trunk_sid: str) -> list[dict]:
        cuerpo = await self._pedir(
            "GET", f"{self._trunking}/Trunks/{trunk_sid}/OriginationUrls", params={"PageSize": 50}
        )
        return list(cuerpo.get("origination_urls") or [])

    async def asegurar_originacion(self, trunk_sid: str) -> None:
        """Que las llamadas que entran por la troncal vayan a Retell."""
        for o in await self.originaciones(trunk_sid):
            if o.get("sip_url") == RETELL_SIP_URI:
                if not o.get("enabled"):
                    await self._pedir(
                        "POST",
                        f"{self._trunking}/Trunks/{trunk_sid}/OriginationUrls/{o['sid']}",
                        datos={"Enabled": "true"},
                    )
                return
        await self._pedir(
            "POST",
            f"{self._trunking}/Trunks/{trunk_sid}/OriginationUrls",
            datos={
                "FriendlyName": "Retell",
                "SipUrl": RETELL_SIP_URI,
                "Weight": 10,
                "Priority": 10,
                "Enabled": "true",
            },
        )

    async def autorizar_retell(self, trunk_sid: str) -> str:
        """La lista de IPs de Retell, completa y colgada de la troncal."""
        listas = await self._pedir(
            "GET", f"{self._cuenta}/SIP/IpAccessControlLists.json", params={"PageSize": 100}
        )
        acl = next(
            (
                a
                for a in listas.get("ip_access_control_lists") or []
                if a.get("friendly_name") == _NOMBRE_ACL
            ),
            None,
        )
        if acl is None:
            acl = await self._pedir(
                "POST",
                f"{self._cuenta}/SIP/IpAccessControlLists.json",
                datos={"FriendlyName": _NOMBRE_ACL},
            )
        direcciones = await self._pedir(
            "GET",
            f"{self._cuenta}/SIP/IpAccessControlLists/{acl['sid']}/IpAddresses.json",
            params={"PageSize": 100},
        )
        tiene = {
            (d.get("ip_address"), str(d.get("cidr_prefix_length")))
            for d in direcciones.get("ip_addresses") or []
        }
        for cidr in RETELL_SIP_CIDRS:
            red, largo = cidr.split("/")
            if (red, largo) not in tiene:
                await self._pedir(
                    "POST",
                    f"{self._cuenta}/SIP/IpAccessControlLists/{acl['sid']}/IpAddresses.json",
                    datos={"FriendlyName": f"Retell {cidr}", "IpAddress": red, "CidrPrefixLength": largo},
                )
        # Asociar dos veces la misma lista no es un no-op en Twilio: da error.
        if not any(a.get("sid") == acl["sid"] for a in await self.listas_de_ips(trunk_sid)):
            await self._pedir(
                "POST",
                f"{self._trunking}/Trunks/{trunk_sid}/IpAccessControlLists",
                datos={"IpAccessControlListSid": acl["sid"]},
            )
        return str(acl["sid"])

    async def permitir_transferencias(self, trunk_sid: str) -> None:
        """Que la troncal acepte pasar llamadas (SIP REFER) también a teléfonos
        normales, mostrando a recepción el número de quien llama (Fase 8.2).

        Sin esto, cuando Sofía pasa la llamada Twilio rechaza la transferencia
        y quien llama se queda colgado. Cada minuto del tramo transferido lo
        cobra Twilio; pasar a un número de otro país pide además activarlo en
        Voice Geographic Permissions → Elastic SIP Trunking.
        """
        troncal = await self.troncal(trunk_sid)
        if (
            troncal.get("transfer_mode") == "enable-all"
            and troncal.get("transfer_caller_id") == "from-transferee"
        ):
            return
        await self._pedir(
            "POST",
            f"{self._trunking}/Trunks/{trunk_sid}",
            datos={"TransferMode": "enable-all", "TransferCallerId": "from-transferee"},
        )

    async def listas_de_ips(self, trunk_sid: str) -> list[dict]:
        """Las listas de IPs colgadas de la troncal (quién puede sacar llamadas)."""
        cuerpo = await self._pedir(
            "GET", f"{self._trunking}/Trunks/{trunk_sid}/IpAccessControlLists", params={"PageSize": 50}
        )
        return list(cuerpo.get("ip_access_control_lists") or [])

    async def mover_numero(self, numero_sid: str, trunk_sid: str) -> None:
        """Colgar el número de la troncal: es lo que lo quita de la app o webhook
        al que apuntara antes."""
        await self._pedir(
            "POST",
            f"{self._cuenta}/IncomingPhoneNumbers/{numero_sid}.json",
            datos={"TrunkSid": trunk_sid},
        )


async def conectar_numero(
    twilio: TwilioSip, *, telefono: str, prefijo: str, nombre: str
) -> TroncalLista:
    """Deja el número colgado de una troncal que manda sus llamadas a Retell."""
    if not telefono.startswith("+"):
        raise TwilioSipError(f"El número tiene que ir con + y lada de país (+52…), no «{telefono}».")
    entrante = await twilio.numero(telefono)
    actual = entrante.get("trunk_sid") or ""
    troncal: dict | None = None
    creada = False
    if actual and _todas_a_retell(await twilio.originaciones(actual)):
        troncal = await twilio.troncal(actual)
    if troncal is None:
        dominio = f"{prefijo}.pstn.twilio.com"
        troncal = await twilio.buscar_troncal(dominio)
        if troncal is None:
            troncal = await twilio.crear_troncal(dominio, nombre)
            creada = True
    sid = str(troncal["sid"])
    await twilio.asegurar_originacion(sid)
    await twilio.autorizar_retell(sid)
    await twilio.permitir_transferencias(sid)
    movido = actual != sid
    if movido:
        await twilio.mover_numero(str(entrante["sid"]), sid)
    logger.info("twilio.troncal.lista", trunk_sid=sid, creada=creada, movido=movido)
    return TroncalLista(
        trunk_sid=sid, dominio=str(troncal["domain_name"]), creada=creada, movido=movido
    )


async def verificar(twilio: TwilioSip, telefono: str) -> list[Comprobacion]:
    """Lee de vuelta el cableado: un 200 al crear dice que se aceptó, no que la
    llamada llegue."""
    try:
        entrante = await twilio.numero(telefono)
    except TwilioSipError as e:
        return [Comprobacion("numero_en_twilio", False, str(e))]
    checks = [Comprobacion("numero_en_twilio", True, telefono)]
    sid = entrante.get("trunk_sid") or ""
    checks.append(Comprobacion("numero_en_la_troncal", bool(sid), sid or "sin troncal"))
    if not sid:
        return checks
    a_retell = [
        o for o in await twilio.originaciones(sid) if "retellai.com" in str(o.get("sip_url") or "")
    ]
    checks.append(
        Comprobacion(
            "entrada_hacia_retell",
            any(o.get("enabled") for o in a_retell),
            ", ".join(str(o.get("sip_url")) for o in a_retell) or "la troncal no manda nada a Retell",
        )
    )
    nombres = [str(a.get("friendly_name")) for a in await twilio.listas_de_ips(sid)]
    checks.append(
        Comprobacion(
            "retell_autorizado",
            _NOMBRE_ACL in nombres,
            ", ".join(nombres) or "sin lista de IPs",
        )
    )
    modo = str((await twilio.troncal(sid)).get("transfer_mode") or "sin configurar")
    checks.append(Comprobacion("transferencias_permitidas", modo == "enable-all", modo))
    return checks
