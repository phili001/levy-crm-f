#!/usr/bin/env python3
"""Pruebas de fallos F-1 contra los simuladores: que nada falle en silencio.

Tres averías reales, provocadas a propósito sobre una instalación local con los
simuladores (mismo arranque que `smoke_simuladores.py`):

  1. La clave del LLM deja de valer a media conversación → la conversación se
     deriva a humano, el cliente recibe el mensaje puente y NO sale ninguna
     respuesta inventada. Arreglada la clave, un cliente nuevo se atiende solo.
  2. Redis se reinicia → en cuanto vuelve, los mensajes se contestan sin tocar
     nada (broker, buffer y cerrojos viven ahí).
  3. El worker se cae → el mensaje se guarda y espera; no sale nada mientras
     no hay worker, y al volver lo contesta solo.
  4. Zernio se cae (500 al enviar) → la conversación de Messenger pasa a
     humano y no se da nada por enviado. Arreglado, un cliente nuevo se
     atiende solo.

Cómo reiniciar Redis o parar el worker depende de dónde corre cada cosa, así
que se pasa como comandos de shell:

  F1_REINICIAR_REDIS   p. ej. `docker restart omnicanal-test-redis`
  F1_PARAR_WORKER      p. ej. `pkill -f "[c]elery -A app.tasks worker"` (el
                       corchete evita que pkill encaje con su propio shell)
  F1_ARRANCAR_WORKER   p. ej. `nohup celery -A app.tasks worker --pool=solo >w.log 2>&1 &`

El escenario cuyo comando falte se salta avisando (no se da por bueno). El
resto de variables son las de `smoke_simuladores.py`. Igual que aquel, se
niega a correr contra una instalación que no sea local.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smoke_simuladores import (  # noqa: E402
    API,
    LLM,
    Contexto,
    ZERNIO,
    conectar_zernio,
    enviados_a,
    enviados_zernio,
    esperar_envio,
    esperar_envio_zernio,
    fallo,
    mensaje,
    mensaje_social,
    nuevo_cliente,
    peticion,
    preparar,
)

PUENTE = "equipo"  # el mensaje puente de la derivación habla del equipo
ESPERA_SIN_WORKER = 15


def comando(nombre: str) -> str | None:
    valor = (os.environ.get(nombre) or "").strip()
    return valor or None


def ejecutar(nombre: str) -> None:
    orden = comando(nombre)
    print(f"   $ {orden}")
    subprocess.run(orden, shell=True, check=True)


def conversacion(ctx: Contexto, cliente: str) -> dict | None:
    codigo, r = peticion("GET", f"{API}/api/v1/conversations?canal=whatsapp", token=ctx.token)
    if codigo != 200:
        return None
    return next((c for c in r["items"] if c["contact"]["telefono"] == cliente), None)


def esperar_api_sana(espera: int = 90) -> None:
    limite = time.time() + espera
    while time.time() < limite:
        codigo, r = peticion("GET", f"{API}/health")
        if codigo == 200 and r.get("db") == "ok" and r.get("redis") == "ok":
            return
        time.sleep(2)
    fallo(f"la API no volvió a estar sana en {espera} s")


def entregar(ctx: Contexto, cliente: str, texto: str) -> None:
    """Manda el mensaje; si el webhook no contesta 200 (Redis aún arrancando),
    reintenta como haría YCloud, que reentrega los webhooks fallidos."""
    for _ in range(10):
        r = mensaje(ctx, cliente, texto, event_id=f"evt.f1.{cliente}.{abs(hash(texto))}")
        if r["status_code"] == 200:
            return
        time.sleep(3)
    fallo(f"el webhook no aceptó el mensaje tras 10 intentos: {r}")


def llm_caido(ctx: Contexto) -> None:
    print("== F-1 · la clave del LLM deja de valer a media conversación ==")
    cliente = nuevo_cliente()
    entregar(ctx, cliente, "Hola, primera pregunta")
    if not esperar_envio(cliente, "primera"):
        fallo("la primera respuesta (con la clave buena) no salió")
    peticion("PUT", f"{LLM}/_mock/fallos", {"chat": 401})
    try:
        entregar(ctx, cliente, "¿Y la ortodoncia cuánto cuesta?")
        if not esperar_envio(cliente, PUENTE):
            fallo(f"no llegó el mensaje puente de la derivación: {enviados_a(cliente)}")
        inventadas = [m for m in enviados_a(cliente) if "ortodoncia" in (m.get("texto") or "")]
        if inventadas:
            fallo(f"con el LLM caído salió una respuesta como si nada: {inventadas}")
        conv = conversacion(ctx, cliente)
        if not conv or conv["status"] != "humano":
            fallo(f"la conversación no pasó a humano: {conv and conv['status']}")
    finally:
        peticion("PUT", f"{LLM}/_mock/fallos", {"chat": None})
    otro = nuevo_cliente()
    entregar(ctx, otro, "Hola, ya con la clave arreglada")
    if not esperar_envio(otro, "arreglada"):
        fallo("arreglada la clave, un cliente nuevo no recibió respuesta")
    print("OK: deriva a humano, manda el puente, no inventa y se recupera solo")


def redis_reiniciado(ctx: Contexto) -> None:
    print("== F-1 · Redis se reinicia ==")
    if not comando("F1_REINICIAR_REDIS"):
        print("SALTADO: falta F1_REINICIAR_REDIS (no se da por probado)")
        return
    ejecutar("F1_REINICIAR_REDIS")
    esperar_api_sana()
    cliente = nuevo_cliente()
    entregar(ctx, cliente, "Hola, después del reinicio de Redis")
    if not esperar_envio(cliente, "reinicio"):
        fallo("tras reiniciar Redis el mensaje no se contestó solo")
    print("OK: se contesta sin tocar nada")


def worker_caido(ctx: Contexto) -> None:
    print("== F-1 · el worker se cae ==")
    if not (comando("F1_PARAR_WORKER") and comando("F1_ARRANCAR_WORKER")):
        print("SALTADO: faltan F1_PARAR_WORKER y F1_ARRANCAR_WORKER (no se da por probado)")
        return
    ejecutar("F1_PARAR_WORKER")
    time.sleep(3)
    cliente = nuevo_cliente()
    try:
        entregar(ctx, cliente, "Hola, ¿hay alguien sin worker?")
        time.sleep(ESPERA_SIN_WORKER)
        if enviados_a(cliente):
            fallo(f"sin worker salió algo, ¿quién lo mandó?: {enviados_a(cliente)}")
        conv = conversacion(ctx, cliente)
        if conv is None:
            fallo("sin worker el mensaje ni siquiera se guardó")
    finally:
        ejecutar("F1_ARRANCAR_WORKER")
    if not esperar_envio(cliente, "worker"):
        fallo("al volver el worker, el mensaje que esperaba no se contestó")
    print("OK: el mensaje espera guardado y se contesta al volver el worker")


def zernio_caido(ctx: Contexto) -> None:
    print("== F-1 · Zernio se cae al enviar ==")
    conectar_zernio(ctx)
    participante = f"f1{int(time.time() * 1000)}"
    peticion("POST", f"{ZERNIO}/_mock/fallo", {"status": 500})
    try:
        r = mensaje_social("facebook", participante, "Hola, ¿hay cita el sábado?")
        if r["status_code"] != 200:
            fallo(f"el webhook de Zernio respondió {r['status_code']}")
        limite = time.time() + 60
        conv = None
        while time.time() < limite:
            codigo, lista = peticion(
                "GET", f"{API}/api/v1/conversations?canal=messenger", token=ctx.token
            )
            if codigo == 200:
                conv = next(
                    (c for c in lista["items"] if c["contact"]["telefono"] == f"fb:{participante}"),
                    None,
                )
                if conv and conv["status"] == "humano":
                    break
            time.sleep(2)
        if not conv or conv["status"] != "humano":
            fallo(f"con Zernio caído la conversación no pasó a humano: {conv and conv['status']}")
        if enviados_zernio(r["evento"]["message"]["conversationId"]):
            fallo("con Zernio caído algo se dio por enviado")
    finally:
        # Reiniciar el simulador quita el fallo (y el webhook registrado).
        peticion("DELETE", f"{ZERNIO}/_mock")
    # El simulador se reinició: se vuelve a conectar (registra otro webhook).
    conectar_zernio(ctx)
    r = mensaje_social("facebook", f"ok{int(time.time() * 1000)}", "Hola, ya con Zernio de vuelta")
    if not esperar_envio_zernio(r["evento"]["message"]["conversationId"], "vuelta"):
        fallo("con Zernio de vuelta, un cliente nuevo no recibió respuesta")
    print("OK: deriva a humano, no da nada por enviado y se recupera solo")


def principal() -> None:
    ctx = preparar()
    llm_caido(ctx)
    zernio_caido(ctx)
    redis_reiniciado(ctx)
    worker_caido(ctx)
    print("\nFallos F-1 contra simuladores OK ✓")


if __name__ == "__main__":
    principal()
