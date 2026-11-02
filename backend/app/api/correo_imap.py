"""Conectar un buzón de correo por IMAP/SMTP desde el panel (Fase 5).

Hay UN canal de correo por instalación. Conectar un buzón lo pasa a
`transport=imap`; conectar Gmail por OAuth lo devuelve a `transport=gmail`.

Probar antes de guardar no es opcional: guardar un buzón que no entra dejaría
un canal "conectado" en el que nunca entra nada, que es el fallo silencioso que
más horas ha costado en este proyecto.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.channel import Channel, ChannelType
from app.models.user import User
from app.providers.email import imap_client
from app.providers.email.imap_client import AVISO_MICROSOFT, ConfigImap, ImapError, Servidor
from app.services.audit import record_audit
from app.services.channel_secrets import apply_channel_values, channel_config
from app.services.correo_smtp import EnvioSmtpError, probar_smtp

router = APIRouter(prefix="/admin/channels/email/imap", tags=["admin"])


class ServidorIn(BaseModel):
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(..., ge=1, le=65535)
    seguridad: str = Field(..., pattern="^(ssl|starttls)$")


class BuzonIn(BaseModel):
    preset: str = Field(..., max_length=20)  # clave de PRESETS o "otro"
    usuario: str = Field(..., min_length=3, max_length=255)
    # Vacía al editar = se queda la guardada.
    password: str | None = Field(default=None, max_length=500)
    # Solo con preset "otro"; con un preset se usan sus servidores.
    imap: ServidorIn | None = None
    smtp: ServidorIn | None = None
    carpeta: str = Field(default="INBOX", max_length=200)


class ConectarIn(BuzonIn):
    auto_send: bool = False
    no_responder: list[str] = Field(default_factory=list, max_length=200)


class PresetOut(BaseModel):
    id: str
    nombre: str
    imap: ServidorIn
    smtp: ServidorIn
    ayuda: str


class ProbarOut(BaseModel):
    imap_ok: bool
    smtp_ok: bool
    errores: list[str]
    carpeta_enviados: str | None = None


@router.get("/presets", response_model=list[PresetOut])
async def presets(_: User = Depends(require_admin)) -> list[PresetOut]:
    return [
        PresetOut(
            id=k,
            nombre=p.nombre,
            imap=ServidorIn(host=p.imap.host, port=p.imap.port, seguridad=p.imap.seguridad),
            smtp=ServidorIn(host=p.smtp.host, port=p.smtp.port, seguridad=p.smtp.seguridad),
            ayuda=p.ayuda,
        )
        for k, p in imap_client.PRESETS.items()
    ]


async def _canal_correo(db: AsyncSession) -> Channel | None:
    return (
        await db.execute(
            select(Channel)
            .where(Channel.type == ChannelType.email)
            .order_by(Channel.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _servidores(body: BuzonIn) -> tuple[Servidor, Servidor]:
    if body.preset == "otro":
        if not (body.imap and body.smtp):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Con «Otro» hay que indicar el servidor IMAP y el SMTP.",
            )
        return (
            Servidor(body.imap.host.strip(), body.imap.port, body.imap.seguridad),
            Servidor(body.smtp.host.strip(), body.smtp.port, body.smtp.seguridad),
        )
    imap = imap_client.servidor_de_preset(body.preset, "imap", body.usuario)
    smtp = imap_client.servidor_de_preset(body.preset, "smtp", body.usuario)
    if not imap or not smtp:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Proveedor no válido.")
    return imap, smtp


async def _password(body: BuzonIn, db: AsyncSession) -> str:
    """La contraseña escrita o, al editar sin reescribirla, la guardada.

    La guardada SOLO se reutiliza contra el mismo usuario y los mismos
    servidores: si no, bastaría cambiar el servidor por uno propio y darle a
    Probar para recibir en él la contraseña del buzón, que el panel no enseña
    a nadie.
    """
    if body.password:
        return body.password
    falta = HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Escribe la contraseña de aplicación.")
    ch = await _canal_correo(db)
    if ch is None:
        raise falta
    cfg = channel_config(ch)
    imap, smtp = _servidores(body)
    mismo = (
        cfg.get("usuario") == body.usuario.strip()
        and (cfg.get("imap_host"), cfg.get("imap_port")) == (imap.host, imap.port)
        and (cfg.get("smtp_host"), cfg.get("smtp_port")) == (smtp.host, smtp.port)
    )
    guardada = str(cfg.get("imap_password") or "")
    if not (mismo and guardada):
        raise falta
    return guardada


async def _probar(body: BuzonIn, password: str) -> ProbarOut:
    usuario = body.usuario.strip()
    # Microsoft primero: ahí no merece la pena ni intentar la conexión.
    if await asyncio.to_thread(imap_client.es_microsoft, usuario):
        return ProbarOut(imap_ok=False, smtp_ok=False, errores=[AVISO_MICROSOFT])
    imap, smtp = _servidores(body)
    cfg = ConfigImap(usuario=usuario, password=password, servidor=imap, carpeta=body.carpeta or "INBOX")
    errores: list[str] = []
    imap_ok = smtp_ok = False
    enviados = None
    try:
        await asyncio.to_thread(imap_client.probar, cfg)
        imap_ok = True
        enviados = await asyncio.to_thread(imap_client.detectar_enviados, cfg)
    except ImapError as exc:
        errores.append(str(exc))
    except Exception as exc:  # noqa: BLE001 — detectar_enviados es opcional
        if not imap_ok:
            errores.append(imap_client.traducir_error(exc, "IMAP"))
    try:
        await asyncio.to_thread(probar_smtp, smtp, usuario, password)
        smtp_ok = True
    except EnvioSmtpError as exc:
        errores.append(str(exc))
    return ProbarOut(imap_ok=imap_ok, smtp_ok=smtp_ok, errores=errores, carpeta_enviados=enviados)


@router.post("/probar", response_model=ProbarOut)
async def probar(
    body: BuzonIn,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_admin),
) -> ProbarOut:
    return await _probar(body, await _password(body, db))


class ConectarOut(BaseModel):
    channel_id: str
    usuario: str
    carpeta_enviados: str | None


@router.post("", response_model=ConectarOut)
async def conectar(
    body: ConectarIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
) -> ConectarOut:
    password = await _password(body, db)
    prueba = await _probar(body, password)
    if not (prueba.imap_ok and prueba.smtp_ok):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, " · ".join(prueba.errores) or "No conecta.")
    imap, smtp = _servidores(body)
    ch = await _canal_correo(db)
    cambio_de_buzon = True
    if ch is None:
        ch = Channel(type=ChannelType.email, name=body.usuario.strip(), enabled=True, config={})
        db.add(ch)
    else:
        anterior = dict(ch.config or {})
        cambio_de_buzon = not (
            anterior.get("transport") == "imap" and anterior.get("usuario") == body.usuario.strip()
        )
        ch.name = body.usuario.strip()
        ch.enabled = True
    valores = {
        "transport": "imap",
        "preset": body.preset,
        "usuario": body.usuario.strip(),
        "imap_password": password,
        "smtp_password": password,
        "imap_host": imap.host,
        "imap_port": imap.port,
        "imap_seguridad": imap.seguridad,
        "smtp_host": smtp.host,
        "smtp_port": smtp.port,
        "smtp_seguridad": smtp.seguridad,
        "carpeta": body.carpeta or "INBOX",
        "carpeta_enviados": prueba.carpeta_enviados,
        "auto_send": body.auto_send,
        "no_responder": [e.strip() for e in body.no_responder if e.strip()],
    }
    apply_channel_values(ch, valores)
    # `apply_channel_values` no pisa con None: lo que hay que VACIAR va directo.
    cfg = dict(ch.config or {})
    cfg["imap_error"] = None
    if cambio_de_buzon:
        # Buzón nuevo: se empieza de cero (lee el último día y se ancla).
        cfg.update({"imap_uidvalidity": None, "imap_last_uid": 0, "imap_fallos": {}})
    ch.config = cfg
    await db.flush()
    await record_audit(
        db,
        user_id=user.id,
        action="email.imap.connected",
        entity="channel",
        entity_id=ch.id,
        after={
            "usuario": body.usuario.strip(),
            "preset": body.preset,
            "auto_send": body.auto_send,
        },
    )
    await db.commit()
    return ConectarOut(
        channel_id=str(ch.id), usuario=body.usuario.strip(), carpeta_enviados=prueba.carpeta_enviados
    )
