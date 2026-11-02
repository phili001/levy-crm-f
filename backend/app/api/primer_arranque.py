"""Primer arranque: el dueño crea el administrador desde el panel.

Es la instalación en un clic en Coolify: con `SETUP_CODE` puesto y sin
administrador en las variables, la instalación arranca sin usuarios y el panel
pide crear el primero. Lo que protege esa ventana es el código: lo genera
Coolify (variable mágica SERVICE_PASSWORD_ARRANQUE) y solo lo ve quien entra
al panel de Coolify. Sin él, quien llegue antes que el dueño al dominio no se
puede quedar con el CRM.

El alta es una sola vez: con un administrador activo, el endpoint ya no hace
nada. La respuesta trae la llave de cifrado para que el dueño la guarde. Es la
única vez que el CRM la enseña, y sin ella lo cifrado no se recupera nunca.

Sin `from __future__ import annotations`: rompe los routers con
`@limiter.limit` (slowapi resuelve las anotaciones al decorar).
"""
import hmac
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import ADMIN_DE_EJEMPLO, settings
from app.core.logging import get_logger
from app.core.ratelimit import client_ip_key, make_limiter
from app.core.security import create_access_token, hash_password, password_policy_error
from app.db.session import get_db
from app.models.user import User, UserRole
from app.schemas.auth import UserOut
from app.services.audit import record_audit

router = APIRouter(prefix="/primer-arranque", tags=["primer-arranque"])
limiter = make_limiter()
logger = get_logger(__name__)

# Dos altas a la vez (dos pestañas, un doble clic) no pueden dejar dos
# administradores: la comprobación y el alta van bajo este candado de Postgres.
_CANDADO_ALTA = 7_071_003

DONDE_ESTA_EL_CODIGO = (
    "Está en Coolify: abre el service del CRM, pestaña Environment Variables, "
    "y copia el valor de SERVICE_PASSWORD_ARRANQUE."
)


class EstadoOut(BaseModel):
    necesita_admin: bool


class AltaIn(BaseModel):
    codigo: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)
    nombre: str | None = Field(default=None, max_length=120)


class AltaOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
    llave_cifrado: str


async def _hay_admin(db: AsyncSession) -> bool:
    total = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.role == UserRole.admin, User.activo.is_(True))
    )
    return bool(total)


@router.get("", response_model=EstadoOut)
async def estado(db: AsyncSession = Depends(get_db)) -> EstadoOut:
    """Público: el login lo consulta para mandar a crear el administrador.

    Solo dice si falta. Ni el código ni nada de la instalación sale de aquí.
    """
    return EstadoOut(necesita_admin=bool(settings.SETUP_CODE) and not await _hay_admin(db))


@router.post("", response_model=AltaOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute;20/hour")
async def crear_administrador(
    request: Request,
    payload: AltaIn,
    db: AsyncSession = Depends(get_db),
) -> AltaOut:
    if not settings.SETUP_CODE:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Esta instalación no se configura con código de arranque."
        )
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CANDADO_ALTA})
    if await _hay_admin(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Esta instalación ya tiene administrador: entra con tu cuenta.",
        )
    ip = client_ip_key(request)
    if not hmac.compare_digest(payload.codigo.strip().encode(), settings.SETUP_CODE.encode()):
        logger.warning("primer_arranque.codigo_incorrecto", ip=ip)
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Ese no es el código de arranque. {DONDE_ESTA_EL_CODIGO}",
        )

    email = payload.email.strip().lower()
    nombre = (payload.nombre or "").strip() or None
    if email in ADMIN_DE_EJEMPLO:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Usa tu correo de verdad: con él recuperas la contraseña si la olvidas.",
        )
    motivo = password_policy_error(payload.password, email=email, nombre=nombre)
    if motivo:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, motivo)
    if await db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Ese correo ya tiene cuenta en este CRM: usa otro."
        )

    admin = User(
        email=email,
        password_hash=hash_password(payload.password),
        role=UserRole.admin,
        nombre=nombre or "Administrador",
        # La contraseña la acaba de escribir el dueño en el panel: no ha
        # viajado por ninguna variable, así que no hay que obligarle a cambiarla.
        debe_cambiar_password=False,
        # Por lo mismo, el paso «Cambia tu contraseña» del checklist de Inicio
        # (que mira este campo) nace hecho. El token de abajo se firma después,
        # en el mismo segundo o más tarde, así que no lo invalida (api/deps.py).
        password_changed_at=datetime.now(timezone.utc),
    )
    db.add(admin)
    await db.flush()
    await record_audit(
        db,
        user_id=admin.id,
        action="primer_arranque.admin_creado",
        entity="user",
        entity_id=admin.id,
        ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()
    await db.refresh(admin)
    logger.info("primer_arranque.admin_creado")
    token = create_access_token(subject=str(admin.id), extra={"role": admin.role.value})
    return AltaOut(
        access_token=token,
        user=UserOut.model_validate(admin),
        llave_cifrado=settings.ENCRYPTION_KEY,
    )
