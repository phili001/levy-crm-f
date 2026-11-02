"""Apariencia de la instalación (marca blanca).

La LECTURA es pública a propósito: la pantalla de login tiene que salir con el
logotipo y el color del cliente antes de que nadie haya entrado. Lo que se
expone es lo mismo que cualquiera vería al abrir el login, nada más.

La ESCRITURA es de admin, y cada cambio deja rastro en la auditoría: cambiar
el nombre o el logotipo de la instalación es lo primero que haría quien quiera
hacerse pasar por el negocio ante su propio equipo.
"""
from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.ratelimit import limit_spec, make_limiter
from app.db.session import get_db
from app.models.user import User
from app.services import apariencia as svc
from app.services.audit import record_audit

router = APIRouter(prefix="/apariencia", tags=["apariencia"])
limiter = make_limiter()


class AparienciaOut(BaseModel):
    nombre: str | None
    paleta: str
    color_marca: str
    tipografia: str
    trazo_iconos: str
    # None = sin logotipo propio; el panel pinta el de fábrica.
    logo_url: str | None
    tipografias: list[str]
    trazos: list[str]


class AparienciaIn(BaseModel):
    nombre: str | None = Field(default=None, max_length=60)
    paleta: str
    color_marca: str
    tipografia: str
    trazo_iconos: str


async def _respuesta(db: AsyncSession) -> AparienciaOut:
    ap = await svc.leer(db)
    huella = await svc.huella_del_logo(db)
    return AparienciaOut(
        **ap.a_dict(),
        # La huella en la URL hace que cambiar el logotipo cambie la URL, y
        # así el navegador no sigue enseñando el viejo desde su caché.
        logo_url=f"/api/v1/apariencia/logo?v={huella}" if huella else None,
        tipografias=svc.TIPOGRAFIAS,
        trazos=svc.TRAZOS,
    )


@router.get("", response_model=AparienciaOut)
async def ver_apariencia(db: AsyncSession = Depends(get_db)) -> AparienciaOut:
    return await _respuesta(db)


@router.put("", response_model=AparienciaOut)
async def guardar_apariencia(
    body: AparienciaIn,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_admin),
) -> AparienciaOut:
    antes = (await svc.leer(db)).a_dict()
    try:
        nueva = svc.validar(body.model_dump())
    except svc.AparienciaInvalida as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await svc.guardar(db, nueva)
    await record_audit(
        db,
        user_id=me.id,
        action="apariencia.updated",
        entity="app_settings",
        before=antes,
        after=nueva.a_dict(),
    )
    await db.commit()
    return await _respuesta(db)


@router.delete("", response_model=AparienciaOut)
async def restablecer_apariencia(
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_admin),
) -> AparienciaOut:
    antes = (await svc.leer(db)).a_dict()
    await svc.restablecer(db)
    await record_audit(
        db,
        user_id=me.id,
        action="apariencia.reset",
        entity="app_settings",
        before=antes,
    )
    await db.commit()
    return await _respuesta(db)


@router.get("/logo", include_in_schema=False)
async def ver_logo(db: AsyncSession = Depends(get_db)) -> Response:
    logo = await svc.leer_logo(db)
    if logo is None:
        raise HTTPException(status_code=404, detail="Sin logotipo")
    contenido, mime, _ = logo
    return Response(
        content=contenido,
        media_type=mime,
        headers={
            # La URL lleva la huella (?v=), así que puede cachearse sin miedo;
            # un logotipo nuevo es una URL nueva.
            "Cache-Control": "public, max-age=86400",
            # Un SVG abierto directamente es un documento del mismo origen que
            # el panel. `sandbox` lo deja sin scripts ni acceso a la sesión
            # aunque se colara uno; a una imagen PNG no le afecta.
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put("/logo", response_model=AparienciaOut)
@limiter.limit(limit_spec("30/hour"))
async def subir_logo(
    request: Request,
    fichero: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_admin),
) -> AparienciaOut:
    # Se lee un byte de más para saber si pasa del límite sin cargar en
    # memoria un fichero de 2 GB que alguien suba por error.
    contenido = await fichero.read(svc.LOGO_MAX_BYTES + 1)
    try:
        huella = await svc.guardar_logo(db, contenido)
    except svc.AparienciaInvalida as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    await record_audit(
        db,
        user_id=me.id,
        action="apariencia.logo_updated",
        entity="app_settings",
        after={"huella": huella, "bytes": len(contenido)},
    )
    await db.commit()
    return await _respuesta(db)


@router.delete("/logo", response_model=AparienciaOut)
async def quitar_logo(
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_admin),
) -> AparienciaOut:
    await svc.borrar_logo(db)
    await record_audit(
        db,
        user_id=me.id,
        action="apariencia.logo_removed",
        entity="app_settings",
    )
    await db.commit()
    return await _respuesta(db)
