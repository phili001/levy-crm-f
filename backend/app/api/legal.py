"""Datos del aviso de privacidad y de los términos (Fase 7, T-7.3.6).

Las páginas públicas `/privacidad` y `/terminos` los necesitan por dos motivos:
Meta (a través de Zernio) pide una URL de privacidad para los canales de
mensajería, y la LFPDPPP obliga a decir quién trata los datos, para qué y cómo
ejercer los derechos ARCO. El texto está en el panel; aquí solo los datos que
cambian de una clínica a otra.

La LECTURA es pública a propósito: son páginas para cualquiera, igual que la
Apariencia. La ESCRITURA es de admin y deja rastro en la auditoría: cambiar a
quién se dirigen las solicitudes de datos es lo primero que haría quien
quisiera quedárselas.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.services import apariencia as apariencia_svc
from app.services.app_settings import get_app_setting, set_app_setting
from app.services.audit import record_audit

router = APIRouter(prefix="/legal", tags=["legal"])

CAMPOS = ("responsable", "domicilio", "correo", "telefono")
_CLAVE = "legal.{}"
_ACTUALIZADO = "legal.actualizado"


class LegalOut(BaseModel):
    # Nombre comercial de la Apariencia, para el encabezado de la página.
    nombre: str | None
    responsable: str
    domicilio: str
    correo: str
    telefono: str
    actualizado: str
    # Faltan responsable, domicilio o correo: la página lo dice en vez de
    # publicar un aviso cojo.
    completo: bool


class LegalIn(BaseModel):
    responsable: str = Field(default="", max_length=200)
    domicilio: str = Field(default="", max_length=300)
    correo: EmailStr | None = None
    telefono: str = Field(default="", max_length=40)


async def _leer(db: AsyncSession) -> LegalOut:
    datos = {c: (await get_app_setting(_CLAVE.format(c), "", db)).strip() for c in CAMPOS}
    marca = await apariencia_svc.leer(db)
    return LegalOut(
        nombre=marca.nombre,
        actualizado=await get_app_setting(_ACTUALIZADO, "", db),
        completo=all(datos[c] for c in ("responsable", "domicilio", "correo")),
        **datos,
    )


@router.get("", response_model=LegalOut)
async def ver_legal(db: AsyncSession = Depends(get_db)) -> LegalOut:
    return await _leer(db)


@router.put("", response_model=LegalOut)
async def guardar_legal(
    body: LegalIn,
    db: AsyncSession = Depends(get_db),
    me: User = Depends(require_admin),
) -> LegalOut:
    antes = (await _leer(db)).model_dump()
    valores = {
        "responsable": body.responsable.strip(),
        "domicilio": body.domicilio.strip(),
        "correo": str(body.correo or "").strip(),
        "telefono": body.telefono.strip(),
    }
    for campo, valor in valores.items():
        await set_app_setting(_CLAVE.format(campo), valor, db)
    await set_app_setting(_ACTUALIZADO, datetime.now(timezone.utc).date().isoformat(), db)
    await record_audit(
        db,
        user_id=me.id,
        action="legal.updated",
        entity="app_settings",
        before={c: antes[c] for c in CAMPOS},
        after=valores,
    )
    await db.commit()
    return await _leer(db)
