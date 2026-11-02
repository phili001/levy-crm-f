"""Correo por IMAP: lo que entra, en qué hilo, y que no se pierde ni se duplica.

El servidor IMAP es un doble en memoria (no hay dependencia de red): lo que se
prueba es nuestra lógica de UIDVALIDITY, anclas, reintentos e hilos.
"""
from __future__ import annotations

import asyncio
import uuid
from email.message import EmailMessage

import pytest

from app.providers.email import imap_client
from app.providers.email.imap_client import ConfigImap, ImapError, Servidor
from app.services.imap_ingest import hilo_de, parsear_correo


def _correo(
    *,
    de: str = "paciente@example.com",
    asunto: str = "Cita",
    cuerpo: str = "Hola, quiero una cita",
    message_id: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    cabeceras: dict | None = None,
    adjunto: tuple[str, bytes] | None = None,
) -> bytes:
    m = EmailMessage()
    m["From"] = f"Paciente <{de}>"
    m["To"] = "recepcion@clinica.mx"
    m["Subject"] = asunto
    m["Message-ID"] = message_id or f"<{uuid.uuid4().hex}@example.com>"
    if in_reply_to:
        m["In-Reply-To"] = in_reply_to
    if references:
        m["References"] = references
    for k, v in (cabeceras or {}).items():
        m[k] = v
    m.set_content(cuerpo)
    if adjunto:
        m.add_attachment(adjunto[1], maintype="application", subtype="pdf", filename=adjunto[0])
    return m.as_bytes()


# --- Parseo y conexión (sin base de datos) -------------------------------------


def test_el_hilo_es_la_raiz_de_references_para_toda_la_conversacion():
    raiz = "<raiz@example.com>"
    assert hilo_de("<a@x>", None, None) == "imap:<a@x>"
    assert hilo_de("<b@x>", raiz, None) == f"imap:{raiz}"
    assert hilo_de("<c@x>", "<b@x>", f"{raiz} <b@x>") == f"imap:{raiz}"


def test_el_correo_se_lee_como_si_viniera_de_gmail(tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.UPLOADS_PATH", str(tmp_path))
    p = parsear_correo(
        _correo(
            cabeceras={"List-Id": "<boletin.example.com>", "Precedence": "bulk"},
            adjunto=("receta.pdf", b"%PDF-1.4 datos"),
        )
    )
    assert p["from_addr"] == "paciente@example.com"
    assert p["subject"] == "Cita"
    assert p["body"].startswith("Hola, quiero una cita")
    assert "[adjunto: receta.pdf]" in p["body"]
    assert p["list_id"] == "<boletin.example.com>" and p["precedence"] == "bulk"
    [adj] = p["attachments"]
    assert adj["filename"] == "receta.pdf" and adj["local"] is True
    assert (tmp_path / adj["attachment_id"]).read_bytes() == b"%PDF-1.4 datos"


def test_outlook_y_microsoft_365_no_admiten_contrasena(monkeypatch):
    assert imap_client.es_microsoft("clinica@hotmail.es", mirar_mx=False)
    assert imap_client.es_microsoft("x@outlook.com", mirar_mx=False)
    assert not imap_client.es_microsoft("x@clinica.mx", mirar_mx=False)

    # Dominio propio con el correo en Microsoft 365: se ve en el MX.
    class _Mx:
        exchange = "clinica-mx.mail.protection.outlook.com."

    monkeypatch.setattr("dns.resolver.resolve", lambda *a, **k: [_Mx()])
    assert imap_client.es_microsoft("recepcion@clinica.mx")

    cfg = ConfigImap("recepcion@clinica.mx", "x", Servidor("imap.clinica.mx", 993, "ssl"))
    with pytest.raises(ImapError, match="Microsoft"):
        imap_client.probar(cfg)


def test_los_errores_de_acceso_se_explican():
    t = imap_client.traducir_error(
        Exception("[ALERT] Application-specific password required"), "IMAP"
    )
    assert "contraseña de aplicación" in t
    assert "usuario o contraseña" in imap_client.traducir_error(
        Exception("[AUTHENTICATIONFAILED] Invalid credentials"), "IMAP"
    )


class _ImapFalso:
    """Lo justo de `imaplib.IMAP4` para `traer_nuevos`."""

    def __init__(self, uidvalidity: str, correos: dict[int, bytes]) -> None:
        self.uidvalidity = uidvalidity
        self.correos = correos
        self.busquedas: list[str] = []

    def select(self, carpeta, readonly=False):
        return "OK", [b"1"]

    def response(self, codigo):
        return "OK", [self.uidvalidity.encode()]

    def status(self, carpeta, que):
        return "OK", [f"INBOX (UIDNEXT {max(self.correos) + 1})".encode()]

    def uid(self, comando, *args):
        if comando == "SEARCH":
            criterio = args[1]
            self.busquedas.append(criterio)
            if criterio.startswith("UID "):
                desde = int(criterio.split()[1].split(":")[0])
                uids = [u for u in self.correos if u >= desde] or [max(self.correos)]
            else:
                uids = list(self.correos)
            return "OK", [" ".join(str(u) for u in sorted(uids)).encode()]
        if comando == "FETCH":
            u = int(args[0])
            return "OK", [(f"{u} (BODY[]".encode(), self.correos[u]), b")"]
        raise AssertionError(comando)

    def logout(self):
        return "BYE", []


def _cfg() -> ConfigImap:
    return ConfigImap("recepcion@clinica.mx", "x", Servidor("imap.clinica.mx", 993, "ssl"))


def test_al_conectar_solo_se_ancla_y_no_se_leen_correos_viejos(monkeypatch):
    falso = _ImapFalso("200", {7: b"viejo", 8: b"viejo"})
    monkeypatch.setattr(imap_client, "_conectar", lambda cfg: falso)
    primera = imap_client.traer_nuevos(_cfg(), None, 0)
    assert primera.correos == [] and primera.ultimo_uid == 8
    assert not any(b.startswith("SINCE") for b in falso.busquedas)


def test_si_cambia_uidvalidity_se_resincroniza_por_fecha(monkeypatch):
    falso = _ImapFalso("200", {1: b"a", 2: b"b"})
    monkeypatch.setattr(imap_client, "_conectar", lambda cfg: falso)

    normal = imap_client.traer_nuevos(_cfg(), "200", 1)
    assert [c.uid for c in normal.correos] == [2] and not normal.resincronizado

    # El servidor recreó la carpeta: los UID ya no significan lo mismo.
    cambiado = imap_client.traer_nuevos(_cfg(), "100", 57)
    assert cambiado.resincronizado and cambiado.uidvalidity == "200"
    assert falso.busquedas[-1].startswith("SINCE ")
    assert [c.uid for c in cambiado.correos] == [1, 2]


def test_sin_correos_nuevos_no_se_relee_el_ultimo(monkeypatch):
    falso = _ImapFalso("200", {5: b"a"})
    monkeypatch.setattr(imap_client, "_conectar", lambda cfg: falso)
    # `UID 6:*` devuelve el 5 (así funciona IMAP); no debe tratarse como nuevo.
    assert imap_client.traer_nuevos(_cfg(), "200", 5).correos == []


# --- Con base de datos -------------------------------------------------------------


def _db_available() -> bool:
    try:
        from sqlalchemy import text

        from app.db.session import db_session

        async def _check():
            async with db_session() as db:
                await db.execute(text("select 1"))

        asyncio.run(_check())
        return True
    except Exception:
        return False


con_db = pytest.mark.skipif(
    not _db_available(), reason="DB no disponible (se ejecuta en CI con Postgres)"
)


async def _canal_imap(uidvalidity: str):
    """Un buzón ya anclado (UIDVALIDITY conocido, desde el UID 0): el caso de
    todas las pasadas después de conectar."""
    from app.db.session import db_session
    from app.models.channel import Channel, ChannelType
    from app.services.channel_secrets import apply_channel_values

    async with db_session() as db:
        ch = Channel(type=ChannelType.email, name=f"Correo {uuid.uuid4().hex[:6]}", enabled=True, config={})
        apply_channel_values(
            ch,
            {
                "transport": "imap",
                "usuario": "recepcion@clinica.mx",
                "imap_password": "secreta",
                "imap_host": "imap.clinica.mx",
                "imap_port": 993,
                "imap_seguridad": "ssl",
                "imap_uidvalidity": uidvalidity,
                "imap_last_uid": 0,
            },
        )
        db.add(ch)
        await db.commit()
        await db.refresh(ch)
        return ch


async def _borrar_canal(ch_id):
    from sqlalchemy import text

    from app.db.session import db_session

    async with db_session() as db:
        await db.execute(text("DELETE FROM channels WHERE id = :i"), {"i": str(ch_id)})
        await db.commit()


@con_db
def test_un_correo_que_falla_se_reintenta_y_una_resync_no_duplica(monkeypatch, tmp_path):
    from sqlalchemy import func, select

    from app.db.session import db_session
    from app.models.channel import Channel
    from app.models.message import Message
    from app.services import imap_ingest

    monkeypatch.setattr("app.core.config.settings.UPLOADS_PATH", str(tmp_path))
    encolados: list[str] = []
    monkeypatch.setattr(
        "app.tasks.process_message.process_message.delay", lambda mid: encolados.append(mid)
    )
    ids = [f"<{uuid.uuid4().hex}@example.com>" for _ in range(3)]
    correos = {
        10: _correo(message_id=ids[0], de=f"a{uuid.uuid4().hex[:6]}@example.com"),
        11: b"esto rompe el parser",  # se sustituye abajo
        12: _correo(message_id=ids[2], de=f"c{uuid.uuid4().hex[:6]}@example.com"),
    }
    real_procesar = imap_ingest._procesar
    rompe = {"activo": True}

    async def procesar(correo, cuenta):
        if correo.uid == 11 and rompe["activo"]:
            return "fallo simulado"
        return await real_procesar(correo, cuenta)

    monkeypatch.setattr(imap_ingest, "_procesar", procesar)
    correos[11] = _correo(message_id=ids[1], de=f"b{uuid.uuid4().hex[:6]}@example.com")
    falso = _ImapFalso("300", correos)
    monkeypatch.setattr(imap_client, "_conectar", lambda cfg: falso)

    async def _run():
        ch = await _canal_imap("300")
        try:
            await imap_ingest.sincronizar_canal(ch)
            async with db_session() as db:
                ch = (await db.execute(select(Channel).where(Channel.id == ch.id))).scalar_one()
            # El 11 falló: el ancla se queda en 10 para reintentarlo.
            assert ch.config["imap_last_uid"] == 10
            assert ch.config["imap_fallos"] == {"11": 1}

            rompe["activo"] = False
            await imap_ingest.sincronizar_canal(ch)
            async with db_session() as db:
                ch = (await db.execute(select(Channel).where(Channel.id == ch.id))).scalar_one()
            assert ch.config["imap_last_uid"] == 12 and ch.config["imap_fallos"] == {}

            # El servidor cambia UIDVALIDITY y reenvía los mismos correos:
            # no se duplica ninguno, porque el id es el Message-ID.
            falso.uidvalidity = "999"
            await imap_ingest.sincronizar_canal(ch)
            async with db_session() as db:
                n = (
                    await db.execute(
                        select(func.count()).select_from(Message).where(
                            Message.extra["provider_message_id"].astext.in_(
                                [f"imap:{i}" for i in ids]
                            )
                        )
                    )
                ).scalar_one()
            assert n == 3
            assert len(encolados) == 3
        finally:
            await _borrar_canal(ch.id)

    asyncio.run(_run())


@con_db
def test_la_respuesta_del_cliente_cae_en_la_misma_conversacion(monkeypatch, tmp_path):
    from sqlalchemy import select

    from app.db.session import db_session
    from app.models.message import Message
    from app.services import imap_ingest

    monkeypatch.setattr("app.core.config.settings.UPLOADS_PATH", str(tmp_path))
    monkeypatch.setattr("app.tasks.process_message.process_message.delay", lambda mid: None)
    de = f"hilo{uuid.uuid4().hex[:6]}@example.com"
    raiz = f"<{uuid.uuid4().hex}@example.com>"
    respuesta_crm = f"<{uuid.uuid4().hex}@clinica.mx>"
    falso = _ImapFalso(
        "400",
        {
            1: _correo(de=de, message_id=raiz),
            2: _correo(
                de=de,
                asunto="Re: Cita",
                in_reply_to=respuesta_crm,
                references=f"{raiz} {respuesta_crm}",
            ),
        },
    )
    monkeypatch.setattr(imap_client, "_conectar", lambda cfg: falso)

    async def _run():
        ch = await _canal_imap("400")
        try:
            await imap_ingest.sincronizar_canal(ch)
            async with db_session() as db:
                convs = (
                    await db.execute(
                        select(Message.conversation_id).where(
                            Message.extra["reply_to_addr"].astext == de
                        )
                    )
                ).scalars().all()
            assert len(convs) == 2 and len(set(convs)) == 1
        finally:
            await _borrar_canal(ch.id)

    asyncio.run(_run())
