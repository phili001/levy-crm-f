"""Ayudas para las pruebas de las herramientas de n8n (Fase 11, SPEC-06).

- `herramienta(...)`: una herramienta en memoria (sin guardar) para probar la
  llamada sin base de datos.
- `n8n_falso(monkeypatch, ...)`: el webhook de n8n simulado con
  `httpx.MockTransport`; apunta cada petición que le llega.
- `url_publica(monkeypatch)`: los dominios de ejemplo (`n8n.ejemplo.mx`) no
  resuelven, y la guardia SSRF trata «no resuelve» como interno. Esto la deja
  aceptar cualquier https que no sea una IP o un nombre local.
"""
from __future__ import annotations

import json
import uuid

import httpx

URL = "https://n8n.ejemplo.mx/webhook/pedidos"
# Una imagen JPEG mínima (cabecera de verdad) para lo que «baja» el CRM.
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
SECRETO = "secreto-de-prueba-de-n8n-0123456789"


def herramienta(**campos):
    from app.models.n8n_tool import N8nTool

    datos = {
        "id": uuid.uuid4(),
        "nombre": "buscar_pedido",
        "descripcion": "Busca un pedido por su número y devuelve en qué estado está.",
        "parametros": [
            {"clave": "numero", "tipo": "texto", "descripcion": "Número del pedido", "obligatorio": True},
        ],
        "url": URL,
        "secreto": SECRETO,
        "accion": "lectura",
        "espera_s": 8,
        "datos_contacto": False,
        "activa": True,
    }
    datos.update(campos)
    return N8nTool(**datos)


class N8nFalso:
    def __init__(self, respuestas):
        # Una respuesta por petición, en orden; la última se repite. Cada una
        # es (estado, cuerpo) o una excepción para lanzar.
        self.respuestas = list(respuestas)
        self.peticiones: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.peticiones.append(request)
        r = self.respuestas.pop(0) if len(self.respuestas) > 1 else self.respuestas[0]
        if isinstance(r, Exception):
            raise r
        if isinstance(r, httpx.Response):
            return r
        estado, cuerpo = r
        if isinstance(cuerpo, dict | list):
            return httpx.Response(estado, json=cuerpo)
        return httpx.Response(estado, content=str(cuerpo).encode("utf-8"))

    def cuerpo(self, i: int = -1) -> dict:
        return json.loads(self.peticiones[i].content)


def n8n_falso(monkeypatch, *respuestas) -> N8nFalso:
    from app.services import n8n_tools

    falso = N8nFalso(respuestas or [(200, {"resultado": "ok"})])
    original = httpx.AsyncClient

    def _cliente(*args, **kwargs):
        # Solo los clientes sin transporte propio (los de n8n_tools): el de las
        # pruebas de la API trae el suyo (ASGI) y no se toca.
        kwargs.setdefault("transport", httpx.MockTransport(falso))
        return original(*args, **kwargs)

    monkeypatch.setattr(n8n_tools.httpx, "AsyncClient", _cliente)
    return falso


def url_publica(monkeypatch) -> None:
    from urllib.parse import urlparse

    from app.services import n8n_tools

    async def _validar(url: str) -> tuple[bool, str]:
        p = urlparse(url or "")
        if p.scheme != "https":
            return False, "La URL debe empezar por https://"
        host = p.hostname or ""
        if host in ("localhost",) or host.replace(".", "").isdigit():
            return False, "El host apunta a una IP interna/privada (no permitido)"
        return True, ""

    from app.services import archivos_salientes

    monkeypatch.setattr(n8n_tools, "validate_public_https_url", _validar)
    monkeypatch.setattr(archivos_salientes, "validate_public_https_url", _validar)


async def admin() -> tuple[dict[str, str], uuid.UUID]:
    from app.core.security import create_access_token, hash_password
    from app.db.session import db_session
    from app.models.user import User

    async with db_session() as db:
        u = User(
            email=f"n8n-{uuid.uuid4().hex[:8]}@ejemplo.com",
            password_hash=hash_password("UnaClaveLargaDeVerdad2026"),
            nombre="Admin n8n",
            role="admin",
        )
        db.add(u)
        await db.commit()
        await db.refresh(u)
    token = create_access_token(subject=str(u.id), extra={"role": "admin"})
    return {"Authorization": f"Bearer {token}"}, u.id


async def limpiar(admin_id: uuid.UUID | None = None, *nombres: str) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.audit_log import AuditLog
    from app.models.n8n_tool import N8nTool
    from app.models.user import User

    async with db_session() as db:
        if nombres:
            await db.execute(delete(N8nTool).where(N8nTool.nombre.in_(nombres)))
        if admin_id:
            await db.execute(delete(AuditLog).where(AuditLog.user_id == admin_id))
            await db.execute(delete(User).where(User.id == admin_id))
        await db.commit()


def cliente():
    from app.main import app

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def db_disponible() -> bool:
    import asyncio

    from sqlalchemy import text

    try:
        async def _check():
            from app.db.session import db_session

            async with db_session() as db:
                await db.execute(text("SELECT 1"))

        asyncio.run(_check())
        return True
    except Exception:
        return False


def archivo(contenido: bytes = JPEG, tipo: str = "image/jpeg") -> httpx.Response:
    """Lo que contesta el servidor de archivos cuando el CRM baja uno."""
    return httpx.Response(200, content=contenido, headers={"content-type": tipo})


async def conversacion(canal: str = "web", *, con_mensaje: bool = True, **campos):
    """Una ficha y una conversación en `bot`, con un mensaje reciente del
    paciente (abre las ventanas de WhatsApp y Meta); devuelve (conv_id, ficha_id)."""
    import uuid as _uuid

    from app.db.session import db_session
    from app.models.contact import Contact, ContactOrigen
    from app.models.conversation import Conversation, ConversationCanal, ConversationStatus
    from tests._fichas import telefono_mx

    async with db_session() as db:
        ficha = Contact(telefono=telefono_mx(), origen=ContactOrigen("whatsapp"))
        db.add(ficha)
        await db.flush()
        conv = Conversation(
            contact_id=ficha.id,
            canal=ConversationCanal(canal),
            session_id=f"{canal}-{_uuid.uuid4().hex}",
            status=ConversationStatus.bot,
            **campos,
        )
        db.add(conv)
        await db.flush()
        if con_mensaje:
            from app.models.message import Message, MessageRole

            db.add(Message(conversation_id=conv.id, rol=MessageRole.user, contenido="¿Me mandas una foto?"))
        await db.commit()
        return conv.id, ficha.id


async def borrar_conversacion(conv_id, ficha_id) -> None:
    from sqlalchemy import delete

    from app.db.session import db_session
    from app.models.contact import Contact
    from app.models.conversation import Conversation

    async with db_session() as db:
        await db.execute(delete(Conversation).where(Conversation.id == conv_id))
        await db.execute(delete(Contact).where(Contact.id == ficha_id))
        await db.commit()


async def mensajes_de(conv_id) -> list:
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message

    async with db_session() as db:
        return list(
            (
                await db.execute(
                    select(Message).where(Message.conversation_id == conv_id).order_by(Message.created_at)
                )
            ).scalars().all()
        )
