"""Un celular mexicano entra como +521… y se guarda como +52….

Si esto falla, el mismo paciente acaba con dos fichas: la de WhatsApp (que
Meta manda con el "1" viejo de móviles) y la de la llamada o del CSV (+52 a
secas), y Sofía no lo reconoce por teléfono.
"""
from app.providers.whatsapp.base import WA_USER_PREFIX, normalize_phone


def test_celular_con_521_se_normaliza_a_52():
    assert normalize_phone("+5219981234567") == "+529981234567"
    assert normalize_phone("5219981234567") == "+529981234567"
    assert normalize_phone("52 1 998 123 4567") == "+529981234567"


def test_un_52_ya_normalizado_o_un_fijo_no_se_toca():
    assert normalize_phone("+529981234567") == "+529981234567"
    assert normalize_phone("+525512345678") == "+525512345678"


def test_otros_paises_y_bsuid_pasan_tal_cual():
    # Un +52 de 11 dígitos no es el caso de móvil viejo: no se recorta a ciegas.
    assert normalize_phone("+52155512345678") == "+52155512345678"
    assert normalize_phone("+34600123456") == "+34600123456"
    assert normalize_phone(f"{WA_USER_PREFIX}MX.abc123") == f"{WA_USER_PREFIX}MX.abc123"
