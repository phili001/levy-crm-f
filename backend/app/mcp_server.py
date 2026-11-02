"""El servidor MCP del CRM, servido por la propia API en `/mcp` (v1.2.0).

Expone la agent-api (`/api/v1/agent-api`) como herramientas MCP para que Claude
(u otro asistente) mire la instalación y trabaje en ella sin entrar al panel.

Por qué vive dentro de la API y no en un contenedor aparte: `omni.py update`
solo cambia APP_VERSION en Coolify y vuelve a desplegar, y el compose guardado
en Coolify se queda como estaba el día de la instalación (app, worker, beat,
frontend, db, redis). Un servicio nuevo no llegaría nunca a las instalaciones
que ya existen; dentro de `app` llega con una actualización normal. El nginx
del panel reenvía `/mcp` a la API (frontend/nginx.conf), así que la URL es la
del propio CRM.

Es un cliente FINO de la agent-api: cada herramienta hace UNA llamada HTTP a su
endpoint reenviando el `Authorization: Bearer <token>` de la petición MCP. Los
ámbitos, el límite de 120 peticiones/minuto por token y la auditoría (con el
nombre del token) se siguen aplicando en un solo sitio, la agent-api, y una
herramienta no puede hacer nada que el token no pueda hacer por HTTP. Sin token
en la petición no se llama a nada. No hay token «de reserva» en el backend, y
no debe haberlo: con uno, este endpoint público quedaría abierto a cualquiera
con los ámbitos de ese token.
"""
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import httpx
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import Field
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.core.config import settings
from app.core.logging import configure_logging, get_logger

# Antes de crear el FastMCP de abajo: su constructor llama a `logging.basicConfig`
# y, si llegara primero, el registro raíz de la API se quedaría con su handler
# (stderr, sin nivel DEBUG) y el nuestro ya no entraría. Repetirlo es inocuo.
configure_logging("INFO" if settings.is_production else "DEBUG")
logger = get_logger(__name__)

# El paquete `mcp` escribe una línea INFO por cada petición («Processing request
# of type CallToolRequest») y otra al arrancar. En los registros del contenedor
# solo estorban; sus avisos y errores sí se quieren ver.
logging.getLogger("mcp").setLevel(logging.WARNING)

RUTA = "/mcp"
_AGENT_API = "/api/v1/agent-api"
# Lo que se espera a la agent-api. Crear o editar un documento de la base de
# conocimiento lo indexa en la misma llamada (embeddings), y eso tarda.
_ESPERA_S = 60.0
# Tope del cuerpo de una petición MCP. El transporte se lee el cuerpo entero en
# memoria ANTES de saber si trae token, y el endpoint es público: sin tope,
# cualquiera podría mandar 25 MB (el límite del nginx) en bucle. El mayor uso
# legítimo es un documento de 200.000 caracteres, que en JSON con caracteres
# escapados no llega a 2,5 MB.
_CUERPO_MAX = 4 * 1024 * 1024

# Sin la comprobación de Host/Origin (la protección contra «DNS rebinding»). Esa
# defensa es para servidores MCP SIN autenticación que escuchan en localhost:
# una web maliciosa apunta su dominio a 127.0.0.1 y les habla desde el
# navegador de la víctima. Aquí no aplica: este servidor está en internet a
# propósito y lo protege el token de cada petición, que una web ajena no tiene
# (no viaja en una cookie: el navegador no lo manda solo). Encendida, toda
# petición con `Host: crm.<dominio>` sale con un 421 salvo que se liste el
# dominio, y cada instalación tiene el suyo (y lo puede cambiar).
#
# Va apagada EXPLÍCITAMENTE, no por omisión: las versiones nuevas del paquete la
# encienden solas en algunos casos, y subir de versión no puede dejar el MCP de
# todas las instalaciones contestando 421. Lo que sigue en pie: el POST tiene
# que ser `application/json`, lo que impide que un formulario de otra web lance
# una petición «simple» sin pasar por el CORS de la API.
_SEGURIDAD = TransportSecuritySettings(enable_dns_rebinding_protection=False)

mcp = FastMCP(
    name="crm",
    instructions=(
        "Herramientas para operar el CRM a través de su agent-api: métricas "
        "agregadas y salud, metadata de conversaciones, la base de conocimiento "
        "(leer, buscar, crear y editar), los prompts de los agentes y, para los "
        "flujos de n8n, leer y contestar conversaciones y actualizar fichas. "
        "Cada petición va con el token de agente como Bearer (Authorization: "
        "Bearer agt_…) y el CRM aplica sus ámbitos: monitor:read, "
        "interactions:read, kb:read, kb:write, prompts:write, classifier:write, "
        "conversations:read, conversations:reply y contacts:write. "
        "classifier:write no tiene herramienta MCP: se usa por HTTP directo "
        "contra la agent-api. Una herramienta sin el ámbito devuelve "
        "{\"error\": true, \"status\": 403}. Antes de editar un documento, léelo "
        "con get_kb_document: el update reemplaza el contenido ENTERO."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path=RUTA,
    transport_security=_SEGURIDAD,
)


# ---------------------------------------------------------------------------
# La llamada a la agent-api
# ---------------------------------------------------------------------------


def _error(detalle: Any, estado: int | None = None) -> str:
    """El mismo formato de error que el servidor MCP de antes: los asistentes
    que ya lo conocían no tienen que aprender otro."""
    cuerpo: dict[str, Any] = {"error": True}
    if estado is not None:
        cuerpo["status"] = estado
    cuerpo["detail"] = detalle
    return json.dumps(cuerpo, ensure_ascii=False)


def _peticion_http(ctx: Context) -> Any:
    """La petición HTTP que trajo la llamada (starlette.Request), o None."""
    try:
        return ctx.request_context.request
    except Exception:  # noqa: BLE001 — fuera de una petición no hay nada que leer
        return None


def _token(peticion: Any) -> str | None:
    if peticion is None:
        return None
    cabecera = peticion.headers.get("authorization") or ""
    if cabecera[:7].lower() != "bearer ":
        return None
    return cabecera[7:].strip() or None


class _IdInvalido(ValueError):
    pass


def _ruta(plantilla: str, ids: dict[str, str] | None) -> str:
    """Rellena la ruta con identificadores que tienen que ser UUID.

    La llamada sale de DENTRO del contenedor, así que un `document_id` como
    `../../admin/users` acabaría en otra parte de la API (httpx resuelve los
    `..`). Con el token de agente no pasaría de un 401, pero no hay por qué
    dejar que el MCP llame a algo que no es la agent-api: todos los
    identificadores de estas rutas son UUID, y se exige que lo sean.
    """
    limpios: dict[str, str] = {}
    for nombre, valor in (ids or {}).items():
        try:
            limpios[nombre] = str(uuid.UUID(str(valor).strip()))
        except ValueError:
            raise _IdInvalido(nombre) from None
    return plantilla.format(**limpios)


def _cliente() -> httpx.AsyncClient:
    """Un cliente nuevo por llamada, hacia la propia API.

    Por llamada y no compartido: un cliente se queda atado al event loop en el
    que se creó, y con uno global habría que cerrarlo al apagar. Hablar con
    127.0.0.1 cuesta una conexión local, nada. Las pruebas lo sustituyen por
    uno que entra en la app por ASGI.
    """
    base = settings.MCP_AGENT_API_BASE_URL.rstrip("/") + _AGENT_API
    return httpx.AsyncClient(base_url=base, timeout=_ESPERA_S)


async def _llamar(
    ctx: Context,
    metodo: str,
    ruta: str,
    *,
    ids: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    cuerpo: dict[str, Any] | None = None,
) -> str:
    """Una llamada a la agent-api con el token de quien llama; devuelve su JSON.

    Si algo va mal (sin token, identificador inválido, red o respuesta que no
    es 2xx) devuelve `{"error": true, "status"?, "detail"}` con el mensaje de la
    API tal cual.
    """
    peticion = _peticion_http(ctx)
    token = _token(peticion)
    if not token:
        return _error(
            "Falta el token de agente. Conecta el cliente MCP con la cabecera "
            "'Authorization: Bearer <token>' (los tokens se crean en el panel, "
            "en Conexiones → API / MCP)."
        )
    try:
        ruta = _ruta(ruta, ids)
    except _IdInvalido as e:
        return _error(f"'{e}' no es un identificador válido (UUID).", 422)

    cabeceras = {"Authorization": f"Bearer {token}"}
    # La cadena de proxies de la petición original, tal cual. El límite por IP
    # de la API (core/ratelimit.py) cuenta desde la derecha: así la llamada
    # interna cae en el cubo de quien llama y no en uno compartido de
    # 127.0.0.1, que con varios asistentes se llenaría entre todos.
    reenviado = peticion.headers.get("x-forwarded-for") if peticion is not None else None
    if reenviado:
        cabeceras["X-Forwarded-For"] = reenviado
    limpios = {k: v for k, v in (params or {}).items() if v is not None} or None

    try:
        async with _cliente() as cliente:
            resp = await cliente.request(
                metodo, ruta, params=limpios, json=cuerpo, headers=cabeceras
            )
    except httpx.HTTPError as e:
        # Sin el mensaje de httpx hacia fuera: lleva la URL interna. Al
        # registro va el tipo de error, que es lo que sirve para el diagnóstico.
        logger.warning("mcp.agent_api.red", ruta=ruta, error=type(e).__name__)
        return _error("No se pudo contactar con la agent-api del CRM. Inténtalo de nuevo.")

    if not 200 <= resp.status_code < 300:
        try:
            datos = resp.json()
            detalle = (
                (datos.get("detail") or datos.get("message") or datos)
                if isinstance(datos, dict)
                else datos
            )
        except ValueError:
            detalle = resp.text
        return _error(detalle, resp.status_code)

    try:
        return json.dumps(resp.json(), ensure_ascii=False)
    except ValueError:
        return resp.text


# ===========================================================================
# monitor:read — métricas AGREGADAS (sin contenido de mensajes ni PII)
# ===========================================================================


@mcp.tool()
async def monitor_overview(ctx: Context, range: str = "7d") -> str:
    """Visión agregada del bot para un rango temporal.

    Devuelve conversaciones por estado/canal, handoffs, volumen de mensajes por
    rol, coste/uso LLM del rango y canales pausados. Sin contenido de mensajes
    ni datos personales de clientes. Requiere el ámbito 'monitor:read'.

    GET /monitor/overview?range=... (valores: today | 7d | 30d | 90d).
    """
    return await _llamar(ctx, "GET", "/monitor/overview", params={"range": range})


@mcp.tool()
async def get_llm_costs(ctx: Context, range: str = "7d") -> str:
    """Coste y uso LLM del rango: por agente, por modelo y serie por día.

    Misma query que el dashboard admin de uso de tokens (los números cuadran
    con el panel). Solo agregados: llamadas, tokens y coste estimado en USD.
    Requiere el ámbito 'monitor:read'.
    GET /monitor/llm-costs?range=... (valores: 24h | 7d | 30d | month).
    """
    return await _llamar(ctx, "GET", "/monitor/llm-costs", params={"range": range})


@mcp.tool()
async def get_channels_status(ctx: Context) -> str:
    """Estado operativo de cada canal del bot.

    Por canal: pausado o activo, si está configurado/habilitado, si tiene
    agente asignado y la última entrada de cliente recibida (proxy del último
    webhook). Incluye los errores recientes del runtime (saneados: solo
    ts/level/event/canal). Requiere el ámbito 'monitor:read'.
    GET /monitor/channels.
    """
    return await _llamar(ctx, "GET", "/monitor/channels")


@mcp.tool()
async def get_health(ctx: Context) -> str:
    """Salud operativa del backend: BD, Redis y backlog de colas.

    Devuelve {status, db, redis, queues:{celery_backlog,
    outbound_jobs_pending}}. Requiere el ámbito 'monitor:read'.
    GET /monitor/health.
    """
    return await _llamar(ctx, "GET", "/monitor/health")


# ===========================================================================
# interactions:read — metadata de conversaciones (sin contenido ni PII: el
# contacto viaja solo como UUID)
# ===========================================================================


@mcp.tool()
async def list_interactions(
    ctx: Context,
    status: str | None = None,
    canal: str | None = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> str:
    """Lista conversaciones con SOLO metadata operativa (sin contenido ni PII).

    Por conversación: id, canal, estado, created_at, last_activity_at,
    segundos en el estado actual, si está asignada a un operador y el contacto
    como UUID. Filtros opcionales: status (bot | humano | cerrada) y canal
    (whatsapp | web | instagram_dm | email | retell_voice). Ordenadas por
    última actividad descendente. Requiere el ámbito 'interactions:read'.
    GET /interactions?status=&canal=&limit=.
    """
    return await _llamar(
        ctx, "GET", "/interactions", params={"status": status, "canal": canal, "limit": limit}
    )


@mcp.tool()
async def get_interaction_metadata(conversation_id: str, ctx: Context) -> str:
    """Metadata de UNA conversación (sin contenido de mensajes ni PII).

    Igual de limpia que el listado, y además: ended_at, derivada_a_humano_at,
    archived/quarantined y conteo de mensajes por rol. Nunca devuelve texto de
    mensajes ni datos del contacto (solo su UUID). Requiere el ámbito
    'interactions:read'. GET /interactions/{conversation_id}/metadata.
    """
    return await _llamar(
        ctx,
        "GET",
        "/interactions/{conversation_id}/metadata",
        ids={"conversation_id": conversation_id},
    )


# ===========================================================================
# kb:read / kb:write — base de conocimiento
# ===========================================================================


@mcp.tool()
async def list_kb_documents(ctx: Context) -> str:
    """Lista los documentos de la base de conocimiento.

    Devuelve id, nombre, formato y si es editable, entre otros. Requiere el
    ámbito 'kb:read'. GET /kb/documents.
    """
    return await _llamar(ctx, "GET", "/kb/documents")


@mcp.tool()
async def get_kb_document(document_id: str, ctx: Context) -> str:
    """Obtiene el contenido completo (texto) de un documento de la KB.

    Devuelve {document_id, nombre, formato, editable, contenido}. Léelo ANTES
    de editar, porque update_kb_document reemplaza el contenido entero.
    Requiere el ámbito 'kb:read'. GET /kb/documents/{document_id}.
    """
    return await _llamar(
        ctx, "GET", "/kb/documents/{document_id}", ids={"document_id": document_id}
    )


@mcp.tool()
async def search_kb(
    query: Annotated[str, Field(min_length=1, max_length=500)],
    ctx: Context,
    top_k: Annotated[int, Field(ge=1, le=8)] = 5,
) -> str:
    """Búsqueda semántica (RAG) en la base de conocimiento.

    Devuelve los fragmentos más relevantes para la consulta. top_k entre 1 y 8
    (por defecto 5). Requiere el ámbito 'kb:read'.
    POST /kb/search con body {query, top_k}.
    """
    return await _llamar(ctx, "POST", "/kb/search", cuerpo={"query": query, "top_k": top_k})


@mcp.tool()
async def create_kb_document(
    titulo: Annotated[str, Field(min_length=1, max_length=200)],
    contenido: Annotated[str, Field(min_length=1, max_length=200_000)],
    ctx: Context,
) -> str:
    """Crea un documento de texto (.txt) en la base de conocimiento.

    Se indexa automáticamente (chunks + embeddings) y queda auditado con el
    token que lo creó. Requiere el ámbito 'kb:write'.
    POST /kb/documents con body {titulo, contenido}.
    """
    return await _llamar(
        ctx, "POST", "/kb/documents", cuerpo={"titulo": titulo, "contenido": contenido}
    )


@mcp.tool()
async def update_kb_document(
    document_id: str,
    contenido: Annotated[str, Field(min_length=1, max_length=200_000)],
    ctx: Context,
) -> str:
    """Edita un documento de texto (txt/md) de la base de conocimiento.

    Reemplaza el contenido ENTERO (no hace parches): léelo antes con
    get_kb_document. La versión anterior queda en el historial (restaurable
    desde el panel) y se reindexa al momento. Los formatos binarios
    (pdf/docx/xlsx) no son editables (HTTP 409). Requiere el ámbito 'kb:write'.
    PUT /kb/documents/{document_id} con body {contenido}.
    """
    return await _llamar(
        ctx,
        "PUT",
        "/kb/documents/{document_id}",
        ids={"document_id": document_id},
        cuerpo={"contenido": contenido},
    )


# ===========================================================================
# prompts:write — leer y mejorar los prompts de los agentes
# ===========================================================================


@mcp.tool()
async def list_agents(ctx: Context) -> str:
    """Lista los agentes con su prompt_system actual.

    Devuelve {agent_id, name, is_active, model_name, prompt_system} por agente.
    Requiere el ámbito 'prompts:write'. GET /agents.
    """
    return await _llamar(ctx, "GET", "/agents")


@mcp.tool()
async def update_agent_prompt(
    agent_id: str,
    prompt_system: Annotated[str, Field(min_length=1, max_length=50_000)],
    ctx: Context,
) -> str:
    """Reemplaza el prompt_system de un agente.

    El prompt anterior queda en el historial (restaurable desde el panel de
    Agentes). Las reglas de seguridad del sistema (anti-inyección, transparencia
    IA) NO forman parte del prompt y no se pueden alterar por aquí. El cambio
    afecta al bot que habla con clientes AL INSTANTE: haz cambios incrementales.
    Requiere el ámbito 'prompts:write'.
    PUT /agents/{agent_id}/prompt con body {prompt_system}.
    """
    return await _llamar(
        ctx,
        "PUT",
        "/agents/{agent_id}/prompt",
        ids={"agent_id": agent_id},
        cuerpo={"prompt_system": prompt_system},
    )


# ===========================================================================
# conversations:read / conversations:reply / contacts:write — para n8n cuando
# hace de cerebro de un agente. Son los ÚNICOS ámbitos que tocan contenido de
# mensajes y datos del paciente: un token solo los tiene si el administrador
# los marca (preset «Para n8n»).
# ===========================================================================


@mcp.tool()
async def get_conversation_messages(
    conversation_id: str, ctx: Context, limit: Annotated[int, Field(ge=1, le=200)] = 50
) -> str:
    """El hilo de una conversación, del más viejo al más nuevo: rol (paciente,
    sofia, operador), texto, tipo y enlace firmado de 1 h a los archivos. Las
    sugerencias de Entrenamiento sin enviar van marcadas. Requiere el ámbito
    'conversations:read'. GET /conversations/{conversation_id}/messages.
    """
    return await _llamar(
        ctx,
        "GET",
        "/conversations/{conversation_id}/messages",
        ids={"conversation_id": conversation_id},
        params={"limit": limit},
    )


@mcp.tool()
async def send_conversation_message(
    conversation_id: str,
    ctx: Context,
    texto: Annotated[str | None, Field(max_length=8000)] = None,
    archivos: list[dict[str, Any]] | None = None,
    idempotency_key: Annotated[str | None, Field(max_length=100)] = None,
) -> str:
    """Contesta en una conversación como si fuera Sofía (sale por su canal).
    `archivos`: [{tipo: imagen|audio|video|documento, url, pie?, nombre?}].
    409 si la tomó una persona o la ventana de 24 h está cerrada; en
    Entrenamiento queda como sugerencia. Requiere 'conversations:reply'.
    POST /conversations/{conversation_id}/messages.
    """
    return await _llamar(
        ctx,
        "POST",
        "/conversations/{conversation_id}/messages",
        ids={"conversation_id": conversation_id},
        cuerpo={"texto": texto, "archivos": archivos or [], "idempotency_key": idempotency_key},
    )


@mcp.tool()
async def handoff_conversation(
    conversation_id: str,
    ctx: Context,
    motivo: Annotated[str, Field(max_length=200)] = "n8n lo pasa a una persona",
    mensaje_al_cliente: Annotated[str | None, Field(max_length=1000)] = None,
) -> str:
    """Pasa la conversación a una persona: mensaje puente al paciente y aviso a
    la operadora. Requiere 'conversations:reply'.
    POST /conversations/{conversation_id}/handoff.
    """
    return await _llamar(
        ctx,
        "POST",
        "/conversations/{conversation_id}/handoff",
        ids={"conversation_id": conversation_id},
        cuerpo={"motivo": motivo, "mensaje_al_cliente": mensaje_al_cliente},
    )


@mcp.tool()
async def update_contact(
    contact_id: str,
    ctx: Context,
    nombre: str | None = None,
    email: str | None = None,
    etapa: str | None = None,
    etiquetas_agregar: list[str] | None = None,
) -> str:
    """Cambia nombre, correo, etapa (contacto, cotizacion, seguimiento,
    paciente, perdido, no_califica) o agrega etiquetas a una ficha. Nunca el
    teléfono. Requiere 'contacts:write'. PATCH /contacts/{contact_id}.
    """
    return await _llamar(
        ctx,
        "PATCH",
        "/contacts/{contact_id}",
        ids={"contact_id": contact_id},
        cuerpo={
            "nombre": nombre,
            "email": email,
            "etapa": etapa,
            "etiquetas_agregar": etiquetas_agregar or [],
        },
    )


# ---------------------------------------------------------------------------
# El endpoint /mcp y su arranque
# ---------------------------------------------------------------------------


class _PuertaMCP:
    """El endpoint ASGI de `/mcp`: pasa cada petición al gestor del transporte.

    Transporte Streamable HTTP SIN estado y con respuestas JSON:
      - Sin estado: cada petición se atiende sola, sin sesión guardada en la
        memoria del proceso. Un redespliegue o un reinicio no deja a los
        clientes con una sesión que ya no existe (404 hasta que reconectan), da
        igual cuántos procesos de uvicorn haya, y no se acumulan sesiones
        abiertas (el aviso de seguridad del paquete sobre eso no aplica).
        Ninguna herramienta necesita lo que da la sesión: notificaciones del
        servidor, progreso o preguntas al cliente.
      - JSON en vez de un stream SSE: cada llamada es una petición y una
        respuesta normales, que ningún proxy (Traefik, nginx, Cloudflare) se
        queda a medias por guardarlas en su búfer.
    """

    def __init__(self) -> None:
        self.gestor: StreamableHTTPSessionManager | None = None
        # slowapi nombra cada ruta con `__module__` + `__name__` para aplicarle
        # el límite por IP de toda la API. Una instancia no trae `__name__` y,
        # sin él, su middleware tumbaba cada petición a /mcp con un 500.
        self.__name__ = "mcp"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["method"] == "GET":
            # El GET abre el stream SSE por el que el servidor manda mensajes
            # por su cuenta. Sin estado no hay nada que mandar, y abrirlo
            # dejaría una conexión colgada hasta que el proxy la cortara. El
            # protocolo prevé el 405 para esto: el cliente sigue solo con POST.
            respuesta = JSONResponse(
                {"detail": "Este servidor MCP no abre stream SSE: usa POST."},
                status_code=405,
                headers={"Allow": "POST"},
            )
            await respuesta(scope, receive, send)
            return
        if self.gestor is None:
            # Solo entre que el proceso acepta conexiones y termina de arrancar.
            respuesta = JSONResponse(
                {"detail": "El servidor MCP está arrancando. Inténtalo en unos segundos."},
                status_code=503,
            )
            await respuesta(scope, receive, send)
            return
        await self.gestor.handle_request(scope, receive, send)


puerta = _PuertaMCP()

# Las dos formas, con y sin barra final, van a la MISMA puerta. Con una sola,
# la otra recibiría una redirección 307 que arma la API con el esquema que ve
# (http, detrás del proxy) y un POST redirigido de https a http no llega.
RUTAS = [
    Route(RUTA, endpoint=puerta, include_in_schema=False, max_body_size=_CUERPO_MAX),
    Route(RUTA + "/", endpoint=puerta, include_in_schema=False, max_body_size=_CUERPO_MAX),
]


@asynccontextmanager
async def mcp_en_marcha() -> AsyncIterator[None]:
    """El gestor del transporte, vivo mientras viva la app (va en su lifespan).

    Un gestor nuevo en cada arranque: el paquete solo deja arrancar cada uno
    una vez, y las pruebas arrancan la app varias veces en el mismo proceso.
    """
    gestor = StreamableHTTPSessionManager(
        app=mcp._mcp_server,
        event_store=None,
        json_response=True,
        stateless=True,
        security_settings=_SEGURIDAD,
    )
    async with gestor.run():
        puerta.gestor = gestor
        try:
            yield
        finally:
            puerta.gestor = None
