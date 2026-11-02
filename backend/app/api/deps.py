import re
from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_token, is_token_revoked
from app.db.session import get_db
from app.models.user import User, UserRole

bearer_scheme = HTTPBearer(auto_error=False)


def get_token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth and auth.lower().startswith("bearer "):
        return auth.split(" ", 1)[1].strip()
    return None


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    # Solo se acepta el token por cabecera Authorization. Aceptarlo por
    # query-string en HTTP filtra el token a logs / Referer / proxies.
    # El WebSocket del inbox usa tickets efímeros (POST /auth/ws-ticket).
    token: str | None = creds.credentials if creds else None

    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token requerido")

    try:
        payload = decode_token(token)
    except ValueError as e:
        # Mensaje genérico: el detalle de python-jose ("Signature verification
        # failed…") describe internals que no aportan nada al cliente legítimo.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido o caducado") from e

    if await is_token_revoked(token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token revocado")

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token inválido")

    user = (await db.execute(select(User).where(User.id == UUID(sub)))).scalar_one_or_none()
    if not user or not user.activo:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Usuario no válido")
    if _issued_before_password_change(payload, user):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token revocado")
    if user.role == UserRole.revisor and not _ruta_de_revisor(request.url.path):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Permisos insuficientes")
    if user.debe_cambiar_password and not _ruta_con_contrasena_inicial(request.url.path):
        # 403 y no 401: la sesión es buena, lo que falta es un paso. Un 401
        # echaría al login y ahí volvería a entrar con la misma contraseña, en
        # bucle. El código va en el detalle para que el panel sepa a dónde
        # llevar a la persona sin adivinar por el texto.
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Cambia la contraseña inicial antes de usar el panel "
            "(debe_cambiar_password)",
        )
    return user


# Lo ÚNICO que puede pedir un revisor. Lista blanca, no lista negra: si mañana
# aparece un endpoint nuevo, por defecto queda fuera de su alcance en vez de
# quedar abierto por olvido. Se comprueba aquí, en el sitio por el que pasan
# todas las peticiones con sesión, y no endpoint a endpoint.
#
# Ojo con lo que NO está: nada de /admin (salvo lo de la cuenta de Instagram),
# ni contactos, ni base de conocimiento, ni etiquetas, ni envíos masivos, ni
# copias.
#
# Sí está `oauth/instagram/start`, y tiene su porqué: el guion de revisión de
# Meta le manda hacer el Business Login entero con SU cuenta, con su pantalla de
# permisos, y sin eso la revisión no pasa. Lo que evita el estropicio no es
# cerrarle la ruta, es dónde acaba su token: el arranque marca el `state` con su
# rol y el callback lo guarda en una fila aparte
# (`instagram_oauth.REVISOR_NAME`), sin tocar la cuenta del negocio ni la
# configuración del canal.
# Lo único que se puede hacer con la contraseña con la que se desplegó la
# instalación: mirarse a uno mismo, cambiarla y salir. Todo lo demás espera.
#
# Es una lista blanca por el mismo motivo que la del revisor: un endpoint nuevo
# nace fuera de alcance en vez de quedar abierto por olvido. Y se comprueba
# aquí, donde pasan todas las peticiones con sesión, y no endpoint a endpoint.
# Ojo: la contraseña se cambia con PATCH /auth/me, no con una ruta propia
# (`/auth/change-password` no existe; está en la lista del revisor por
# herencia). Por eso `/auth/me` tiene que estar aquí, y por eso el corte no
# puede ser por método.
RUTAS_CON_CONTRASENA_INICIAL = (
    r"/api/v1/auth/me",
    r"/api/v1/auth/logout",
)

_RUTAS_CONTRASENA_INICIAL_RE = re.compile(
    "|".join(f"(?:{r})" for r in RUTAS_CON_CONTRASENA_INICIAL)
)


def _ruta_con_contrasena_inicial(path: str) -> bool:
    return bool(_RUTAS_CONTRASENA_INICIAL_RE.fullmatch(path.rstrip("/") or "/"))


_UUID = r"[0-9a-fA-F-]{36}"
RUTAS_DE_REVISOR = (
    r"/api/v1/auth/me",
    r"/api/v1/auth/logout",
    r"/api/v1/auth/ws-ticket",  # tiempo real de la bandeja
    r"/api/v1/auth/change-password",
    r"/api/v1/conversations",  # la lista (ya filtrada a Instagram)
    r"/api/v1/conversations/counts",
    rf"/api/v1/conversations/{_UUID}",
    rf"/api/v1/conversations/{_UUID}/messages",
    rf"/api/v1/conversations/{_UUID}/mark-read",
    rf"/api/v1/conversations/{_UUID}/take-over",
    rf"/api/v1/conversations/{_UUID}/return-to-bot",
    r"/api/v1/admin/oauth/instagram/profile",  # ficha de la cuenta, de solo leer
    r"/api/v1/admin/oauth/instagram/start",  # conectar SU cuenta (va a fila aparte)
    r"/api/v1/admin/oauth/instagram/revisor-profile",  # la cuenta que él conectó
    r"/api/v1/uploads/[^/]+",  # adjuntos de los mensajes que sí puede ver
    r"/audios/[^/]+",  # notas de voz de esos mismos mensajes
)

_RUTAS_DE_REVISOR_RE = re.compile("|".join(f"(?:{r})" for r in RUTAS_DE_REVISOR))


def _ruta_de_revisor(path: str) -> bool:
    return bool(_RUTAS_DE_REVISOR_RE.fullmatch(path.rstrip("/") or "/"))


def _issued_before_password_change(payload: dict, user: User) -> bool:
    """True si el token se emitió ANTES del último cambio de contraseña.

    Un token robado deja de valer en cuanto la víctima cambia su clave: los
    endpoints que cambian/resetean contraseña sellan `password_changed_at` y
    aquí se rechaza cualquier `iat` anterior. Comparación en epoch (int) para
    que un login inmediatamente posterior (mismo segundo) siga siendo válido.
    """
    changed_at = user.password_changed_at
    if not changed_at:
        return False
    iat = payload.get("iat")
    if not isinstance(iat, (int, float)):
        return True  # sin iat no podemos situarlo: fuera
    return int(iat) < int(changed_at.timestamp())


def require_role(*roles: UserRole) -> Callable[[User], Awaitable[User]]:
    async def _checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Permisos insuficientes")
        return user
    return _checker


# ---------------------------------------------------------------------------
# MAPA DE ROLES (leer antes de tocar una dependencia de un endpoint)
#
# Hay tres roles. Entre admin y cliente la diferencia NO es "ve más / ve menos":
# es qué puede hacer. El tercero, revisor, sí es de ver menos, y va aparte. El
# criterio, fijo:
#
#   admin    → lo irreversible, lo masivo y lo que cuesta dinero.
#              Borrar contactos, borrar etiquetas (afecta a todas las
#              conversaciones), volcar toda la base de contactos a CSV,
#              importar en bloque, archivar en lote, sacar del antispam
#              (re-dispara el bot), toda la base de conocimiento y
#              todo /admin/*.
#
#              La base de conocimiento es de admin ENTERA, también lo que solo
#              lee: los 13 endpoints de api/knowledge_base.py llevan
#              `Depends(require_admin)`. Leer, porque ahí vive lo interno del
#              negocio (precios, procedimientos, políticas); y `POST /kb/search`
#              además, porque cada búsqueda es una llamada de pago a embeddings.
#              Lo comprueba por HTTP (403 con sesión de operador) el test
#              `test_la_base_de_conocimiento_es_de_admin` de
#              tests/test_roles_permisos_http.py — que NO está marcado xfail:
#              afirma el 403 y pasa en verde.
#
#   cliente  → operador de bandeja. El día a día de una conversación:
#              leer, responder, adjuntar, tomar el control, devolver al bot,
#              archivar/desarchivar UNA conversación, cerrar, marcar leído,
#              redactar/afinar/descartar borradores, ver y editar la ficha de
#              un contacto, sus notas, su actividad y sus etiquetas.
#
#   revisor  → quien revisa la app en Meta. Solo la bandeja de Instagram, poder
#              responder en ella, ver de qué cuenta de Instagram se trata y
#              conectar la SUYA con Business Login (que es lo que Meta exige
#              ver). Ni contactos, ni base de conocimiento, ni llamadas, ni
#              /admin/*, ni los otros canales. Su token de Instagram se guarda
#              en una fila aparte, así que conectarse no echa la cuenta de casa
#              ni toca el canal. Se le corta arriba, en `get_current_user`,
#              contra RUTAS_DE_REVISOR; el canal se le fija en
#              api/conversations.py. La cuenta se crea para una revisión y se
#              borra al acabar.
#
# `get_current_user` = "hay una sesión válida" y equivale hoy a rol operador.
# Se usa tal cual en los endpoints del día a día; `require_admin` marca el
# resto. No existe una dependencia `require_cliente`: sería un sinónimo de
# `get_current_user` y era código muerto (nadie la usaba), así que se ha quitado
# en vez de dar la falsa sensación de que algo la comprobaba. Al revisor lo que
# lo acota no es una dependencia: es la lista blanca de rutas de arriba.
# ---------------------------------------------------------------------------

require_admin = require_role(UserRole.admin)
