"""Simulador de un buzón de correo: servidor IMAP mínimo + SMTP que captura.

Cubre justo lo que usa el canal de correo (`app/providers/email/imap_client.py`
y `app/services/correo_smtp.py`): entrar con usuario y contraseña, abrir la
carpeta en solo lectura, leer `UIDVALIDITY` y `UIDNEXT`, buscar por UID o por
fecha, traer un correo sin marcarlo leído, listar carpetas (para encontrar
Enviados) y copiar ahí lo que sale por SMTP.

TLS de verdad, no un atajo: el CRM se conecta siempre con TLS verificado
(`ssl.create_default_context()`), y un modo "sin cifrar" solo para pruebas sería
una puerta que alguien acabaría usando en producción. Así que el simulador
genera su propio certificado (válido para `localhost`, `127.0.0.1` e
`imap-mock`) y quien lo use tiene que confiar en él:
  - en las pruebas, añadiéndolo al contexto TLS (ver `confiar_en()`);
  - en el perfil `dev` del compose, con `SSL_CERT_FILE` apuntando al paquete
    `bundle.pem` que deja escrito (las CA de siempre + la del simulador).

Puertos (configurables): IMAPS 9993, IMAP+STARTTLS 9143, SMTPS 9465 y
SMTP+STARTTLS 9587. Una contraseña que termina en `-invalid` no entra.

Rutas de control (HTTP, junto al resto de simuladores):
  - `POST /_mock/correo`        llega un correo al buzón (INBOX).
  - `GET  /_mock/enviados`      lo que el CRM mandó por SMTP.
  - `GET  /_mock/carpeta/{n}`   lo que hay en una carpeta (p. ej. `Sent`).
  - `PUT  /_mock/uidvalidity`   cambia UIDVALIDITY (buzón migrado).
  - `DELETE /_mock`             vacía el buzón.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import email
import email.policy
import ipaddress
import logging
import os
import re
import ssl
import tempfile
import threading
import warnings
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from tests.mocks._comun import SUFIJO_CLAVE_INVALIDA

NOMBRES_DEL_CERTIFICADO = ("localhost", "imap-mock", "mocks")
IPS_DEL_CERTIFICADO = ("127.0.0.1",)
CARPETA_ENVIADOS = "Sent"


# --------------------------------------------------------------------- estado
@dataclass
class Correo:
    uid: int
    crudo: bytes
    fecha: dt.datetime
    flags: tuple[str, ...] = ()


@dataclass
class Carpeta:
    nombre: str
    atributos: tuple[str, ...] = ()
    correos: list[Correo] = field(default_factory=list)
    siguiente_uid: int = 1

    def guardar(self, crudo: bytes, fecha: dt.datetime | None = None, flags=()) -> Correo:
        correo = Correo(self.siguiente_uid, crudo, fecha or dt.datetime.now(dt.UTC), flags)
        self.correos.append(correo)
        self.siguiente_uid += 1
        return correo


@dataclass
class EstadoCorreo:
    uidvalidity: int = 1
    carpetas: dict[str, Carpeta] = field(default_factory=dict)
    enviados_smtp: list[dict] = field(default_factory=list)
    candado: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if not self.carpetas:
            self.carpetas = {
                "INBOX": Carpeta("INBOX"),
                CARPETA_ENVIADOS: Carpeta(CARPETA_ENVIADOS, ("\\Sent",)),
            }

    def carpeta(self, nombre: str) -> Carpeta | None:
        nombre = nombre.strip('"')
        if nombre.upper() == "INBOX":
            return self.carpetas["INBOX"]
        return self.carpetas.get(nombre)


# ----------------------------------------------------------------- certificado
@dataclass(frozen=True)
class Certificado:
    cert: str  # ruta del PEM del certificado (es su propia CA)
    clave: str
    bundle: str  # CA de siempre + la del simulador, para SSL_CERT_FILE


def generar_certificado(directorio: str | None = None) -> Certificado:
    """Certificado autofirmado que sirve de CA de sí mismo. Si ya existe en el
    directorio, se reutiliza: así el paquete que lee el backend no cambia en
    cada arranque del simulador."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directorio = directorio or os.environ.get("IMAP_MOCK_CERT_DIR") or tempfile.mkdtemp(
        prefix="imap-mock-"
    )
    os.makedirs(directorio, exist_ok=True)
    ruta_cert = os.path.join(directorio, "ca.pem")
    ruta_clave = os.path.join(directorio, "clave.pem")
    ruta_bundle = os.path.join(directorio, "bundle.pem")
    if not (os.path.exists(ruta_cert) and os.path.exists(ruta_clave)):
        clave = ec.generate_private_key(ec.SECP256R1())
        nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Simulador de correo")])
        ahora = dt.datetime.now(dt.UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(nombre)
            .issuer_name(nombre)
            .public_key(clave.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(ahora - dt.timedelta(days=1))
            .not_valid_after(ahora + dt.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.DNSName(n) for n in NOMBRES_DEL_CERTIFICADO]
                    + [x509.IPAddress(ipaddress.ip_address(i)) for i in IPS_DEL_CERTIFICADO]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(clave, hashes.SHA256())
        )
        with open(ruta_clave, "wb") as f:
            f.write(
                clave.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
        with open(ruta_cert, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(ruta_bundle, "wb") as salida:
        try:
            import certifi

            with open(certifi.where(), "rb") as f:
                salida.write(f.read())
        except ImportError:
            pass
        with open(ruta_cert, "rb") as f:
            salida.write(b"\n" + f.read())
    return Certificado(ruta_cert, ruta_clave, ruta_bundle)


def contexto_servidor(cert: Certificado) -> ssl.SSLContext:
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert.cert, cert.clave)
    return ctx


def confiar_en(cert: Certificado):
    """Parche para las pruebas: `ssl.create_default_context()` confía además en
    el certificado del simulador. Se usa como `monkeypatch.setattr(ssl,
    "create_default_context", confiar_en(cert))`."""
    original = ssl.create_default_context

    def crear(*args, **kwargs):
        ctx = original(*args, **kwargs)
        ctx.load_verify_locations(cert.cert)
        return ctx

    return crear


# ------------------------------------------------------------------- IMAP
_ATOMO = re.compile(r'"((?:[^"\\]|\\.)*)"|(\{\d+\})|(\([^)]*\))|(\S+)')


def _argumentos(texto: str) -> list[str]:
    """Separa los argumentos de un comando IMAP respetando las comillas y los
    paréntesis (lo justo para lo que manda `imaplib`)."""
    salida = []
    for m in _ATOMO.finditer(texto):
        if m.group(1) is not None:
            salida.append(re.sub(r"\\(.)", r"\1", m.group(1)))
        else:
            salida.append(m.group(2) or m.group(3) or m.group(4))
    return salida


def _rango(expresion: str, maximo: int) -> tuple[int, int]:
    a, _, b = expresion.partition(":")
    inicio = maximo if a == "*" else int(a)
    fin = inicio if not b else (maximo if b == "*" else int(b))
    return min(inicio, fin), max(inicio, fin)


class SesionImap:
    def __init__(
        self,
        estado: EstadoCorreo,
        lector: asyncio.StreamReader,
        escritor: asyncio.StreamWriter,
        tls: ssl.SSLContext | None,
    ) -> None:
        self.estado = estado
        self.lector = lector
        self.escritor = escritor
        # Contexto para STARTTLS; None cuando la conexión ya es TLS (IMAPS).
        self.tls_pendiente = tls
        self.dentro = False
        self.carpeta: Carpeta | None = None

    async def enviar(self, linea: str | bytes) -> None:
        self.escritor.write(linea if isinstance(linea, bytes) else linea.encode() + b"\r\n")
        await self.escritor.drain()

    def capacidades(self) -> str:
        extra = " STARTTLS LOGINDISABLED" if self.tls_pendiente else " AUTH=PLAIN"
        return "IMAP4rev1 UIDPLUS" + extra

    async def atender(self) -> None:
        await self.enviar(f"* OK [CAPABILITY {self.capacidades()}] Simulador de correo listo")
        try:
            while True:
                linea = await self.lector.readline()
                if not linea:
                    return
                texto = linea.decode(errors="replace").rstrip("\r\n")
                etiqueta, _, resto = texto.partition(" ")
                comando, _, args = resto.partition(" ")
                seguir = await self.comando(etiqueta, comando.upper(), args)
                if not seguir:
                    return
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError):
            return
        finally:
            self.escritor.close()

    async def comando(self, tag: str, cmd: str, args: str) -> bool:
        if cmd == "CAPABILITY":
            await self.enviar(f"* CAPABILITY {self.capacidades()}")
            await self.enviar(f"{tag} OK CAPABILITY completed")
        elif cmd == "NOOP":
            await self.enviar(f"{tag} OK NOOP completed")
        elif cmd == "LOGOUT":
            await self.enviar("* BYE Hasta luego")
            await self.enviar(f"{tag} OK LOGOUT completed")
            return False
        elif cmd == "STARTTLS" and self.tls_pendiente:
            await self.enviar(f"{tag} OK Begin TLS negotiation now")
            await self.escritor.start_tls(self.tls_pendiente)
            self.tls_pendiente = None
        elif cmd == "LOGIN":
            if self.tls_pendiente:
                await self.enviar(f"{tag} NO [PRIVACYREQUIRED] Usa STARTTLS antes de entrar")
                return True
            usuario, clave = (_argumentos(args) + ["", ""])[:2]
            if not usuario or clave.endswith(SUFIJO_CLAVE_INVALIDA):
                await self.enviar(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials (Failure)")
            else:
                self.dentro = True
                await self.enviar(f"{tag} OK [CAPABILITY {self.capacidades()}] Logged in")
        elif not self.dentro:
            await self.enviar(f"{tag} BAD Primero LOGIN")
        elif cmd in ("SELECT", "EXAMINE"):
            await self.seleccionar(tag, cmd, args)
        elif cmd == "STATUS":
            await self.status(tag, args)
        elif cmd == "LIST":
            for c in self.estado.carpetas.values():
                attrs = " ".join(("\\HasNoChildren",) + c.atributos)
                await self.enviar(f'* LIST ({attrs}) "/" "{c.nombre}"')
            await self.enviar(f"{tag} OK LIST completed")
        elif cmd == "APPEND":
            await self.append(tag, args)
        elif cmd == "UID":
            sub, _, resto = args.partition(" ")
            if sub.upper() == "SEARCH":
                await self.buscar(tag, resto)
            elif sub.upper() == "FETCH":
                await self.traer(tag, resto)
            else:
                await self.enviar(f"{tag} BAD UID {sub} no soportado por el simulador")
        else:
            await self.enviar(f"{tag} BAD Comando no soportado por el simulador: {cmd}")
        return True

    async def seleccionar(self, tag: str, cmd: str, args: str) -> None:
        nombre = (_argumentos(args) or ["INBOX"])[0]
        carpeta = self.estado.carpeta(nombre)
        if carpeta is None:
            await self.enviar(f"{tag} NO [NONEXISTENT] No existe la carpeta")
            return
        self.carpeta = carpeta
        await self.enviar("* FLAGS (\\Answered \\Flagged \\Deleted \\Seen \\Draft)")
        await self.enviar(f"* {len(carpeta.correos)} EXISTS")
        await self.enviar("* 0 RECENT")
        await self.enviar(f"* OK [UIDVALIDITY {self.estado.uidvalidity}] UIDs valid")
        await self.enviar(f"* OK [UIDNEXT {carpeta.siguiente_uid}] Predicted next UID")
        modo = "READ-ONLY" if cmd == "EXAMINE" else "READ-WRITE"
        await self.enviar(f"{tag} OK [{modo}] {cmd} completed")

    async def status(self, tag: str, args: str) -> None:
        partes = _argumentos(args)
        carpeta = self.estado.carpeta(partes[0]) if partes else None
        if carpeta is None:
            await self.enviar(f"{tag} NO [NONEXISTENT] No existe la carpeta")
            return
        pedidos = (partes[1] if len(partes) > 1 else "").strip("()").upper().split()
        valores = {
            "UIDNEXT": carpeta.siguiente_uid,
            "UIDVALIDITY": self.estado.uidvalidity,
            "MESSAGES": len(carpeta.correos),
            "UNSEEN": 0,
        }
        datos = " ".join(f"{k} {valores[k]}" for k in pedidos if k in valores)
        await self.enviar(f'* STATUS "{carpeta.nombre}" ({datos})')
        await self.enviar(f"{tag} OK STATUS completed")

    async def buscar(self, tag: str, criterio: str) -> None:
        if self.carpeta is None:
            await self.enviar(f"{tag} BAD Primero SELECT")
            return
        correos = self.carpeta.correos
        partes = criterio.split()
        if partes and partes[0].upper() == "CHARSET":
            partes = partes[2:]
        clave = partes[0].upper() if partes else "ALL"
        if clave == "UID" and len(partes) > 1:
            maximo = correos[-1].uid if correos else 0
            a, b = _rango(partes[1], maximo)
            uids = [c.uid for c in correos if a <= c.uid <= b]
            # Como un servidor real: `N:*` con N por encima del último devuelve
            # el último igualmente. El cliente lo filtra (`u > ultimo_uid`).
            if not uids and correos and partes[1].endswith(":*"):
                uids = [correos[-1].uid]
        elif clave == "SINCE" and len(partes) > 1:
            desde = dt.datetime.strptime(partes[1], "%d-%b-%Y").date()
            uids = [c.uid for c in correos if c.fecha.date() >= desde]
        else:
            uids = [c.uid for c in correos]
        await self.enviar("* SEARCH" + "".join(f" {u}" for u in uids))
        await self.enviar(f"{tag} OK SEARCH completed")

    async def traer(self, tag: str, resto: str) -> None:
        if self.carpeta is None:
            await self.enviar(f"{tag} BAD Primero SELECT")
            return
        conjunto, _, _items = resto.partition(" ")
        maximo = self.carpeta.correos[-1].uid if self.carpeta.correos else 0
        a, b = _rango(conjunto, maximo)
        for secuencia, c in enumerate(self.carpeta.correos, start=1):
            if a <= c.uid <= b:
                # BODY.PEEK[] no marca como leído: el simulador no toca flags.
                await self.enviar(
                    f"* {secuencia} FETCH (UID {c.uid} BODY[] {{{len(c.crudo)}}}\r\n".encode()
                    + c.crudo
                    + b")\r\n"
                )
        await self.enviar(f"{tag} OK FETCH completed")

    async def append(self, tag: str, args: str) -> None:
        m = re.search(r"\{(\d+)\}$", args)
        if not m:
            await self.enviar(f"{tag} BAD APPEND sin literal")
            return
        carpeta = self.estado.carpeta((_argumentos(args) or [""])[0])
        await self.enviar("+ Ready for literal data")
        crudo = await self.lector.readexactly(int(m.group(1)))
        await self.lector.readline()  # CRLF que cierra el comando
        if carpeta is None:
            await self.enviar(f"{tag} NO [TRYCREATE] No existe la carpeta")
            return
        with self.estado.candado:
            correo = carpeta.guardar(crudo, flags=("\\Seen",))
        await self.enviar(
            f"{tag} OK [APPENDUID {self.estado.uidvalidity} {correo.uid}] APPEND completed"
        )


async def servir_imap(
    estado: EstadoCorreo, cert: Certificado, host: str, puerto_ssl: int, puerto_starttls: int
) -> list[asyncio.base_events.Server]:
    ctx = contexto_servidor(cert)

    async def con_ssl(lector, escritor):
        await SesionImap(estado, lector, escritor, None).atender()

    async def con_starttls(lector, escritor):
        await SesionImap(estado, lector, escritor, ctx).atender()

    return [
        await asyncio.start_server(con_ssl, host, puerto_ssl, ssl=ctx),
        await asyncio.start_server(con_starttls, host, puerto_starttls),
    ]


# ------------------------------------------------------------------- SMTP
def servir_smtp(estado: EstadoCorreo, cert: Certificado, host: str, puerto_ssl: int, puerto_starttls: int):
    """Dos SMTP de aiosmtpd (TLS implícito y STARTTLS) que exigen entrar con
    usuario y contraseña y guardan lo recibido en `estado.enviados_smtp`."""
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import AuthResult

    class Captura:
        async def handle_DATA(self, server, session, envelope):
            crudo = envelope.original_content or envelope.content
            mensaje = email.message_from_bytes(crudo, policy=email.policy.default)
            cuerpo = mensaje.get_body(preferencelist=("plain", "html"))
            with estado.candado:
                estado.enviados_smtp.append(
                    {
                        "n": len(estado.enviados_smtp) + 1,
                        "usuario": _usuario(session),
                        "mail_from": envelope.mail_from,
                        "rcpt_tos": list(envelope.rcpt_tos),
                        "subject": str(mensaje.get("Subject") or ""),
                        "message_id": str(mensaje.get("Message-ID") or ""),
                        "in_reply_to": str(mensaje.get("In-Reply-To") or ""),
                        "references": str(mensaje.get("References") or ""),
                        "texto": cuerpo.get_content() if cuerpo is not None else "",
                        "crudo": crudo.decode(errors="replace"),
                    }
                )
            return "250 Message accepted"

    def _usuario(session) -> str:
        datos = getattr(session, "auth_data", None)
        login = getattr(datos, "login", None)
        return login.decode(errors="replace") if isinstance(login, bytes) else str(login or "")

    def autenticar(server, session, envelope, mecanismo, datos):
        clave = (datos.password or b"").decode(errors="replace")
        return AuthResult(
            success=not clave.endswith(SUFIJO_CLAVE_INVALIDA), handled=False, auth_data=datos
        )

    comun = {
        "handler": Captura(),
        "hostname": host,
        "authenticator": autenticar,
        "auth_required": True,
        "server_hostname": "localhost",
    }
    # Con TLS implícito aiosmtpd no sabe que la conexión ya va cifrada y, si se
    # le pide TLS para AUTH, no lo anuncia nunca. Aquí la conexión es TLS desde
    # el primer byte, así que no exigirlo no abre nada.
    # El aviso de aiosmtpd salta al crear cada sesión (en el hilo del servidor),
    # no al crear el Controller: se filtra por su texto para todo el proceso.
    warnings.filterwarnings("ignore", message="Requiring AUTH while not requiring TLS")
    logging.getLogger("mail.log").setLevel(logging.ERROR)
    implicito = Controller(
        port=puerto_ssl, ssl_context=contexto_servidor(cert), auth_require_tls=False, **comun
    )
    explicito = Controller(
        port=puerto_starttls,
        tls_context=contexto_servidor(cert),
        require_starttls=True,
        auth_require_tls=True,
        **comun,
    )
    implicito.start()
    explicito.start()
    return [implicito, explicito]


# ---------------------------------------------------------------- control HTTP
class NuevoCorreo(BaseModel):
    from_: str = Field(default="Paciente <paciente@example.com>", alias="from")
    to: str = "clinica@example.com"
    subject: str = "Quiero una cita"
    text: str = "Hola, ¿tienen hueco esta semana para una limpieza?"
    message_id: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    carpeta: str = "INBOX"

    model_config = {"populate_by_name": True, "extra": "forbid"}


class Uidvalidity(BaseModel):
    valor: int


def crear_correo(datos: NuevoCorreo) -> tuple[bytes, str]:
    m = EmailMessage()
    m["From"] = datos.from_
    m["To"] = datos.to
    m["Subject"] = datos.subject
    m["Date"] = format_datetime(dt.datetime.now(dt.UTC))
    message_id = datos.message_id or make_msgid(domain="example.com")
    m["Message-ID"] = message_id
    if datos.in_reply_to:
        m["In-Reply-To"] = datos.in_reply_to
    if datos.references:
        m["References"] = datos.references
    m.set_content(datos.text)
    return m.as_bytes(policy=email.policy.SMTP), message_id


def crear_app(estado: EstadoCorreo) -> FastAPI:
    app = FastAPI(title="Simulador de correo (control)")

    @app.post("/_mock/correo")
    async def llega_correo(datos: NuevoCorreo) -> dict:
        carpeta = estado.carpeta(datos.carpeta)
        if carpeta is None:
            raise HTTPException(404, "No existe la carpeta")
        crudo, message_id = crear_correo(datos)
        with estado.candado:
            correo = carpeta.guardar(crudo)
        return {"uid": correo.uid, "message_id": message_id}

    @app.get("/_mock/enviados")
    async def enviados() -> list[dict]:
        return estado.enviados_smtp

    @app.get("/_mock/carpeta/{nombre}")
    async def carpeta(nombre: str) -> list[dict]:
        c = estado.carpeta(nombre)
        if c is None:
            raise HTTPException(404, "No existe la carpeta")
        return [
            {
                "uid": x.uid,
                "flags": list(x.flags),
                "subject": str(email.message_from_bytes(x.crudo).get("Subject") or ""),
                "message_id": str(email.message_from_bytes(x.crudo).get("Message-ID") or ""),
            }
            for x in c.correos
        ]

    @app.put("/_mock/uidvalidity")
    async def cambiar_uidvalidity(datos: Uidvalidity) -> dict:
        estado.uidvalidity = datos.valor
        return {"uidvalidity": estado.uidvalidity}

    @app.delete("/_mock")
    async def reiniciar() -> dict:
        with estado.candado:
            nuevo = EstadoCorreo()
            estado.carpetas = nuevo.carpetas
            estado.enviados_smtp.clear()
            estado.uidvalidity = 1
        return {"ok": True}

    @app.get("/_mock/salud")
    async def salud() -> dict:
        return {"ok": True, "proveedor": "correo"}

    return app
