"""Lectura y escritura de las banderas de módulos, y el guardia para la API.

Uso en un endpoint que pertenece a un módulo apagable:

    @router.get("/outbound/jobs", dependencies=[Depends(require_flag(FLAG_ENVIO_MASIVO))])

Con la bandera apagada la ruta responde 404, no 403: para quien instala
siguiendo el curso ese módulo no existe, y un 403 invitaría a buscar cómo
conseguir permiso. Las tareas periódicas consultan `is_enabled` al arrancar y
salen sin hacer nada si el módulo está apagado.

Sin caché a propósito: es una lectura por clave primaria en rutas de admin y en
una tarea diaria. Si algún día una bandera protege algo de alto tráfico, ahí
se añade la caché, no antes.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import db_session, get_db
from app.models.feature_flag import BANDERAS, FeatureFlag


async def is_enabled(clave: str, db: AsyncSession | None = None) -> bool:
    """True si la bandera existe y está encendida. Desconocida o ausente = apagada."""
    if clave not in BANDERAS:
        return False
    stmt = select(FeatureFlag.activa).where(FeatureFlag.clave == clave)
    if db is not None:
        return bool((await db.execute(stmt)).scalar_one_or_none())
    async with db_session() as own_db:
        return bool((await own_db.execute(stmt)).scalar_one_or_none())


async def list_flags(db: AsyncSession) -> dict[str, bool]:
    """Todas las banderas conocidas con su estado (las sin fila salen apagadas)."""
    rows = (await db.execute(select(FeatureFlag.clave, FeatureFlag.activa))).all()
    estado = {clave: bool(activa) for clave, activa in rows}
    return {clave: estado.get(clave, False) for clave in BANDERAS}


async def set_flag(clave: str, activa: bool, db: AsyncSession) -> None:
    """Enciende o apaga una bandera conocida (upsert). No hace commit: lo hace el llamante."""
    if clave not in BANDERAS:
        raise ValueError(f"Bandera desconocida: {clave}")
    stmt = (
        pg_insert(FeatureFlag)
        .values(clave=clave, activa=activa)
        .on_conflict_do_update(index_elements=[FeatureFlag.clave], set_={"activa": activa})
    )
    await db.execute(stmt)


def require_flag(clave: str) -> Callable[[AsyncSession], Awaitable[None]]:
    """Dependencia de FastAPI: 404 si el módulo está apagado."""

    async def _guardia(db: AsyncSession = Depends(get_db)) -> None:
        if not await is_enabled(clave, db):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Este módulo está desactivado en esta instalación.",
            )

    return _guardia
