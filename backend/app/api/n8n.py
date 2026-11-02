"""Conexiones → n8n: las herramientas de n8n para Sofía (Fase 11, SPEC-06).

Solo administradores. El secreto de cada herramienta lo genera el CRM y nunca
sale entero en un listado: hay un endpoint aparte para copiarlo (con su
auditoría), porque hay que pegarlo en el «Header Auth» del nodo Webhook de n8n.

«Probar» llama al flujo con `prueba: true` y unos datos de ejemplo, y enseña lo
que contestó. No le escribe a ningún paciente.
"""
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.ratelimit import limit_spec, make_limiter
from app.db.session import get_db
from app.models.n8n_tool import N8nTool
from app.models.user import User
from app.schemas.common import OkResponse
from app.services import n8n_tools as svc
from app.services.audit import record_audit

router = APIRouter(prefix="/admin/n8n", tags=["n8n"])
limiter = make_limiter()


class N8nToolIn(BaseModel):
    nombre: str = Field(max_length=40)
    descripcion: str = Field(max_length=500)
    parametros: list[dict[str, Any]] = Field(default_factory=list)
    url: str = Field(max_length=500)
    accion: str = "escritura"
    espera_s: int = 8
    datos_contacto: bool = False
    activa: bool = True


class N8nToolOut(BaseModel):
    id: uuid.UUID
    nombre: str
    descripcion: str
    parametros: list[dict[str, Any]]
    url: str
    accion: str
    espera_s: int
    datos_contacto: bool
    activa: bool
    secreto_enmascarado: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SecretoOut(BaseModel):
    secreto: str
    cabecera: str = svc.CABECERA_SECRETO


class ProbarIn(BaseModel):
    args: dict[str, Any] = Field(default_factory=dict)


class ProbarOut(BaseModel):
    ok: bool
    estado: int | None = None
    ms: int = 0
    respuesta: Any = None
    error: str | None = None


def _out(t: N8nTool) -> N8nToolOut:
    return N8nToolOut(
        id=t.id,
        nombre=t.nombre,
        descripcion=t.descripcion,
        parametros=list(t.parametros or []),
        url=t.url,
        accion=t.accion,
        espera_s=t.espera_s,
        datos_contacto=t.datos_contacto,
        activa=t.activa,
        secreto_enmascarado=svc.enmascarar(t.secreto),
        created_at=t.created_at,
        updated_at=t.updated_at,
    )


def _nombres_del_crm() -> set[str]:
    from app.agents.tools import ALL_TOOLS  # importarlo registra todas

    return set(ALL_TOOLS.keys())


async def _la_herramienta(db: AsyncSession, tool_id: uuid.UUID) -> N8nTool:
    t = (await db.execute(select(N8nTool).where(N8nTool.id == tool_id))).scalar_one_or_none()
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa herramienta ya no existe.")
    return t


async def _validar(db: AsyncSession, body: N8nToolIn, *, otra_que: uuid.UUID | None = None) -> dict:
    try:
        datos = await svc.validar_herramienta(body.model_dump(), nombres_del_crm=_nombres_del_crm())
    except svc.HerramientaInvalida as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(e)) from e
    q = select(N8nTool.id).where(N8nTool.nombre == datos["nombre"])
    if otra_que is not None:
        q = q.where(N8nTool.id != otra_que)
    if (await db.execute(q)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, f"Ya hay una herramienta llamada «{datos['nombre']}».")
    return datos


@router.get("/tools", response_model=list[N8nToolOut])
async def list_n8n_tools(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> list[N8nToolOut]:
    filas = (await db.execute(select(N8nTool).order_by(N8nTool.nombre))).scalars().all()
    return [_out(t) for t in filas]


@router.post("/tools", response_model=N8nToolOut, status_code=status.HTTP_201_CREATED)
async def create_n8n_tool(
    body: N8nToolIn, db: AsyncSession = Depends(get_db), me: User = Depends(require_admin)
) -> N8nToolOut:
    total = (await db.execute(select(func.count()).select_from(N8nTool))).scalar_one()
    if total >= svc.MAX_HERRAMIENTAS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Como mucho {svc.MAX_HERRAMIENTAS} herramientas de n8n. Borra alguna que no uses.",
        )
    datos = await _validar(db, body)
    t = N8nTool(**datos, secreto=svc.generar_secreto(), created_by=me.id)
    db.add(t)
    await db.flush()
    await record_audit(
        db, user_id=me.id, action="n8n_tool.created", entity="n8n_tool", entity_id=t.id,
        after={"nombre": t.nombre, "url": t.url, "accion": t.accion},
    )
    await db.commit()
    await db.refresh(t)
    return _out(t)


@router.put("/tools/{tool_id}", response_model=N8nToolOut)
async def update_n8n_tool(
    tool_id: uuid.UUID,
    body: N8nToolIn,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_admin),
) -> N8nToolOut:
    t = await _la_herramienta(db, tool_id)
    antes = {"nombre": t.nombre, "url": t.url, "accion": t.accion, "activa": t.activa}
    datos = await _validar(db, body, otra_que=t.id)
    for campo, valor in datos.items():
        setattr(t, campo, valor)
    await record_audit(
        db, user_id=me.id, action="n8n_tool.updated", entity="n8n_tool", entity_id=t.id,
        before=antes, after={"nombre": t.nombre, "url": t.url, "accion": t.accion, "activa": t.activa},
    )
    await db.commit()
    await db.refresh(t)
    return _out(t)


@router.delete("/tools/{tool_id}", response_model=OkResponse)
async def delete_n8n_tool(
    tool_id: uuid.UUID, db: AsyncSession = Depends(get_db), me: User = Depends(require_admin)
) -> OkResponse:
    t = await _la_herramienta(db, tool_id)
    await record_audit(
        db, user_id=me.id, action="n8n_tool.deleted", entity="n8n_tool", entity_id=t.id,
        before={"nombre": t.nombre, "url": t.url},
    )
    await db.delete(t)
    await db.commit()
    return OkResponse()


@router.get("/tools/{tool_id}/secreto", response_model=SecretoOut)
async def get_n8n_tool_secret(
    tool_id: uuid.UUID, db: AsyncSession = Depends(get_db), me: User = Depends(require_admin)
) -> SecretoOut:
    """Para el botón «Copiar»: queda apuntado quién lo vio."""
    t = await _la_herramienta(db, tool_id)
    await record_audit(
        db, user_id=me.id, action="n8n_tool.secreto_visto", entity="n8n_tool", entity_id=t.id
    )
    await db.commit()
    return SecretoOut(secreto=t.secreto)


@router.post("/tools/{tool_id}/regenerar-secreto", response_model=N8nToolOut)
async def rotate_n8n_tool_secret(
    tool_id: uuid.UUID, db: AsyncSession = Depends(get_db), me: User = Depends(require_admin)
) -> N8nToolOut:
    """El viejo deja de valer al momento: hay que pegar el nuevo en n8n."""
    t = await _la_herramienta(db, tool_id)
    t.secreto = svc.generar_secreto()
    await record_audit(
        db, user_id=me.id, action="n8n_tool.secreto_regenerado", entity="n8n_tool", entity_id=t.id
    )
    await db.commit()
    await db.refresh(t)
    return _out(t)


@router.post("/tools/{tool_id}/probar", response_model=ProbarOut)
@limiter.limit(limit_spec("20/minute"))
async def try_n8n_tool(
    request: Request,
    tool_id: uuid.UUID,
    body: ProbarIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> ProbarOut:
    t = await _la_herramienta(db, tool_id)
    db.expunge(t)
    hecho = await svc.llamar_a_n8n(t, body.args, {"canal": "prueba"}, prueba=True)
    return ProbarOut(
        ok=bool(hecho.get("ok")),
        estado=hecho.get("estado"),
        ms=int(hecho.get("ms") or 0),
        respuesta=hecho.get("respuesta"),
        error=hecho.get("error"),
    )
