"""Perfil `blank`: la instalación de fábrica, sin datos de ningún negocio.

Es el comportamiento que siempre tuvo el seed: admin, los dos agentes con su
plantilla llena de {{PENDIENTE}}, las cuatro etiquetas base y nada más. Sirve
para quien instala el CRM para un negocio que no es una clínica.
"""
from __future__ import annotations

PERFIL: dict = {
    "nombre": "blank",
    "bloque_comun": "",
    "prompt_texto": None,
    "prompt_voz": None,
    "saludo_voz": "",
    "etiquetas": None,  # None = las de fábrica del seed (TAGS_DEFAULT)
    "horario": None,
    "servicios": [],
    "kb": [],
    "widget": {"activo": False, "saludo": "", "titulo": ""},
    "demo_publica": False,
}
