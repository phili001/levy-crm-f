#!/usr/bin/env python3
"""Compuerta de voz V-3: ¿Sofía contesta lo bastante rápido por teléfono?

Lee de la API de Retell la latencia de cada llamada de la prueba y da el
veredicto. La latencia que cuenta es `latency.e2e`: desde que quien llama
termina de hablar hasta que el agente empieza a sonar, en CADA cambio de turno
(la documentación de Retell la define así). Se juntan todos los turnos de
todas las llamadas y se calcula el p50 y el p90 del conjunto.

Pasa si p50 ≤ 1500 ms y p90 ≤ 2500 ms. Lo que cuenta es el
número de TURNOS, no de llamadas: con menos de 60 el p90 lo deciden dos o tres
turnos y el veredicto es provisional. Por eso la prueba son 8 llamadas largas
(unos 64 turnos) en vez de 20 cortas; lo decidió Carlos el 2026-09-23.

Uso (la API key se teclea sin eco, o va en RETELL_API_KEY; nunca en el chat):

    python3 scripts/medir_v3.py --agente agent_xxx --desde "2026-09-24 10:00"
    python3 scripts/medir_v3.py --llamadas call_a,call_b,call_c

Imprime una tabla en Markdown para pegarla en docs/pruebas/V-3.md.
Solo stdlib.
"""
from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

BASE = os.environ.get("RETELL_BASE_URL", "https://api.retellai.com").rstrip("/")
UMBRAL_P50_MS = 1500
UMBRAL_P90_MS = 2500
TURNOS_MINIMOS = 60
# Cada llamada arranca en frío (conexión y primer turno, el más lento): con muy
# pocas llamadas ese caso casi no entra en la muestra.
LLAMADAS_MINIMAS = 8


def percentil(valores: list[float], p: float) -> float:
    """Percentil por interpolación lineal (el mismo criterio que numpy)."""
    if not valores:
        return math.nan
    orden = sorted(valores)
    k = (len(orden) - 1) * p / 100
    bajo, alto = math.floor(k), math.ceil(k)
    if bajo == alto:
        return float(orden[int(k)])
    return orden[bajo] + (orden[alto] - orden[bajo]) * (k - bajo)


def pedir(metodo: str, ruta: str, clave: str, cuerpo: dict | None = None) -> object:
    req = urllib.request.Request(
        f"{BASE}{ruta}",
        data=json.dumps(cuerpo).encode() if cuerpo is not None else None,
        method=metodo,
        headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        detalle = e.read().decode(errors="replace")[:200]
        sys.exit(f"Retell respondió {e.code} en {ruta}: {detalle}")


def llamadas_del_agente(clave: str, agente: str, desde_ms: int) -> list[str]:
    """Las llamadas terminadas de ese agente desde esa hora. Se filtra aquí y no
    en la petición para no depender de la sintaxis de filtros de su API."""
    datos = pedir("POST", "/v3/list-calls", clave, {"limit": 200, "sort_order": "descending"})
    items = datos.get("items", []) if isinstance(datos, dict) else datos or []
    return [
        c["call_id"]
        for c in items
        if c.get("agent_id") == agente
        and (c.get("start_timestamp") or 0) >= desde_ms
        and c.get("call_status") == "ended"
    ]


def medir(clave: str, ids: list[str]) -> tuple[list[dict], list[float]]:
    filas, todos = [], []
    for call_id in ids:
        llamada = pedir("GET", f"/v2/get-call/{call_id}", clave) or {}
        e2e = ((llamada.get("latency") or {}).get("e2e") or {})
        valores = [float(v) for v in e2e.get("values") or []]
        todos.extend(valores)
        filas.append(
            {
                "call_id": call_id,
                "turnos": len(valores),
                "p50": percentil(valores, 50),
                "p90": percentil(valores, 90),
                "max": max(valores) if valores else math.nan,
            }
        )
    return filas, todos


def informe(filas: list[dict], todos: list[float]) -> tuple[str, bool]:
    p50, p90 = percentil(todos, 50), percentil(todos, 90)
    pasa = bool(todos) and p50 <= UMBRAL_P50_MS and p90 <= UMBRAL_P90_MS

    def ms(v: float) -> str:
        return "—" if math.isnan(v) else f"{v:.0f}"

    lineas = [
        "| # | call_id | turnos | p50 ms | p90 ms | máx ms |",
        "|---|---|---|---|---|---|",
    ]
    for i, f in enumerate(filas, start=1):
        lineas.append(
            f"| {i} | `{f['call_id']}` | {f['turnos']} | {ms(f['p50'])} | "
            f"{ms(f['p90'])} | {ms(f['max'])} |"
        )
    lineas += [
        "",
        f"**Conjunto:** {len(filas)} llamadas, {len(todos)} turnos · "
        f"**p50 {ms(p50)} ms** (umbral {UMBRAL_P50_MS}) · "
        f"**p90 {ms(p90)} ms** (umbral {UMBRAL_P90_MS})",
        "",
        f"**Veredicto V-3: {'PASA' if pasa else 'NO PASA'}**",
    ]
    if len(todos) < TURNOS_MINIMOS or len(filas) < LLAMADAS_MINIMAS:
        lineas.append(
            f"\nOjo: son {len(todos)} turnos en {len(filas)} llamadas y la prueba pide "
            f"al menos {TURNOS_MINIMOS} turnos en {LLAMADAS_MINIMAS} llamadas; el "
            "veredicto es provisional."
        )
    sin_datos = [f["call_id"] for f in filas if not f["turnos"]]
    if sin_datos:
        lineas.append(
            "\nSin latencia (¿llamada aún procesándose o sin turnos?): "
            + ", ".join(sin_datos)
        )
    return "\n".join(lineas), pasa


def principal(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--agente", help="agent_id de Retell de la prueba")
    ap.add_argument("--desde", help='hora local de inicio de la prueba, "AAAA-MM-DD HH:MM"')
    ap.add_argument("--llamadas", help="call_id separados por comas (en vez de --agente)")
    args = ap.parse_args(argv)
    if not args.llamadas and not (args.agente and args.desde):
        ap.error("pasa --llamadas, o --agente junto con --desde")

    clave = os.environ.get("RETELL_API_KEY") or getpass.getpass("API key de Retell: ")
    if args.llamadas:
        ids = [x.strip() for x in args.llamadas.split(",") if x.strip()]
    else:
        desde = datetime.strptime(args.desde, "%Y-%m-%d %H:%M")
        ids = llamadas_del_agente(clave, args.agente, int(desde.timestamp() * 1000))
        if not ids:
            print("No hay llamadas terminadas de ese agente desde esa hora.")
            return 1
    filas, todos = medir(clave, ids)
    texto, pasa = informe(filas, todos)
    print(texto)
    return 0 if pasa else 2


if __name__ == "__main__":
    sys.exit(principal())
