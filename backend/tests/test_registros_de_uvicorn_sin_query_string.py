"""Las líneas de uvicorn no llevan la query string.

Salió en el dry-run 2 (S-1, crm2): el registro de `app` enseñaba
`GET /api/v1/webchat/history?visitor_id=…&token=…` con el token de sesión del
widget en claro. Con el mismo camino acabarían ahí el ticket del WebSocket del
inbox y lo que el operador busca en Contactos.
"""
import logging

from app.core.logging import RutaSinQueryString, configure_logging


def _linea(nombre: str, formato: str, args: tuple) -> str:
    registro = logging.LogRecord(nombre, logging.INFO, __file__, 1, formato, args, None)
    logging.getLogger(nombre).filter(registro)
    return registro.getMessage()


def test_el_acceso_al_historial_del_widget_no_deja_el_token_en_el_registro():
    configure_logging("INFO")
    linea = _linea(
        "uvicorn.access",
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.5.5:59058", "GET",
         "/api/v1/webchat/history?visitor_id=cbe2c63b&token=v1.f2ba7f.1790516071.secreto",
         "1.1", 200),
    )
    assert linea == '10.0.5.5:59058 - "GET /api/v1/webchat/history HTTP/1.1" 200'


def test_el_websocket_del_inbox_no_deja_el_ticket_en_el_registro():
    configure_logging("INFO")
    linea = _linea(
        "uvicorn.error",
        '%s - "WebSocket %s" [accepted]',
        ("10.0.5.5:40000", "/api/v1/ws/inbox?ticket=un-solo-uso"),
    )
    assert "ticket" not in linea
    assert "/api/v1/ws/inbox" in linea


def test_una_busqueda_de_contactos_no_deja_el_nombre_en_el_registro():
    configure_logging("INFO")
    linea = _linea(
        "uvicorn.access",
        '%s - "%s %s HTTP/%s" %d',
        ("10.0.5.5:1", "GET", "/api/v1/contacts?search=Juan%20P%C3%A9rez", "1.1", 200),
    )
    assert "Juan" not in linea
    assert "/api/v1/contacts" in linea


def test_lo_que_no_es_una_ruta_se_queda_como_estaba():
    registro = logging.LogRecord(
        "uvicorn.error", logging.INFO, __file__, 1, "%s: %s", ("¿Qué?", 3), None
    )
    RutaSinQueryString().filter(registro)
    assert registro.getMessage() == "¿Qué?: 3"


def test_configurar_dos_veces_no_duplica_el_filtro():
    configure_logging("INFO")
    configure_logging("INFO")
    filtros = [f for f in logging.getLogger("uvicorn.access").filters
               if isinstance(f, RutaSinQueryString)]
    assert len(filtros) == 1
