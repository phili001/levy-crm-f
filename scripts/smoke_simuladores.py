#!/usr/bin/env python3
"""Smoke de punta a punta contra los simuladores: mensaje → Sofía → envío.

Recorre el camino entero sin una sola cuenta real:

  1. Conecta WhatsApp (YCloud) apuntando al simulador y deja un LLM de prueba
     como proveedor del agente de texto.
  2. El simulador manda un mensaje del cliente, firmado como YCloud.
  3. Espera a que el agente conteste y comprueba que la respuesta salió por
     el envío de YCloud (lo que capturó el simulador), no solo que se guardó.
  4. Un webhook con la firma mala se rechaza con 401.
  5. Messenger por Zernio: se conecta con la API key (el CRM registra el
     webhook en el simulador), llega un mensaje a la página y Sofía contesta
     por el envío de Zernio.

CAMBIA LA CONFIGURACIÓN de la instalación a la que apunta (canal de WhatsApp y
proveedor del agente). Por eso se niega a correr contra algo que no sea local,
salvo que se fuerce con SMOKE_FORZAR=1. Nunca contra producción.

Solo stdlib: corre en la CI y en cualquier ordenador sin instalar nada.

Variables:
  API            URL del backend vista desde aquí (http://localhost:8000).
  EMAIL / PASS   admin de la instalación.
  PASS_NUEVA     solo si el admin aún tiene que estrenar contraseña (recién sembrado).
  MOCKS          URL base de los simuladores vista desde aquí (http://localhost).
  MOCKS_DESDE_CRM  la misma, vista desde el backend (en el compose: http://mocks).
  API_DESDE_MOCKS  el backend visto desde los simuladores (en el compose: http://app:8000).
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

API = os.environ.get("API", "http://localhost:8000").rstrip("/")
MOCKS = os.environ.get("MOCKS", "http://localhost").rstrip("/")
MOCKS_DESDE_CRM = os.environ.get("MOCKS_DESDE_CRM", MOCKS).rstrip("/")
API_DESDE_MOCKS = os.environ.get("API_DESDE_MOCKS", API).rstrip("/")
WA = f"{MOCKS}:{os.environ.get('MOCK_WA_PORT', '9101')}"
LLM = f"{MOCKS}:{os.environ.get('MOCK_LLM_PORT', '9102')}"
LLM_DESDE_CRM = f"{MOCKS_DESDE_CRM}:{os.environ.get('MOCK_LLM_PORT', '9102')}/v1"
ZERNIO = f"{MOCKS}:{os.environ.get('MOCK_ZERNIO_PORT', '9105')}"
NUMERO_CLINICA = os.environ.get("WA_MOCK_NUMERO", "+529980000000")
NOMBRE_PROVEEDOR = "Simulador (pruebas)"
ESPERA_MAXIMA = int(os.environ.get("SMOKE_ESPERA", "90"))


def fallo(texto: str) -> None:
    print(f"FALLO: {texto}")
    sys.exit(1)


def peticion(metodo: str, url: str, datos: object = None, token: str | None = None):
    cuerpo = json.dumps(datos).encode() if datos is not None else None
    req = urllib.request.Request(url, data=cuerpo, method=metodo)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            texto = r.read().decode()
            return r.status, (json.loads(texto) if texto else None)
    except urllib.error.HTTPError as e:
        texto = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(texto)
        except ValueError:
            return e.code, texto
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        # 0 = no contestó nadie (servicio caído o reiniciándose).
        return 0, str(e)


def exigir(codigo: int, respuesta: object, que: str, esperado: int = 200):
    if codigo != esperado:
        fallo(f"{que}: se esperaba {esperado} y respondió {codigo}: {str(respuesta)[:300]}")
    return respuesta


def es_local(url: str) -> bool:
    host = urllib.parse.urlparse(url).hostname or ""
    return host in ("localhost", "127.0.0.1", "app") or host.endswith(".local")


@dataclass
class Contexto:
    token: str
    webhook: str
    secreto: str


def nuevo_cliente() -> str:
    # Formato actual de celular mexicano (+52 y 10 dígitos). El viejo `+521…`
    # el CRM lo normaliza quitando el 1, y el destinatario ya no coincidiría.
    return "+5299" + str(secrets.randbelow(10**8)).zfill(8)


def iniciar_sesion(email: str, clave: str) -> str:
    codigo, r = peticion("POST", f"{API}/api/v1/auth/login", {"email": email, "password": clave})
    token = exigir(codigo, r, "login")["access_token"]
    if (r.get("user") or {}).get("debe_cambiar_password"):
        # Instalación recién sembrada: el admin inicial tiene que estrenar
        # contraseña antes de poder tocar nada. En la CI se le da una nueva.
        nueva = os.environ.get("PASS_NUEVA")
        if not nueva:
            fallo("el admin tiene que cambiar la contraseña antes; define PASS_NUEVA.")
        codigo, r = peticion(
            "PATCH", f"{API}/api/v1/auth/me",
            {"current_password": clave, "new_password": nueva}, token,
        )
        exigir(codigo, r, "estrenar la contraseña del admin")
        codigo, r = peticion("POST", f"{API}/api/v1/auth/login", {"email": email, "password": nueva})
        token = exigir(codigo, r, "login con la contraseña nueva")["access_token"]
    return token


def preparar() -> Contexto:
    """Simuladores limpios, sesión, LLM de prueba en el agente y WhatsApp al
    simulador. Deja la instalación lista para mandar mensajes."""
    if not es_local(API) and os.environ.get("SMOKE_FORZAR") != "1":
        fallo(
            f"{API} no parece una instalación local. Este smoke reconfigura WhatsApp y el "
            "LLM del agente; si de verdad es un entorno de pruebas, repite con SMOKE_FORZAR=1."
        )
    email, clave = os.environ.get("EMAIL"), os.environ.get("PASS")
    if not email or not clave:
        fallo("define EMAIL y PASS con el admin de la instalación.")

    print("== Simuladores ==")
    for nombre, url in (("YCloud", WA), ("LLM", LLM), ("Zernio", ZERNIO)):
        codigo, r = peticion("GET", f"{url}/_mock/salud")
        exigir(codigo, r, f"simulador de {nombre} en {url} (¿está levantado?)")
    peticion("DELETE", f"{WA}/_mock")
    peticion("DELETE", f"{LLM}/_mock")
    peticion("DELETE", f"{ZERNIO}/_mock")
    print("OK")

    print("== Login ==")
    token = iniciar_sesion(email, clave)
    print("OK")

    print("== Proveedor LLM de prueba ==")
    codigo, proveedores = peticion("GET", f"{API}/api/v1/admin/llm-providers", token=token)
    exigir(codigo, proveedores, "listar proveedores")
    prov = next((p for p in proveedores if p["name"] == NOMBRE_PROVEEDOR), None)
    datos_prov = {"name": NOMBRE_PROVEEDOR, "base_url": LLM_DESDE_CRM, "api_key": "mock-key"}
    if prov:
        codigo, prov = peticion(
            "PUT", f"{API}/api/v1/admin/llm-providers/{prov['id']}", datos_prov, token
        )
    else:
        codigo, prov = peticion("POST", f"{API}/api/v1/admin/llm-providers", datos_prov, token)
    exigir(codigo, prov, "guardar el proveedor de prueba")
    print(f"OK ({LLM_DESDE_CRM})")

    print("== Agente de texto con el LLM de prueba ==")
    codigo, agentes = peticion("GET", f"{API}/api/v1/admin/agents", token=token)
    exigir(codigo, agentes, "listar agentes")
    agente = next((a for a in agentes if a.get("kind") == "text" and a.get("is_active")), None)
    if agente is None:
        fallo("no hay ningún agente de texto activo (¿se sembró la instalación?)")
    campos = (
        "name kind prompt_system model_name temperature max_tokens buffer_seconds "
        "response_split_max_parts context_window handoff_bridge_message monthly_budget_usd "
        "tools_enabled is_active fallback_provider_id fallback_model"
    ).split()
    cuerpo = {k: agente.get(k) for k in campos if k in agente}
    # Buffer corto: el smoke no tiene por qué esperar la ráfaga de 8 s.
    cuerpo.update(llm_provider_id=prov["id"], buffer_seconds=1)
    codigo, r = peticion("PATCH", f"{API}/api/v1/admin/agents/{agente['id']}", cuerpo, token)
    exigir(codigo, r, "asignar el proveedor al agente")
    print(f"OK ({agente['name']})")

    print("== WhatsApp por YCloud (simulado) ==")
    secreto = "whsec_smoke_" + secrets.token_hex(12)
    codigo, r = peticion(
        "POST",
        f"{API}/api/v1/admin/channels/whatsapp/provision",
        {
            "provider": "ycloud",
            "api_key": "mock-key",
            "phone_number": NUMERO_CLINICA,
            "webhook_secret": secreto,
            "agent_id": agente["id"],
        },
        token,
    )
    exigir(codigo, r, "conectar WhatsApp")
    print("OK")
    return Contexto(token, f"{API_DESDE_MOCKS}/api/v1/webhooks/ycloud", secreto)


def mensaje(ctx: Contexto, cliente: str, texto: str, **extra: object) -> dict:
    """El cliente escribe: el simulador lo entrega firmado. Devuelve lo que
    contestó el webhook del CRM (`status_code`, `respuesta`)."""
    codigo, r = peticion(
        "POST",
        f"{WA}/_mock/inbound",
        {"from": cliente, "text": texto, "url": ctx.webhook, "secret": ctx.secreto, **extra},
    )
    return exigir(codigo, r, "simulador de YCloud")


def enviados_a(cliente: str) -> list[dict]:
    _, outbox = peticion("GET", f"{WA}/_mock/outbox")
    return [m for m in outbox or [] if m.get("to") == cliente]


def esperar_envio(cliente: str, contiene: str, espera: int = ESPERA_MAXIMA) -> dict | None:
    """El primer envío a `cliente` cuyo texto contiene `contiene`, o None si no
    sale en `espera` segundos."""
    limite = time.time() + espera
    while time.time() < limite:
        encontrado = next(
            (m for m in enviados_a(cliente) if contiene in (m.get("texto") or "")), None
        )
        if encontrado:
            return encontrado
        time.sleep(2)
    return None


def conectar_zernio(ctx: Contexto) -> None:
    """Instagram y Messenger por Zernio (simulado). Va DESPUÉS de WhatsApp:
    los canales nuevos heredan el agente que ya responde ahí."""
    print("== Instagram y Messenger por Zernio (simulado) ==")
    codigo, r = peticion("POST", f"{API}/api/v1/admin/zernio", {"api_key": "zk-smoke"}, ctx.token)
    exigir(codigo, r, "conectar Zernio")
    if not r.get("webhook_registrado"):
        fallo(f"el CRM no registró el webhook en el simulador de Zernio: {r.get('aviso')}")
    print(f"OK ({', '.join(c['tipo'] for c in r.get('canales', []))})")


def mensaje_social(plataforma: str, participante: str, texto: str, **extra: object) -> dict:
    """Llega un mensaje por Zernio, firmado con el secreto que registró el CRM."""
    codigo, r = peticion(
        "POST",
        f"{ZERNIO}/_mock/entrante",
        {
            "platform": plataforma,
            "participant_id": participante,
            "text": texto,
            "url": f"{API_DESDE_MOCKS}/api/v1/webhooks/zernio",
            **extra,
        },
    )
    return exigir(codigo, r, "simulador de Zernio")


def enviados_zernio(conversation_id: str) -> list[dict]:
    _, enviados = peticion("GET", f"{ZERNIO}/_mock/enviados")
    return [m for m in enviados or [] if m.get("conversation_id") == conversation_id]


def esperar_envio_zernio(conversation_id: str, contiene: str, espera: int = ESPERA_MAXIMA):
    limite = time.time() + espera
    while time.time() < limite:
        encontrado = next(
            (m for m in enviados_zernio(conversation_id) if contiene in (m.get("message") or "")),
            None,
        )
        if encontrado:
            return encontrado
        time.sleep(2)
    return None


def principal() -> None:
    ctx = preparar()
    conectar_zernio(ctx)

    print("== Firma mala → 401 ==")
    r = mensaje(ctx, nuevo_cliente(), "no debería entrar", firma_mala=True)
    if r["status_code"] != 401:
        fallo(f"un webhook con la firma mala respondió {r['status_code']}, no 401")
    print("OK")

    print("== Mensaje del cliente → respuesta de Sofía por WhatsApp ==")
    cliente = nuevo_cliente()
    r = mensaje(ctx, cliente, "Hola, ¿cuánto cuesta una limpieza dental?", name="Paciente Smoke")
    if r["status_code"] != 200:
        fallo(f"el webhook firmado respondió {r['status_code']}: {r['respuesta']}")
    enviado = esperar_envio(cliente, "limpieza")
    if not enviado:
        _, llamadas = peticion("GET", f"{LLM}/_mock/llamadas")
        fallo(
            f"en {ESPERA_MAXIMA} s no salió la respuesta hacia {cliente}. "
            f"Envíos a ese número: {enviados_a(cliente)}. Llamadas al LLM: "
            f"{len(llamadas or [])}. Mira el worker y Logs en vivo."
        )
    print(f"OK: «{enviado['texto']}»")

    print("== Mensaje a la página (Messenger por Zernio) → respuesta de Sofía ==")
    r = mensaje_social("facebook", str(int(time.time() * 1000)), "Hola, ¿cuánto cuesta una limpieza?")
    if r["status_code"] != 200:
        fallo(f"el webhook de Zernio respondió {r['status_code']}: {r['respuesta']}")
    cid = r["evento"]["message"]["conversationId"]
    enviado = esperar_envio_zernio(cid, "limpieza")
    if not enviado:
        fallo(f"en {ESPERA_MAXIMA} s no salió la respuesta por Zernio. Mira el worker y Logs en vivo.")
    if not enviado.get("idempotency_key"):
        fallo("el envío por Zernio salió sin Idempotency-Key")
    print(f"OK: «{enviado['message']}»")

    print("\nSmoke contra simuladores OK ✓")


if __name__ == "__main__":
    principal()
