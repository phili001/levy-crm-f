"""Perfiles de instalación: lo que el seed deja listo según el negocio.

Un perfil es un diccionario Python (no YAML: el repo no trae PyYAML y no vale
una dependencia nueva para dos archivos) con las claves que lee
`app/scripts/apply_profile.py`:

    nombre            identificador (dental-mx, blank)
    bloque_comun      texto para `app_settings["prompt.bloque_comun"]`
    prompt_texto      prompt del agente de texto (None = plantilla del seed)
    prompt_voz        prompt del agente de voz (None = plantilla del seed)
    saludo_voz        saludo del canal de voz (`app_settings["voice.greeting"]`)
    etiquetas         [(nombre, color)] que reemplazan a las de fábrica
    horario           dict con las claves calendar.* (zona, días, tramos, paso, antelación)
    servicios         [{nombre, duracion_min, precio_aprox}] → `app_settings["agenda.servicios"]`
    kb                [(título, contenido)] documentos de texto de la base de conocimiento
    widget            {"activo": bool, "saludo": str, "titulo": str}
    demo_publica      bool → `app_settings["ui.demo_publica"]`

`SEED_PROFILE` (variable de entorno) elige cuál. El perfil `blank` es la
instalación de fábrica de siempre: admin, plantillas y etiquetas base.
"""
from __future__ import annotations

from app.scripts.profiles import blank, dental_mx

PERFILES: dict[str, dict] = {
    dental_mx.PERFIL["nombre"]: dental_mx.PERFIL,
    blank.PERFIL["nombre"]: blank.PERFIL,
}


def obtener_perfil(nombre: str) -> dict:
    """Perfil por nombre; un nombre desconocido cae a `blank` con aviso del llamante."""
    return PERFILES.get((nombre or "").strip().lower(), blank.PERFIL)
