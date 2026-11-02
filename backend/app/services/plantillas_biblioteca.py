"""Plantillas sugeridas: las que necesitan los recordatorios y seguimientos.

Es la `template_library` del CRM del 01, pero en código: no se editan desde el
panel (para eso está «Nueva») y así llegan iguales a todas las instalaciones
con cada versión, sin migraciones de datos.

Llevan `_v1` en el nombre a propósito. Meta no deja reutilizar el nombre de una
plantilla borrada durante semanas y una rechazada no conviene editarla (tiene
topes), así que corregir una es crear la `_v2`.

Las variables dicen de qué dato salen en la etiqueta. Solo el nombre se enlaza
a la ficha; la fecha, la hora y el negocio los rellenan los recordatorios
(tramo 2 de la SPEC-18), que saben de qué cita se trata.
"""
from __future__ import annotations

from pydantic import BaseModel

from app.services.plantillas_whatsapp import BotonPlantilla, PlantillaIn, VariablePlantilla


class Sugerida(BaseModel):
    clave: str
    titulo: str
    para_que: str
    plantilla: PlantillaIn


def _var(n: int, etiqueta: str, ejemplo: str, campo: str | None = None) -> VariablePlantilla:
    return VariablePlantilla(n=n, etiqueta=etiqueta, ejemplo=ejemplo, campo=campo)


SUGERIDAS: list[Sugerida] = [
    Sugerida(
        clave="recordatorio",
        titulo="Recordatorio de cita",
        para_que=(
            "Sale antes de la cita. Con los botones, el paciente confirma o pide "
            "cambiarla, y Sofía sigue la conversación."
        ),
        plantilla=PlantillaIn(
            nombre="recordatorio_cita_v1",
            categoria="UTILITY",
            cuerpo=(
                "Hola {{1}}, te recordamos tu cita en {{2}} el {{3}} a las {{4}}. "
                "¿Nos confirmas tu asistencia?"
            ),
            variables=[
                _var(1, "Nombre del paciente", "Ana", "nombre"),
                _var(2, "Nombre del negocio", "Clínica Sonrisa Perfecta"),
                _var(3, "Fecha de la cita", "martes 28 de octubre"),
                _var(4, "Hora de la cita", "10:30"),
            ],
            botones=[
                BotonPlantilla(tipo="respuesta", texto="Confirmo"),
                BotonPlantilla(tipo="respuesta", texto="Necesito cambiarla"),
            ],
        ),
    ),
    Sugerida(
        clave="inasistencia",
        titulo="Cita perdida",
        para_que=(
            "Sale cuando en la Agenda se marca «No asistió». Si el paciente quiere "
            "otro horario, Sofía se lo busca."
        ),
        plantilla=PlantillaIn(
            nombre="cita_perdida_v1",
            categoria="UTILITY",
            cuerpo=(
                "Hola {{1}}, te esperábamos en {{2}} para tu cita del {{3}} y no "
                "pudimos verte. ¿Te ayudamos a buscar otro horario?"
            ),
            variables=[
                _var(1, "Nombre del paciente", "Ana", "nombre"),
                _var(2, "Nombre del negocio", "Clínica Sonrisa Perfecta"),
                _var(3, "Fecha de la cita", "martes 28 de octubre"),
            ],
            botones=[
                BotonPlantilla(tipo="respuesta", texto="Sí, otro horario"),
                BotonPlantilla(tipo="respuesta", texto="Ahora no"),
            ],
        ),
    ),
    Sugerida(
        clave="confirmacion",
        titulo="Cita agendada",
        para_que=(
            "Para avisarle a alguien de una cita que agendó el equipo (por teléfono "
            "o en persona) cuando su ventana de 24 h ya se cerró."
        ),
        plantilla=PlantillaIn(
            nombre="cita_agendada_v1",
            categoria="UTILITY",
            cuerpo=(
                "Hola {{1}}, tu cita en {{2}} quedó agendada para el {{3}} a las {{4}}. "
                "Si necesitas cambiarla, respóndenos por aquí."
            ),
            variables=[
                _var(1, "Nombre del paciente", "Ana", "nombre"),
                _var(2, "Nombre del negocio", "Clínica Sonrisa Perfecta"),
                _var(3, "Fecha de la cita", "martes 28 de octubre"),
                _var(4, "Hora de la cita", "10:30"),
            ],
        ),
    ),
    Sugerida(
        clave="seguimiento",
        titulo="Seguimiento de interés",
        para_que=(
            "Para escribirle días después a quien preguntó y no agendó, con la "
            "ventana de 24 h ya cerrada. Es de marketing: Meta la cobra como tal "
            "y el pie ofrece la baja."
        ),
        plantilla=PlantillaIn(
            nombre="seguimiento_interes_v1",
            categoria="MARKETING",
            cuerpo=(
                "Hola {{1}}, hace unos días nos preguntaste por {{2}}. Si quieres, "
                "te ayudamos a agendar una cita de valoración. ¿Te buscamos un horario?"
            ),
            variables=[
                _var(1, "Nombre del paciente", "Ana", "nombre"),
                _var(2, "Lo que le interesa", "el blanqueamiento dental", "servicio_interes"),
            ],
            pie="Responde BAJA si no quieres más mensajes.",
            botones=[
                BotonPlantilla(tipo="respuesta", texto="Sí, agendar"),
                BotonPlantilla(tipo="respuesta", texto="Ahora no"),
            ],
        ),
    ),
]
