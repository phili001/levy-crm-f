"""Dos reservas del mismo hueco a la vez: la que Postgres corta por bloqueo
mutuo se repite una vez y cae en «ocupado», en vez de acabar en un error.

Salió en el Release de la 0.12.1: con Postgres 17,
`test_dos_canales_a_la_vez_por_el_mismo_hueco_solo_uno_se_lo_lleva` falló con
`deadlock detected`. Las dos filas se esperaban una a la otra por la
restricción de exclusión y quien perdía recibía la excepción, no horarios
nuevos. Aquí se prueba la regla sin depender de que el choque ocurra.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import DBAPIError, IntegrityError


class _ErrorDeAsyncpg(Exception):
    def __init__(self, sqlstate: str | None) -> None:
        super().__init__("error de asyncpg")
        self.sqlstate = sqlstate


def _error_de_bd(sqlstate: str | None, texto: str, clase: type[DBAPIError] = DBAPIError):
    orig = Exception(texto)
    orig.__cause__ = _ErrorDeAsyncpg(sqlstate)
    return clase("INSERT INTO appointments ...", {}, orig)


def test_reconoce_el_bloqueo_mutuo_por_su_codigo_o_por_el_texto():
    from app.services.agenda import _es_bloqueo_mutuo

    assert _es_bloqueo_mutuo(_error_de_bd("40P01", "error"))
    assert _es_bloqueo_mutuo(_error_de_bd(None, "deadlock detected"))
    assert not _es_bloqueo_mutuo(
        _error_de_bd("23P01", "conflicting key value violates exclusion constraint")
    )


@pytest.mark.asyncio
async def test_se_repite_una_vez_si_postgres_la_corta_por_bloqueo_mutuo():
    from app.services.agenda import _reintentar_si_bloqueo

    intentos: list[int] = []

    async def reservar() -> str:
        intentos.append(1)
        if len(intentos) == 1:
            raise _error_de_bd("40P01", "deadlock detected")
        return "ocupado o guardada, pero sin excepción"

    assert await _reintentar_si_bloqueo(reservar) == "ocupado o guardada, pero sin excepción"
    assert len(intentos) == 2


@pytest.mark.asyncio
async def test_otros_errores_no_se_repiten_y_dos_bloqueos_seguidos_no_hacen_bucle():
    from app.services.agenda import _reintentar_si_bloqueo

    intentos: list[int] = []

    async def clave_repetida() -> None:
        intentos.append(1)
        raise _error_de_bd("23505", "duplicate key value", IntegrityError)

    with pytest.raises(IntegrityError):
        await _reintentar_si_bloqueo(clave_repetida)
    assert len(intentos) == 1, "un error que no es de bloqueo sale tal cual"

    intentos.clear()

    async def siempre_bloqueada() -> None:
        intentos.append(1)
        raise _error_de_bd("40P01", "deadlock detected")

    with pytest.raises(DBAPIError):
        await _reintentar_si_bloqueo(siempre_bloqueada)
    assert len(intentos) == 2, "se repite una sola vez"
