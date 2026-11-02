"""Asistente → Seguimientos: los avisos que Sofía manda sola (SPEC-18).

Solo administradores. La configuración vive en `app_settings["seguimientos"]`;
la vuelta que manda los avisos está en `services/seguimientos.py` (beat, cada
10 min). Aquí también se dice, al momento, si con la plantilla elegida va a
salir algo o por qué no: un recordatorio encendido que no sale nunca y nadie
sabe por qué es peor que uno apagado.
"""
import json
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.user import User
from app.services import seguimientos as svc
from app.services.audit import record_audit

router = APIRouter(prefix="/admin/seguimientos", tags=["seguimientos"])


class DatoOut(BaseModel):
    clave: str
    etiqueta: str


class PlantillaAviso(BaseModel):
    name: str
    language: str
    status: str | None
    category: str | None
    body: str | None
    variable_names: list[str]
    # Por qué no sirve para un aviso automático, o None si sirve.
    problema: str | None
    # De dónde sale cada variable según sus etiquetas («Fecha de la cita» →
    # `cita_fecha`). El panel lo propone al elegirla; se puede cambiar.
    sugerencia: dict[str, str]


class SeguimientosOut(BaseModel):
    config: svc.SeguimientosConfig
    datos: list[DatoOut]
    plantillas: list[PlantillaAviso]
    # Si no se pudieron leer las plantillas de la cuenta (WhatsApp sin conectar).
    error_plantillas: str | None
    # Por qué cada aviso encendido no va a salir ahora mismo, o None.
    estado: dict[str, str | None]
    zona: str
    negocio_sugerido: str


class RegistroOut(BaseModel):
    tipo: str
    contact_id: str
    contacto: str | None
    cuando: str
    canal: str | None
    motivo: str | None


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def dato_por_etiqueta(etiqueta: str) -> str | None:
    """«Fecha de la cita» → `cita_fecha`. Por palabras: lo que escribe la IA o
    una persona varía («Nombre de la clínica», «Hora de su cita»)."""
    e = _plano(etiqueta)
    if "hora" in e:
        return "cita_hora"
    if "fecha" in e or " dia" in f" {e}":
        return "cita_fecha"
    if "servicio" in e or "tratamiento" in e:
        return "cita_servicio"
    if "negocio" in e or "clinica" in e or "consultorio" in e or "empresa" in e:
        return "negocio"
    if "nombre" in e or "paciente" in e or "cliente" in e:
        return "paciente"
    return None


async def _plantillas(db: AsyncSession) -> tuple[list[tuple[PlantillaAviso, object]], str | None]:
    """Las plantillas de la cuenta, cada una con la de `_template_to_out` al lado."""
    from app.api.admin import _template_to_out
    from app.models.whatsapp_template_var import WhatsappTemplateVar
    from app.providers.whatsapp import get_whatsapp_provider

    try:
        crudas = await get_whatsapp_provider().list_templates()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — sin WhatsApp, la pantalla lo dice y sigue
        return [], str(exc)
    etiquetas: dict[tuple[str, str], dict[str, str]] = {}
    for v in (await db.execute(select(WhatsappTemplateVar))).scalars():
        etiquetas.setdefault((v.template_name, v.language), {})[str(v.idx)] = v.nombre or ""
    salida = []
    for t in map(_template_to_out, crudas):
        propias = etiquetas.get((t.name, t.language), {})
        sugerencia = {}
        for n in t.variable_names:
            dato = dato_por_etiqueta(propias.get(n, ""))
            if dato:
                sugerencia[n] = dato
        prueba = svc.AvisoConfig(plantilla=t.name, idioma=t.language, variables=dict.fromkeys(t.variable_names, "x"))
        problema = svc.problema_de_plantilla(t, prueba, svc.SeguimientosConfig(negocio="x"))
        salida.append((
            PlantillaAviso(
                name=t.name,
                language=t.language,
                status=t.status,
                category=t.category,
                body=t.body,
                variable_names=t.variable_names,
                problema=problema,
                sugerencia=sugerencia,
            ),
            t,
        ))
    return salida, None


async def _salida(db: AsyncSession) -> SeguimientosOut:
    from app.agents.tools.schedule_config import get_schedule_config
    from app.services import apariencia

    cfg = await svc.leer_config()
    pares, error = await _plantillas(db)
    estado: dict[str, str | None] = {}
    for tipo in (svc.RECORDATORIO, svc.INASISTENCIA):
        aviso: svc.AvisoConfig = getattr(cfg, tipo)
        if not aviso.activo:
            estado[tipo] = None
            continue
        if error:
            estado[tipo] = f"no se pudieron leer tus plantillas: {error}"
            continue
        elegida = next(
            (t for p, t in pares if p.name == aviso.plantilla and p.language == aviso.idioma), None
        )
        estado[tipo] = svc.problema_de_plantilla(elegida, aviso, cfg)
    try:
        nombre_panel = (await apariencia.leer(db)).nombre or ""
    except Exception:  # noqa: BLE001
        nombre_panel = ""
    return SeguimientosOut(
        config=cfg,
        datos=[DatoOut(clave=k, etiqueta=v) for k, v in svc.DATOS.items()],
        plantillas=[p for p, _ in pares],
        error_plantillas=error,
        estado=estado,
        zona=(await get_schedule_config()).tz_name,
        negocio_sugerido=nombre_panel,
    )


@router.get("", response_model=SeguimientosOut)
async def ver_seguimientos(
    db: AsyncSession = Depends(get_db), _: User = Depends(require_admin)
) -> SeguimientosOut:
    return await _salida(db)


@router.put("", response_model=SeguimientosOut)
async def guardar_seguimientos(
    body: svc.SeguimientosConfig,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> SeguimientosOut:
    from app.services.app_settings import set_app_setting

    fallos = svc.problemas_de_config(body)
    if fallos:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " ".join(fallos))
    antes = await svc.leer_config()
    await set_app_setting(svc.SETTING_SEGUIMIENTOS, json.dumps(body.model_dump()), db)
    await record_audit(
        db,
        user_id=user.id,
        action="seguimientos.updated",
        entity="app_setting",
        entity_id=None,
        before={"recordatorio": antes.recordatorio.activo, "inasistencia": antes.inasistencia.activo},
        after={"recordatorio": body.recordatorio.activo, "inasistencia": body.inasistencia.activo},
    )
    await db.commit()
    return await _salida(db)


@router.get("/registro", response_model=list[RegistroOut])
async def registro_de_seguimientos(_: User = Depends(require_admin)) -> list[RegistroOut]:
    return [
        RegistroOut(
            tipo=r["tipo"],
            contact_id=r["contact_id"],
            contacto=r["contacto"],
            cuando=r["cuando"].isoformat() if r["cuando"] else "",
            canal=r["canal"],
            motivo=r["motivo"],
        )
        for r in await svc.registro()
    ]
