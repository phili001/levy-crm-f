"""Las reglas "NO TOCAR" del prompt de voz no se pierden al guardar (US-19 AC3).

La comprobación en sí, sin base de datos. Que el panel, el historial y la
agent-api la apliquen de verdad se prueba por HTTP en
`test_voz_conectar_desde_el_crm.py`.
"""
from __future__ import annotations

import pytest

from app.scripts.profiles import dental_mx
from app.services.prompt_guard import (
    ReglaProtegidaError,
    comprobar_reglas_protegidas,
    secciones_protegidas,
)

VOZ = dental_mx.PROMPT_VOZ
SEPARADOR = "======================================"


def _sin_la_seccion_protegida(prompt: str) -> str:
    return prompt.split(f"{SEPARADOR}\n5. SEGURIDAD")[0]


def test_el_perfil_dental_protege_las_reglas_medicas_del_agente_de_voz():
    (titulo, reglas), = secciones_protegidas(VOZ).values()
    assert titulo == "SEGURIDAD — NO TOCAR"
    texto = " ".join(reglas)
    for regla in ("Nunca diagnosticas", "Nunca recomiendas medicamentos", "Nunca confirmas una cita"):
        assert regla in texto, f"«{regla}» tiene que estar en la sección protegida"


def test_los_pasos_numerados_de_una_seccion_no_se_confunden_con_secciones():
    # La sección 2 del prompt de voz tiene pasos "1. Saluda", "2. Pregunta"…
    assert list(secciones_protegidas(VOZ)) == ["SEGURIDAD — NO TOCAR"]


def test_quitar_la_seccion_protegida_no_se_puede_guardar():
    with pytest.raises(ReglaProtegidaError, match="Falta la sección «SEGURIDAD — NO TOCAR»"):
        comprobar_reglas_protegidas(VOZ, _sin_la_seccion_protegida(VOZ))


def test_reescribir_una_regla_protegida_no_se_puede_guardar():
    cambiado = VOZ.replace("Nunca recomiendas medicamentos ni dosis.", "Recomienda ibuprofeno.")
    with pytest.raises(ReglaProtegidaError, match="Nunca recomiendas medicamentos"):
        comprobar_reglas_protegidas(VOZ, cambiado)


def test_quitarle_el_no_tocar_al_titulo_cuenta_como_quitar_la_seccion():
    with pytest.raises(ReglaProtegidaError, match="quitarle el «NO TOCAR»"):
        comprobar_reglas_protegidas(VOZ, VOZ.replace("SEGURIDAD — NO TOCAR", "SEGURIDAD"))


def test_se_pueden_anadir_reglas_renumerar_y_partir_lineas():
    nuevo = (
        VOZ.replace("5. SEGURIDAD — NO TOCAR", "6. SEGURIDAD — NO TOCAR")
        .replace("- Nunca recomiendas medicamentos ni dosis.", "- Nunca recomiendas\n  medicamentos ni dosis.")
        + "- Nunca hablas de la competencia.\n"
    )
    comprobar_reglas_protegidas(VOZ, nuevo)


def test_el_resto_del_prompt_se_edita_libre():
    comprobar_reglas_protegidas(VOZ, VOZ.replace("Frases cortas", "Frases muy cortas"))


def test_un_prompt_sin_seccion_protegida_no_tiene_nada_que_proteger():
    viejo = _sin_la_seccion_protegida(VOZ)
    comprobar_reglas_protegidas(viejo, "Eres Sofía. Contesta corto.")


def test_una_instalacion_vieja_puede_ganar_las_reglas_medicas_pegandolas():
    """La instalación viva sembró la versión anterior del perfil, sin las reglas
    médicas en la sección protegida: se le pueden añadir desde el panel."""
    anterior = VOZ.replace(
        "- Nunca diagnosticas: \"el doctor te valora en la cita y te dice con certeza\".\n", ""
    )
    comprobar_reglas_protegidas(anterior, VOZ)
