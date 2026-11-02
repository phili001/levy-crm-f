"""Canales → Plantillas: crear plantillas de WhatsApp desde el panel (SPEC-18).

Solo administradores. El listado sigue en `GET /admin/whatsapp/templates`
(admin.py), que ya es en vivo contra el proveedor; aquí está lo nuevo: las
sugeridas, «Generar con IA» y «Enviar a aprobación».

Ojo: este router NO puede llevar `from __future__ import annotations`. Con
`@limiter.limit` los tipos de los parámetros quedan como texto y FastAPI no
sabe leer el cuerpo de la petición (pasó con `apariencia.py`).
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.ratelimit import limit_spec, make_limiter
from app.db.session import get_db
from app.models.user import User
from app.services import plantillas_whatsapp as svc
from app.services.audit import record_audit
from app.services.plantillas_biblioteca import SUGERIDAS, Sugerida

router = APIRouter(prefix="/admin/whatsapp/plantillas", tags=["plantillas"])
limiter = make_limiter()


def _campos_permitidos() -> frozenset[str]:
    # Los mismos que deja enlazar la configuración de variables de Campañas.
    from app.api.admin import ALLOWED_CONTACT_FIELDS

    return ALLOWED_CONTACT_FIELDS


class PlantillasInfo(BaseModel):
    proveedor: str
    # Solo con YCloud se crean desde aquí; con Meta se listan y nada más.
    puede_crear: bool
    idiomas: list[str]
    campos: list[str]
    sugeridas: list[Sugerida]


class GenerarIn(BaseModel):
    descripcion: str = Field(min_length=10, max_length=500)
    categoria: str | None = None
    idioma: str = "es_MX"


class GenerarOut(BaseModel):
    plantilla: svc.PlantillaIn
    # Lo que falta para poder mandarla, en palabras. El editor lo enseña.
    avisos: list[str]


class CreadaOut(BaseModel):
    nombre: str
    idioma: str
    estado: str


@router.get("", response_model=PlantillasInfo)
async def plantillas_info(_: User = Depends(require_admin)) -> PlantillasInfo:
    from app.providers.whatsapp.selector import PROVIDER_YCLOUD, get_provider_name

    proveedor = await get_provider_name()
    return PlantillasInfo(
        proveedor=proveedor,
        puede_crear=proveedor == PROVIDER_YCLOUD,
        idiomas=list(svc.IDIOMAS),
        campos=sorted(_campos_permitidos()),
        sugeridas=SUGERIDAS,
    )


@router.post("/generar", response_model=GenerarOut)
@limiter.limit(limit_spec("10/minute"))
async def generar_plantilla(
    request: Request,
    body: GenerarIn,
    _: User = Depends(require_admin),
) -> GenerarOut:
    if body.idioma not in svc.IDIOMAS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Idioma no admitido: {body.idioma}.")
    try:
        plantilla = await svc.generar_con_ia(
            body.descripcion, categoria=body.categoria, idioma=body.idioma
        )
    except svc.GeneracionFallida as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return GenerarOut(
        plantilla=plantilla,
        avisos=svc.problemas(plantilla, campos_permitidos=_campos_permitidos()),
    )


@router.post("", response_model=CreadaOut, status_code=status.HTTP_201_CREATED)
@limiter.limit(limit_spec("20/minute"))
async def crear_plantilla(
    request: Request,
    body: svc.PlantillaIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> CreadaOut:
    """Manda la plantilla a revisión de Meta por YCloud.

    Antes de llamar a nadie se comprueba lo que Meta rechaza siempre: un
    rechazo tarda hasta 24 h en llegar y obliga a cambiar de nombre. Las
    etiquetas de las variables se guardan en `whatsapp_template_var`, que es de
    donde las lee quien la manda después (chat, ficha, Campañas).
    """
    from app.models.whatsapp_template_var import WhatsappTemplateVar
    from app.providers.whatsapp.base import TemplateCreateError, WhatsAppNotConfiguredError
    from app.providers.whatsapp.selector import (
        PROVIDER_LABELS,
        PROVIDER_YCLOUD,
        get_provider_name,
        provider_by_name,
    )

    fallos = svc.problemas(body, campos_permitidos=_campos_permitidos())
    if fallos:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " ".join(fallos))
    proveedor = await get_provider_name()
    if proveedor != PROVIDER_YCLOUD:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Tu WhatsApp va por {PROVIDER_LABELS.get(proveedor, proveedor)}: las plantillas "
            "se crean en el Administrador de WhatsApp de Meta y aquí solo se listan.",
        )
    ycloud = provider_by_name(PROVIDER_YCLOUD)
    try:
        creada = await ycloud.create_template(svc.a_ycloud(body))  # type: ignore[attr-defined]
    except WhatsAppNotConfiguredError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except TemplateCreateError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"YCloud no la aceptó: {exc}"
        ) from exc

    # Reemplaza, no acumula: si existía una configuración vieja con este nombre
    # e idioma (una plantilla borrada en YCloud y vuelta a crear), manda la nueva.
    await db.execute(
        delete(WhatsappTemplateVar).where(
            WhatsappTemplateVar.template_name == body.nombre,
            WhatsappTemplateVar.language == body.idioma,
        )
    )
    for v in body.variables:
        db.add(
            WhatsappTemplateVar(
                template_name=body.nombre,
                language=body.idioma,
                idx=v.n,
                nombre=v.etiqueta[:60],
                contact_field=v.campo,
            )
        )
    await record_audit(
        db,
        user_id=user.id,
        action="whatsapp_template.created",
        entity="template",
        entity_id=None,
        after={"nombre": body.nombre, "idioma": body.idioma, "categoria": body.categoria},
    )
    await db.commit()
    return CreadaOut(
        nombre=str(creada.get("name") or body.nombre),
        idioma=body.idioma,
        estado=str(creada.get("status") or "PENDING"),
    )
