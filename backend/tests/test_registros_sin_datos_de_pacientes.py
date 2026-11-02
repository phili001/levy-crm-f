"""Lo que llega a los registros por la puerta de atrás también sale tapado.

Salió en el dry-run 2 (auditoría de seguridad, R-1 a R-3):
- el texto de un error de la base llevaba los valores de la consulta, y hay
  cientos de `error=str(e)`: el mensaje de un paciente acababa en el registro;
- un traceback volcaba las variables locales de cada marco sin tapar;
- las claves con guiones (`sk-proj-…`, `sk-ant-…`) no se reconocían.
"""
from __future__ import annotations

import asyncio
import sys

import pytest
import structlog

from app.core.logging import _scrub_value, configure_logging


def _db_available() -> bool:
    try:
        from sqlalchemy import text

        from app.db.session import db_session

        async def _check():
            async with db_session() as db:
                await db.execute(text("select 1"))

        asyncio.run(_check())
        return True
    except Exception:
        return False


needs_db = pytest.mark.skipif(
    not _db_available(),
    reason="DB no disponible en este entorno (se ejecuta en CI con Postgres)",
)


@pytest.mark.parametrize("clave", [
    "sk-proj-" + "Ab3" * 12,
    "sk-ant-api03-" + "Xy9_" * 10,
    "sk-or-v1-" + "0f" * 20,
])
def test_una_clave_con_guiones_tambien_se_tapa(clave):
    salida = _scrub_value(f"el proveedor rechazó la clave {clave}")
    assert clave not in salida
    assert "<API_KEY>" in salida


def test_un_traceback_no_vuelca_variables_locales_ni_telefonos():
    configure_logging("INFO")
    nota_del_paciente = "le duele desde el lunes"
    try:
        raise ValueError("no se pudo avisar al +529980000004")
    except ValueError:
        evento = {"event": "aviso.fallo", "exc_info": sys.exc_info()}
        for procesar in structlog.get_config()["processors"]:
            evento = procesar(None, "error", evento)
    assert "aviso.fallo" in evento and "ValueError" in evento
    assert "+529980000004" not in evento
    assert "le duele desde el lunes" not in evento
    assert "nota_del_paciente" not in evento


@needs_db
async def test_un_error_de_la_base_no_lleva_los_valores_de_la_consulta():
    from sqlalchemy import text

    from app.db.session import db_session

    with pytest.raises(Exception) as error:
        async with db_session() as db:
            await db.execute(
                text("SELECT CAST(:mensaje AS text) FROM tabla_que_no_existe_r1"),
                {"mensaje": "hola, soy Juan y me duele la muela"},
            )
    assert "me duele la muela" not in str(error.value)


@pytest.mark.parametrize("uuid_", [
    "421d44cf-2100-41e7-8d78-f13c92579835",
    "421d44cf-2100-41e7-8d78-925798351234",
])
def test_un_id_de_conversacion_no_se_tapa_como_si_fuera_un_telefono(uuid_):
    """Salió en crm2 con la 0.13.0: el worker registró el id de la conversación
    con sus últimos dígitos como `<PHONE>`, y así no hay forma de buscarla."""
    assert _scrub_value(uuid_) == uuid_
    assert _scrub_value(f"conversación {uuid_[:-12]}f13c92579835 lista") == (
        f"conversación {uuid_[:-12]}f13c92579835 lista"
    )


@pytest.mark.parametrize("texto", [
    "llamar al 9980000005 mañana", "+529980000005", "tel:+529980000005", "wa_5219980000005",
])
def test_un_telefono_de_verdad_se_sigue_tapando(texto):
    assert "9980000005" not in _scrub_value(texto)
