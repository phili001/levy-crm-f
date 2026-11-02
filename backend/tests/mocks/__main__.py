"""Levanta todos los simuladores en un solo proceso.

    cd backend && python -m tests.mocks

Puertos (cada uno con su variable por si choca con algo):

  MOCK_WA_PORT      9101  YCloud           → YCLOUD_BASE_URL=http://<host>:9101/v2
  MOCK_LLM_PORT     9102  LLM (OpenAI)     → proveedor con base_url http://<host>:9102/v1
  MOCK_RETELL_PORT  9103  Retell           → RETELL_BASE_URL=http://<host>:9103
  MOCK_CORREO_PORT  9104  control del correo (llegan correos, lo enviado)
  MOCK_ZERNIO_PORT  9105  Zernio (IG/FB)   → ZERNIO_BASE_URL=http://<host>:9105/api/v1
  MOCK_N8N_PORT     9106  webhooks de n8n  → herramientas y «Sofía en n8n» (modo `agente`) con url
                          http://<host>:9106/webhook/<nombre> (solo desde pruebas: el CRM exige
                          HTTPS pública)
  MOCK_IMAPS_PORT   9993  IMAP con TLS     · MOCK_IMAP_PORT 9143 IMAP + STARTTLS
  MOCK_SMTPS_PORT   9465  SMTP con TLS     · MOCK_SMTP_PORT 9587 SMTP + STARTTLS

`MOCKS_HOST` decide dónde escuchan (127.0.0.1 por defecto; el compose pone
0.0.0.0 para que los vean los otros contenedores).
"""
from __future__ import annotations

import asyncio
import os

import uvicorn

from tests.mocks import imap_mock, llm_mock, n8n_mock, retell_mock, wa_mock, zernio_mock


def _puerto(nombre: str, por_defecto: int) -> int:
    return int(os.environ.get(nombre) or por_defecto)


async def principal() -> None:
    host = os.environ.get("MOCKS_HOST") or "127.0.0.1"
    cert = imap_mock.generar_certificado()
    correo = imap_mock.EstadoCorreo()

    correo_puertos = (
        _puerto("MOCK_IMAPS_PORT", 9993),
        _puerto("MOCK_IMAP_PORT", 9143),
        _puerto("MOCK_SMTPS_PORT", 9465),
        _puerto("MOCK_SMTP_PORT", 9587),
    )
    await imap_mock.servir_imap(correo, cert, host, *correo_puertos[:2])
    imap_mock.servir_smtp(correo, cert, host, *correo_puertos[2:])

    apps = [
        (wa_mock.crear_app(), _puerto("MOCK_WA_PORT", 9101)),
        (llm_mock.crear_app(), _puerto("MOCK_LLM_PORT", 9102)),
        (retell_mock.crear_app(), _puerto("MOCK_RETELL_PORT", 9103)),
        (imap_mock.crear_app(correo), _puerto("MOCK_CORREO_PORT", 9104)),
        (zernio_mock.crear_app(), _puerto("MOCK_ZERNIO_PORT", 9105)),
        (n8n_mock.crear_app(), _puerto("MOCK_N8N_PORT", 9106)),
    ]
    servidores = [
        uvicorn.Server(uvicorn.Config(app, host=host, port=puerto, log_level="warning"))
        for app, puerto in apps
    ]
    print(
        f"Simuladores en {host}: YCloud :{apps[0][1]}, LLM :{apps[1][1]}, "
        f"Retell :{apps[2][1]}, correo :{apps[3][1]}, Zernio :{apps[4][1]}, n8n :{apps[5][1]} "
        f"(IMAPS/IMAP/SMTPS/SMTP {'/'.join(map(str, correo_puertos))}). "
        f"Certificado del correo: {cert.cert} (paquete para SSL_CERT_FILE: {cert.bundle})",
        flush=True,
    )
    await asyncio.gather(*(s.serve() for s in servidores))


if __name__ == "__main__":
    asyncio.run(principal())
