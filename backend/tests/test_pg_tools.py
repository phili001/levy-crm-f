"""Tests de la elección de cliente Postgres (app/services/pg_tools.py).

Todo en local y sin Postgres real (el sondeo de versión y el árbol de binarios
se fabrican con tmp_path y monkeypatch):
  - installed_client_majors: lee /usr/lib/postgresql/<N>/bin, ignora carpetas
    sin binario y carpetas que no son un número.
  - choose_client_major: la tabla de decisión entera, incluidos los dos casos
    que costaron un fallo real (servidor 17 con cliente 16 → no hay copia;
    servidor 16 con cliente 17 → copia que no se restaura).
  - tool_path: ruta absoluta si existe, nombre pelado si no.
  - server_major: cachea por conexión y refresh=True vuelve a preguntar.
  - pg_tool: sin clientes instalados NO pregunta la versión al servidor.
  - avisos_de_cliente_postgres: qué se le dice al que despliega en cada caso.
  - filtrar_sets_desconocidos: solo caen los SET que el servidor no conoce.
"""
from __future__ import annotations

import pytest

from app.services import pg_tools
from app.services.backups import filtrar_sets_desconocidos
from app.services.pg_tools import (
    avisos_de_cliente_postgres,
    choose_client_major,
    installed_client_majors,
    tool_path,
)

DB = {"host": "db", "port": 5432, "user": "chat", "password": "x", "dbname": "chatbot"}


@pytest.fixture(autouse=True)
def _sin_cache():
    pg_tools.reset_cache()
    yield
    pg_tools.reset_cache()


def _arbol(tmp_path, versiones, *, con_binario=True):
    """Fabrica /usr/lib/postgresql de mentira con esas versiones dentro."""
    for v in versiones:
        d = tmp_path / str(v) / "bin"
        d.mkdir(parents=True)
        if con_binario:
            for nombre in ("pg_dump", "pg_restore", "psql"):
                (d / nombre).write_text("#!/bin/sh\n")
    return tmp_path


# ---------------------------------------------------------------------------
# installed_client_majors
# ---------------------------------------------------------------------------


def test_installed_client_majors_ordenadas(tmp_path):
    _arbol(tmp_path, [17, 16])
    assert installed_client_majors(tmp_path) == [16, 17]


def test_installed_client_majors_ignora_lo_que_no_es_version(tmp_path):
    _arbol(tmp_path, [16])
    (tmp_path / "common" / "bin").mkdir(parents=True)
    assert installed_client_majors(tmp_path) == [16]


def test_installed_client_majors_carpeta_sin_binario_no_cuenta(tmp_path):
    # Paquete a medias: queda el directorio pero no las herramientas.
    _arbol(tmp_path, [16], con_binario=False)
    assert installed_client_majors(tmp_path) == []


def test_installed_client_majors_sin_carpeta(tmp_path):
    assert installed_client_majors(tmp_path / "no-existe") == []


# ---------------------------------------------------------------------------
# choose_client_major — la tabla de decisión
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "servidor, disponibles, esperado",
    [
        # El caso bueno: la misma versión que el servidor.
        (16, [16, 17], 16),
        (17, [16, 17], 17),
        # Solo cliente 17 contra servidor 16: se hace la copia (el 17 sabe
        # leer un servidor 16) aunque luego haga falta el camino de compat.
        (16, [17], 17),
        # Solo cliente 16 contra servidor 17: pg_dump abortaría igual; se
        # devuelve el único que hay y el aviso de arranque lo canta.
        (17, [16], 16),
        # Servidor por debajo de todo lo instalado: el más bajo por encima.
        (14, [16, 17], 16),
        # Servidor por encima de todo lo instalado: el más alto.
        (18, [16, 17], 17),
        # Sin saber la versión del servidor: el más alto (una copia que no se
        # hace duele más que una que necesita rodeo para restaurarse).
        (None, [16, 17], 17),
        # Sin clientes no hay nada que elegir.
        (16, [], None),
        (None, [], None),
    ],
)
def test_choose_client_major(servidor, disponibles, esperado):
    assert choose_client_major(servidor, disponibles) == esperado


# ---------------------------------------------------------------------------
# tool_path
# ---------------------------------------------------------------------------


def test_tool_path_ruta_absoluta_si_existe(tmp_path):
    _arbol(tmp_path, [16])
    assert tool_path("pg_dump", 16, tmp_path) == str(tmp_path / "16" / "bin" / "pg_dump")


def test_tool_path_nombre_pelado_si_no_existe(tmp_path):
    assert tool_path("pg_dump", 16, tmp_path) == "pg_dump"


def test_tool_path_sin_version_no_inventa_ruta(tmp_path):
    _arbol(tmp_path, [16])
    assert tool_path("pg_dump", None, tmp_path) == "pg_dump"


# ---------------------------------------------------------------------------
# server_major — caché
# ---------------------------------------------------------------------------


def test_server_major_solo_pregunta_una_vez(monkeypatch):
    llamadas = []

    def falso(db):
        llamadas.append(db)
        return 16

    monkeypatch.setattr(pg_tools, "_probe_server_major", falso)
    assert pg_tools.server_major(DB) == 16
    assert pg_tools.server_major(DB) == 16
    assert len(llamadas) == 1


def test_server_major_refresh_vuelve_a_preguntar(monkeypatch):
    valores = iter([16, 17])
    monkeypatch.setattr(pg_tools, "_probe_server_major", lambda db: next(valores))
    assert pg_tools.server_major(DB) == 16
    assert pg_tools.server_major(DB, refresh=True) == 17


def test_server_major_cachea_por_conexion(monkeypatch):
    monkeypatch.setattr(
        pg_tools, "_probe_server_major", lambda db: 16 if db["host"] == "db" else 17
    )
    assert pg_tools.server_major(DB) == 16
    assert pg_tools.server_major({**DB, "host": "otra"}) == 17


# ---------------------------------------------------------------------------
# pg_tool
# ---------------------------------------------------------------------------


def test_pg_tool_sin_clientes_no_pregunta_al_servidor(monkeypatch):
    """En un portátil de desarrollo (sin PGDG) no se abre un subprocess inútil."""

    def no_debe_llamarse(db):
        raise AssertionError("sin clientes instalados no hay que sondear el servidor")

    monkeypatch.setattr(pg_tools, "installed_client_majors", lambda *a, **k: [])
    monkeypatch.setattr(pg_tools, "_probe_server_major", no_debe_llamarse)
    assert pg_tools.pg_tool("pg_dump", DB) == "pg_dump"


def test_pg_tool_elige_el_de_la_version_del_servidor(monkeypatch, tmp_path):
    _arbol(tmp_path, [16, 17])
    monkeypatch.setattr(pg_tools, "PG_BIN_ROOT", tmp_path)
    monkeypatch.setattr(pg_tools, "_probe_server_major", lambda db: 16)
    assert pg_tools.pg_tool("pg_dump", DB) == str(tmp_path / "16" / "bin" / "pg_dump")


def test_pg_tool_rechaza_herramientas_de_fuera(monkeypatch):
    with pytest.raises(ValueError):
        pg_tools.pg_tool("rm", DB)


# ---------------------------------------------------------------------------
# avisos_de_cliente_postgres
# ---------------------------------------------------------------------------


def test_aviso_ninguno_cuando_cuadran(monkeypatch, tmp_path):
    _arbol(tmp_path, [16, 17])
    monkeypatch.setattr(pg_tools, "PG_BIN_ROOT", tmp_path)
    monkeypatch.setattr(pg_tools, "_probe_server_major", lambda db: 16)
    assert avisos_de_cliente_postgres(DB) == []


def test_aviso_si_el_cliente_es_mayor_que_el_servidor(monkeypatch, tmp_path):
    _arbol(tmp_path, [17])
    monkeypatch.setattr(pg_tools, "PG_BIN_ROOT", tmp_path)
    monkeypatch.setattr(pg_tools, "_probe_server_major", lambda db: 16)
    (aviso,) = avisos_de_cliente_postgres(DB)
    assert "postgresql-client-16" in aviso
    assert "compatibilidad" in aviso


def test_aviso_gordo_si_no_hay_ningun_cliente_valido(monkeypatch, tmp_path):
    _arbol(tmp_path, [16])
    monkeypatch.setattr(pg_tools, "PG_BIN_ROOT", tmp_path)
    monkeypatch.setattr(pg_tools, "_probe_server_major", lambda db: 17)
    (aviso,) = avisos_de_cliente_postgres(DB)
    assert "NO habrá copias" in aviso


def test_aviso_si_no_se_puede_leer_la_version(monkeypatch, tmp_path):
    _arbol(tmp_path, [16, 17])
    monkeypatch.setattr(pg_tools, "PG_BIN_ROOT", tmp_path)
    monkeypatch.setattr(pg_tools, "_probe_server_major", lambda db: None)
    (aviso,) = avisos_de_cliente_postgres(DB)
    assert "DATABASE_URL" in aviso


def test_aviso_si_la_imagen_no_trae_clientes(monkeypatch, tmp_path):
    monkeypatch.setattr(pg_tools, "PG_BIN_ROOT", tmp_path / "vacio")
    monkeypatch.setattr(pg_tools, "_probe_server_major", lambda db: 16)
    (aviso,) = avisos_de_cliente_postgres(DB)
    assert "no hay ningún cliente" in aviso


# ---------------------------------------------------------------------------
# filtrar_sets_desconocidos (camino de compatibilidad del restore)
# ---------------------------------------------------------------------------


def test_filtro_quita_solo_el_parametro_que_el_servidor_no_conoce():
    conocidos = {"statement_timeout", "search_path", "lock_timeout"}
    lineas = [
        "SET statement_timeout = 0;\n",
        "SET transaction_timeout = 0;\n",
        "SET search_path = '';\n",
    ]
    buenas, quitados = filtrar_sets_desconocidos(lineas, conocidos)
    assert quitados == ["transaction_timeout"]
    assert "SET transaction_timeout = 0;\n" not in buenas
    assert len(buenas) == 2


def test_filtro_no_toca_datos_ni_ddl():
    conocidos = {"search_path"}
    lineas = [
        "CREATE TABLE public.mensajes (id integer);\n",
        "COPY public.mensajes (id) FROM stdin;\n",
        "SET SESSION AUTHORIZATION 'chat';\n",
        "1\n",
        "\\.\n",
    ]
    buenas, quitados = filtrar_sets_desconocidos(lineas, conocidos)
    assert quitados == []
    assert buenas == lineas


def test_filtro_no_se_come_una_fila_de_datos_dentro_de_un_copy():
    """El cuerpo de un COPY son datos: ahí no se filtra NADA.

    Un mensaje de un contacto que empiece por "SET transaction_timeout = 0;"
    es una fila legítima de la tabla. Borrarla al restaurar sería perder un
    registro sin que nadie se entere.
    """
    conocidos = {"search_path"}
    lineas = [
        "SET transaction_timeout = 0;\n",  # cabecera: esta SÍ cae
        "COPY public.mensajes (id, texto) FROM stdin;\n",
        "1\tSET transaction_timeout = 0;\n",
        "2\tSET transaction_timeout = 0;\n",  # sin tabulador delante tampoco
        "\\.\n",
        "SET transaction_timeout = 0;\n",  # ya fuera del COPY: cae
    ]
    buenas, quitados = filtrar_sets_desconocidos(lineas, conocidos)
    assert quitados == ["transaction_timeout", "transaction_timeout"]
    assert buenas == lineas[1:5]


def test_filtro_respeta_copys_seguidos():
    conocidos: set[str] = set()
    lineas = [
        "COPY a (x) FROM stdin;\n",
        "SET loquesea = 1;\n",
        "\\.\n",
        "SET loquesea = 1;\n",
        "COPY b (x) FROM stdin;\n",
        "SET loquesea = 1;\n",
        "\\.\n",
    ]
    buenas, quitados = filtrar_sets_desconocidos(lineas, conocidos)
    assert quitados == ["loquesea"]
    assert len(buenas) == 6


def test_filtro_exige_punto_y_coma():
    # Una línea que empieza por SET pero no cierra la sentencia no es un SET
    # de cabecera; no se toca.
    buenas, quitados = filtrar_sets_desconocidos(["SET algo = (\n"], set())
    assert quitados == []
    assert buenas == ["SET algo = (\n"]
