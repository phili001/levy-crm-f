"""El catálogo «Copiar prompt» (Fase 10.2).

Los 12 prompts que se pegan en Claude Code, servidos desde `backend/prompts/`
(viajan en la imagen, como el CHANGELOG). Lo que se comprueba:
  - están los 12, cada uno con título, grupo y texto, y el LEEME no sale;
  - cada tarjeta de Canales → Servicios que lleva botón tiene su prompt;
  - ninguno pide una llave por el chat: las llaves van a la segunda terminal;
  - `docs/prompts` apunta a la misma carpeta;
  - solo un administrador los lee.
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.test_voice_gating import pytestmark_db

# Las tarjetas del panel que llevan «Copiar prompt» (ServicesTab.tsx) y la IA.
CANALES_DEL_PANEL = {"ia", "whatsapp", "social", "correo", "webchat", "voz", "meta", "google"}


def _catalogo() -> list[dict]:
    from app.services.prompts_catalogo import leer_catalogo

    return leer_catalogo()


def test_estan_los_doce_y_el_leeme_no_sale():
    prompts = _catalogo()
    assert len(prompts) == 12
    ids = [p["id"] for p in prompts]
    assert len(set(ids)) == 12
    assert "LEEME" not in ids
    assert ids[0] == "instalar", "van en el orden de los archivos"
    for p in prompts:
        assert p["titulo"] and p["texto"] and p["grupo"] in ("principal", "avanzado"), p["id"]


def test_cada_tarjeta_con_boton_tiene_su_prompt():
    canales = {p["canal"] for p in _catalogo() if p["canal"]}
    assert CANALES_DEL_PANEL <= canales


def test_ninguno_pide_una_llave_por_el_chat():
    for p in _catalogo():
        texto = p["texto"]
        if "secret set" in texto:
            assert "segunda terminal" in texto, p["id"]
        assert "pégame" not in texto.lower() and "pégala aquí" not in texto.lower(), p["id"]


def test_los_que_esperan_el_mensaje_de_prueba_piden_tiempo_de_sobra():
    """`connect` se queda hasta 5 min esperando la prueba: con el tiempo de
    espera por defecto de Claude Code (2 min) se cortaría a medias."""
    for p in _catalogo():
        if "omni.py connect" in p["texto"] and p["canal"] in ("whatsapp", "social", "correo", "voz"):
            assert "10 minutos" in p["texto"], p["id"]


def test_docs_prompts_es_la_misma_carpeta():
    from app.services.prompts_catalogo import carpeta

    repo = Path(__file__).resolve().parents[2]
    enlace = repo / "docs" / "prompts"
    assert enlace.is_dir()
    assert enlace.resolve() == carpeta().resolve()


@pytestmark_db
@pytest.mark.asyncio
async def test_solo_un_administrador_lee_el_catalogo():
    from app.main import app
    from tests.test_agenda_desde_el_panel import _borrar_usuarios, _usuarios

    admin, operador, _revisor, usuarios = await _usuarios()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/api/v1/admin/prompts-catalogo", headers=admin)
            assert r.status_code == 200
            assert len(r.json()) == 12
            assert "<dominio>" in r.json()[1]["texto"], "el panel pone el dominio al copiar"
            r = await c.get("/api/v1/admin/prompts-catalogo", headers=operador)
            assert r.status_code == 403
    finally:
        await _borrar_usuarios(usuarios)
