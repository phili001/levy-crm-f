import logging
import re
import sys

import structlog

PII_PATTERNS = [
    # Sin letras ni dígitos pegados: el trozo final de un UUID (`…-f13c92579835`)
    # no es un teléfono, y taparlo dejaba el id de la conversación inservible.
    (re.compile(r"(?<![0-9A-Za-z])\+?\d{8,15}(?![0-9A-Za-z])"), "<PHONE>"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<EMAIL>"),
    # Con guiones dentro: sk-proj-… (OpenAI), sk-ant-… (Anthropic), sk-or-v1-…
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), "<API_KEY>"),
    (re.compile(r"Bearer\s+[A-Za-z0-9_\-\.=]+", re.IGNORECASE), "Bearer <REDACTED>"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"), "<JWT>"),
]


_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _scrub_value(value):
    if isinstance(value, str):
        if _UUID.fullmatch(value):
            return value
        for pattern, repl in PII_PATTERNS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub_value(v) for v in value)
    return value


# Campos cuyo valor es un número de diagnóstico nuestro, nunca un dato de una
# persona. El patrón de teléfono tapa cualquier tirada de 8 a 15 dígitos, y una
# marca de tiempo unix tiene 10: sin esta lista, el log de una firma caducada
# salía con `timestamp_recv: "<PHONE>"` y no había forma de ver qué hora llegó,
# que es justo el dato por el que se mira ese log.
#
# Es una lista de CLAVES, no de patrones, y a propósito: ensanchar el patrón de
# teléfono para que distinga una fecha de un móvil mexicano (diez dígitos los
# dos) acabaría dejando pasar teléfonos de verdad.
CLAVES_SIN_REDACTAR = frozenset({
    "timestamp_recv",  # la `t` de la cabecera de firma de YCloud
    "desfase",         # segundos de diferencia con nuestro reloj
    "ts",
    "timestamp",
    "elapsed_ms",
    "duration_ms",
})


def _scrub_pii(_, __, event_dict):
    for key, value in list(event_dict.items()):
        if key in CLAVES_SIN_REDACTAR:
            continue
        event_dict[key] = _scrub_value(value)
    return event_dict


def _sin_query_string(valor):
    if isinstance(valor, str) and valor.startswith("/") and "?" in valor:
        return valor.split("?", 1)[0]
    return valor


class RutaSinQueryString(logging.Filter):
    """Quita la query string de las líneas de uvicorn (accesos y WebSockets).

    Uvicorn escribe la URL entera y por ahí viajan secretos y datos personales:
    el token de sesión del widget (`/webchat/history?…&token=…`, vale 12 h), el
    ticket del WebSocket del inbox y lo que el operador busca en Contactos
    (nombres, teléfonos). Esas líneas no pasan por `_scrub_pii` (no son de
    structlog) y las lee cualquiera con acceso a los registros del contenedor, o
    el servicio al que Coolify los mande. Para depurar basta la ruta.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(_sin_query_string(a) for a in record.args)
        return True


_FILTRO_RUTAS = RutaSinQueryString()


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)

    # Uvicorn ya configuró sus loggers al importar la app; el filtro se les
    # engancha a ellos (no al handler) y solo una vez aunque esto se repita.
    for nombre in ("uvicorn.access", "uvicorn.error"):
        registro = logging.getLogger(nombre)
        if _FILTRO_RUTAS not in registro.filters:
            registro.addFilter(_FILTRO_RUTAS)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            # El traceback se arma ANTES de tapar, para que también se tape, y sin
            # las variables locales de cada marco (structlog las vuelca por
            # defecto, y ahí van teléfonos, textos y contraseñas en claro).
            structlog.processors.ExceptionRenderer(
                structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
            ),
            _scrub_pii,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None):
    return structlog.get_logger(name)
