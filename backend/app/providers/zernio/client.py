"""Cliente HTTP de Zernio (Instagram, Messenger y WhatsApp por una sola API).

Zernio habla con Meta como una única app ya aprobada: la clínica solo hace
OAuth con su cuenta y se ahorra crear una app de Meta y pasar App Review. Aquí
va la parte que llama a su API REST; lo que entra por el webhook se interpreta
en `dispatch.py`.

Escrito contra la doc oficial (docs.zernio.com y su OpenAPI v1.11) y los
eventos reales de `tests/fixtures/zernio/`. Decisiones que no son obvias:

- `httpx` directo y NO el SDK `zernio-sdk`: el SDK no manda `Idempotency-Key`,
  y sin ella un reintento manda el mensaje dos veces al paciente.
- El id del mensaje enviado es `data.messageId` (el `mid` de la plataforma, el
  mismo que luego trae el eco como `platformMessageId`). Leerlo de otro sitio
  deja al CRM sin forma de reconocer su propio eco.
- Un `500` al ENVIAR no se reintenta: Zernio no reintenta los envíos y puede
  haberlo entregado ya. Se avisa como fallo y decide una persona.
- Un `429` se respeta con `Retry-After`, una sola vez y con la MISMA llave de
  idempotencia (si el primero llegó a salir, Zernio devuelve el original).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)

ZERNIO_BASE_URL = "https://zernio.com/api/v1"

# Más de esto esperando un 429 y es mejor fallar: el agente o la operadora
# están esperando a que el mensaje salga.
_MAX_RETRY_AFTER_S = 10.0
_TIMEOUT_S = 15.0


class ZernioError(RuntimeError):
    """Zernio contestó con error (o no contestó). `status` 0 = sin respuesta."""

    def __init__(self, mensaje: str, *, status: int = 0, code: str = "") -> None:
        super().__init__(mensaje)
        self.status = status
        self.code = code


class ZernioAuthError(ZernioError):
    """La API key no vale (401): caducada, borrada o mal copiada."""


class ZernioWindowClosed(ZernioError):
    """Meta rechazó el envío por la ventana de mensajería (`platform_api_error`)."""


class ZernioAllowanceExceeded(ZernioError):
    """Se acabaron los 10,000 salientes gratis del mes y no hay tarjeta."""


@dataclass
class ZernioEnvio:
    """Resultado de un envío: el `mid` real y lo que Zernio avise de paso."""

    message_id: str
    conversation_id: str = ""
    warnings: list = field(default_factory=list)
    replayed: bool = False


def _error_de(r: httpx.Response) -> ZernioError:
    """Traduce una respuesta de error a la excepción que le toca."""
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = {}
    if not isinstance(cuerpo, dict):
        cuerpo = {}
    code = str(cuerpo.get("code") or cuerpo.get("error_code") or "")
    detalle = str(cuerpo.get("error") or cuerpo.get("message") or r.text[:200] or "")
    mensaje = f"Zernio respondió {r.status_code}: {detalle}".strip()
    if r.status_code == 401:
        return ZernioAuthError(mensaje, status=401, code=code)
    if code == "message_allowance_exceeded":
        return ZernioAllowanceExceeded(mensaje, status=r.status_code, code=code)
    if r.status_code in (400, 403) and code == "platform_api_error":
        return ZernioWindowClosed(mensaje, status=r.status_code, code=code)
    return ZernioError(mensaje, status=r.status_code, code=code)


def _retry_after(r: httpx.Response) -> float:
    try:
        return max(0.0, float(r.headers.get("retry-after") or 0))
    except ValueError:
        return 0.0


def _retry_after_del_cuerpo(r: httpx.Response) -> float:
    """Zernio también lo dice en el texto: «Please retry after 55 seconds»."""
    import re

    m = re.search(r"retry after (\d+)", r.text or "", re.IGNORECASE)
    return float(m.group(1)) if m else 1.0


class ZernioClient:
    """Una API key = una cuenta de Zernio de la clínica."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_espera_429: float = _MAX_RETRY_AFTER_S,
        intentos_429: int = 2,
        intervalo_min_s: float = 0.0,
    ) -> None:
        """`max_espera_429`, `intentos_429` e `intervalo_min_s` son para los
        trabajos largos en segundo plano (el historial): ahí no hay nadie
        esperando, así que se va al ritmo que marca Zernio (60 peticiones por
        minuto con 1-2 cuentas) en vez de chocar con su límite y abandonar."""
        from app.core.config import settings

        self._api_key = api_key
        # Configurable (`ZERNIO_BASE_URL`) solo para apuntarla al simulador.
        self._base_url = (base_url or settings.ZERNIO_BASE_URL or ZERNIO_BASE_URL).rstrip("/")
        # Inyectable para las pruebas (httpx.MockTransport).
        self._transport = transport
        self._max_espera_429 = max_espera_429
        self._intentos_429 = max(2, intentos_429)
        self._intervalo_min_s = intervalo_min_s
        self._ultima = 0.0

    def _cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=_TIMEOUT_S,
            transport=self._transport,
        )

    async def _pedir(
        self,
        metodo: str,
        ruta: str,
        *,
        params: dict | None = None,
        json: dict | None = None,
        headers: dict | None = None,
        reintentar_5xx: bool = True,
    ) -> httpx.Response:
        """Una petición con la política de reintentos de arriba."""
        intentos = self._intentos_429
        for intento in range(intentos):
            if self._intervalo_min_s:
                pausa = self._ultima + self._intervalo_min_s - time.monotonic()
                if pausa > 0:
                    await asyncio.sleep(pausa)
                self._ultima = time.monotonic()
            try:
                async with self._cliente() as c:
                    r = await c.request(metodo, ruta, params=params, json=json, headers=headers)
            except httpx.HTTPError as e:
                raise ZernioError(f"Zernio no responde: {type(e).__name__}") from e
            ultimo = intento == intentos - 1
            if r.status_code == 429 and not ultimo:
                espera = _retry_after(r) or _retry_after_del_cuerpo(r)
                if espera <= self._max_espera_429:
                    logger.warning("zernio.rate_limited", ruta=ruta, espera_s=espera)
                    await asyncio.sleep(espera)
                    continue
            if r.status_code >= 500 and reintentar_5xx and intento == 0:
                await asyncio.sleep(1)
                continue
            if r.status_code >= 400:
                raise _error_de(r)
            return r
        raise ZernioError("Zernio no responde", status=0)  # pragma: no cover

    # ---------- Cuentas y webhooks ----------

    async def list_accounts(self, profile_id: str | None = None) -> list[dict]:
        """Cuentas conectadas. Sirve también para validar la llave: es la
        llamada más barata que la exige."""
        params = {"profileId": profile_id} if profile_id else None
        r = await self._pedir("GET", "/accounts", params=params)
        return list((r.json() or {}).get("accounts") or [])

    async def accounts_health(self) -> dict:
        r = await self._pedir("GET", "/accounts/health")
        return r.json() or {}

    async def register_webhook(
        self, url: str, secret: str, events: list[str], *, name: str = "CRM"
    ) -> dict:
        """Registra el endpoint. El `secret` lo elegimos nosotros: Zernio no lo
        genera, y es con lo que después verificamos la firma."""
        r = await self._pedir(
            "POST",
            "/webhooks/settings",
            json={"name": name[:50], "url": url, "secret": secret, "events": events},
        )
        return (r.json() or {}).get("webhook") or {}

    async def delete_webhook(self, webhook_id: str) -> None:
        await self._pedir("DELETE", "/webhooks/settings", params={"id": webhook_id})

    async def test_webhook(self, webhook_id: str) -> None:
        await self._pedir("POST", "/webhooks/test", json={"webhookId": webhook_id})

    # ---------- Inbox ----------

    async def send_message(
        self,
        conversation_id: str,
        account_id: str,
        text: str,
        *,
        idempotency_key: str,
        attachment_url: str | None = None,
        attachment_type: str | None = None,
        messaging_type: str | None = None,
        message_tag: str | None = None,
    ) -> ZernioEnvio:
        cuerpo: dict = {"accountId": account_id, "message": text}
        if attachment_url:
            cuerpo["attachmentUrl"] = attachment_url
            cuerpo["attachmentType"] = attachment_type or "file"
        if messaging_type:
            cuerpo["messagingType"] = messaging_type
        if message_tag:
            cuerpo["messageTag"] = message_tag
        r = await self._pedir(
            "POST",
            f"/inbox/conversations/{conversation_id}/messages",
            json=cuerpo,
            headers={"Idempotency-Key": idempotency_key},
            reintentar_5xx=False,
        )
        datos = r.json() or {}
        data = datos.get("data") or {}
        message_id = str(data.get("messageId") or "")
        if not message_id:
            # Sin id no hay forma de casar el eco ni de saber qué salió.
            raise ZernioError("Zernio aceptó el envío pero no devolvió messageId", status=r.status_code)
        return ZernioEnvio(
            message_id=message_id,
            conversation_id=str(data.get("conversationId") or ""),
            warnings=list(datos.get("warnings") or []),
            replayed=(r.headers.get("idempotent-replayed") or "").lower() == "true",
        )

    async def list_conversations(
        self, account_id: str, cursor: str | None = None, *, limit: int = 50
    ) -> tuple[list[dict], str | None]:
        """Una página de conversaciones: (conversaciones, siguiente cursor)."""
        params: dict = {"accountId": account_id, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        r = await self._pedir("GET", "/inbox/conversations", params=params)
        datos = r.json() or {}
        pag = datos.get("pagination") or {}
        siguiente = pag.get("nextCursor") if pag.get("hasMore") else None
        return list(datos.get("data") or []), siguiente

    async def get_messages(
        self, conversation_id: str, account_id: str, cursor: str | None = None
    ) -> tuple[list[dict], str | None]:
        """Una página de mensajes, del más viejo al más nuevo."""
        params: dict = {"accountId": account_id, "limit": 100, "sortOrder": "asc"}
        if cursor:
            params["cursor"] = cursor
        r = await self._pedir(
            "GET", f"/inbox/conversations/{conversation_id}/messages", params=params
        )
        datos = r.json() or {}
        pag = datos.get("pagination") or {}
        siguiente = pag.get("nextCursor") if pag.get("hasMore") else None
        return list(datos.get("messages") or []), siguiente

    async def resolve_attachment(
        self, conversation_id: str, message_id: str, index: int, account_id: str
    ) -> str:
        """URL viva de un adjunto. Las de la CDN de Meta caducan; esta llamada
        las vuelve a firmar. `message_id` es el `platformMessageId`."""
        r = await self._pedir(
            "GET",
            f"/inbox/conversations/{conversation_id}/messages/{message_id}/attachments/{index}",
            params={"accountId": account_id, "format": "json"},
        )
        return str((r.json() or {}).get("url") or "")

    async def mark_read(self, conversation_id: str, account_id: str) -> None:
        await self._pedir(
            "POST", f"/inbox/conversations/{conversation_id}/read", json={"accountId": account_id}
        )

    async def typing(self, conversation_id: str, account_id: str) -> None:
        await self._pedir(
            "POST", f"/inbox/conversations/{conversation_id}/typing", json={"accountId": account_id}
        )
