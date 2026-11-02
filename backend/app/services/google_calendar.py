"""Cliente Google Calendar (F8b).

Wrapper minimalista sobre la Calendar v3 REST API. No usamos
`google-api-python-client` para no añadir dependencia pesada — bastan
httpx + el access_token que da `google_oauth.get_access_token`.

Métodos:
  - list_calendars() — calendarios a los que tiene acceso el usuario.
  - list_busy_slots(start, end, calendar_ids) — slots ocupados via freeBusy.
  - create_event(calendar_id, start, end, summary, ...) — crea evento.
  - update_event(event_id, ...) / delete_event(event_id) — la copia de la
    agenda propia (Fase 9, `services/calendar_sync.py`).

Los que escriben devuelven None si no hay cuenta conectada y lanzan
`httpx.HTTPStatusError` si Google contesta con error: quien llama distingue
"no hay Google" de "Google dijo que no".

El timezone por defecto es el del negocio (DASHBOARD_TIMEZONE). Si viene `timezone` en kwargs, se
respeta. Para todas las consultas de disponibilidad se asume que la entrada va
en la zona por defecto, y el cliente del agente verá los horarios en esa misma
zona.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from app.core.config import settings

from app.core.logging import get_logger
from app.services.google_oauth import get_access_token

logger = get_logger(__name__)

CAL_BASE = "https://www.googleapis.com/calendar/v3"
DEFAULT_TZ = settings.DASHBOARD_TIMEZONE


async def _client_headers() -> dict[str, str] | None:
    token = await get_access_token("google_calendar")
    if not token:
        return None
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


async def list_calendars() -> list[dict[str, Any]]:
    headers = await _client_headers()
    if not headers:
        return []
    url = f"{CAL_BASE}/users/me/calendarList"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()
    items = data.get("items") or []
    return [
        {
            "id": c.get("id"),
            "summary": c.get("summary"),
            "primary": bool(c.get("primary")),
            "access_role": c.get("accessRole"),
            "background_color": c.get("backgroundColor"),
            "timezone": c.get("timeZone"),
        }
        for c in items
    ]


async def list_busy_slots(
    time_min: datetime,
    time_max: datetime,
    calendar_ids: list[str] | None = None,
) -> list[dict[str, str]]:
    """Devuelve slots ocupados en los calendarios indicados.

    Si `calendar_ids` es None, usa el primary del usuario.
    Cada slot: {"start": "2026-05-20T10:00:00+02:00", "end": "...", "calendar_id": "..."}
    """
    headers = await _client_headers()
    if not headers:
        return []
    if not calendar_ids:
        cals = await list_calendars()
        primary = next((c for c in cals if c.get("primary")), None)
        if not primary:
            return []
        calendar_ids = [primary["id"]]
    body = {
        "timeMin": time_min.isoformat(),
        "timeMax": time_max.isoformat(),
        "timeZone": DEFAULT_TZ,
        "items": [{"id": cid} for cid in calendar_ids],
    }
    url = f"{CAL_BASE}/freeBusy"
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(url, headers=headers, json=body)
        r.raise_for_status()
        data = r.json()
    busy: list[dict[str, str]] = []
    for cid, info in (data.get("calendars") or {}).items():
        for slot in info.get("busy") or []:
            busy.append(
                {
                    "calendar_id": cid,
                    "start": slot.get("start", ""),
                    "end": slot.get("end", ""),
                }
            )
    return busy


async def create_event(
    *,
    start: datetime,
    end: datetime,
    summary: str,
    description: str | None = None,
    calendar_id: str = "primary",
    attendee_emails: list[str] | None = None,
    timezone_str: str = DEFAULT_TZ,
    send_updates: str = "all",  # "all" | "externalOnly" | "none"
    event_id: str | None = None,
) -> dict[str, Any] | None:
    """Crea un evento en el calendario. Devuelve el dict del evento creado.

    Con `event_id` el evento nace con ese id (Google admite 0-9 y a-v, de 5 a
    1024 caracteres). Crearlo dos veces da 409 en vez de un duplicado: es lo
    que hace idempotente la copia de la agenda.
    """
    headers = await _client_headers()
    if not headers:
        return None
    body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": timezone_str},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone_str},
    }
    if event_id:
        body["id"] = event_id
    if description:
        body["description"] = description
    if attendee_emails:
        body["attendees"] = [{"email": e} for e in attendee_emails]
    url = f"{CAL_BASE}/calendars/{calendar_id}/events"
    params = {"sendUpdates": send_updates}
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, headers=headers, params=params, json=body)
        if r.status_code >= 400:
            # 409 con id propio = ya existe, y quien llama lo espera: no es un error.
            if not (r.status_code == 409 and event_id):
                logger.error("calendar.create_event.error", status=r.status_code, body=r.text[:200])
            r.raise_for_status()
        return r.json()


async def update_event(
    event_id: str,
    *,
    start: datetime,
    end: datetime,
    summary: str,
    description: str | None = None,
    calendar_id: str = "primary",
    timezone_str: str = DEFAULT_TZ,
) -> dict[str, Any] | None:
    """Pone el evento como se le dice, sin avisar a nadie.

    Lleva `status: confirmed` para que una copia borrada a mano en Google
    vuelva a aparecer si la cita sigue en pie.
    """
    headers = await _client_headers()
    if not headers:
        return None
    body: dict[str, Any] = {
        "summary": summary,
        "description": description or "",
        "start": {"dateTime": start.isoformat(), "timeZone": timezone_str},
        "end": {"dateTime": end.isoformat(), "timeZone": timezone_str},
        "status": "confirmed",
    }
    url = f"{CAL_BASE}/calendars/{calendar_id}/events/{event_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.patch(url, headers=headers, params={"sendUpdates": "none"}, json=body)
        if r.status_code >= 400:
            if r.status_code not in (404, 410):
                logger.error("calendar.update_event.error", status=r.status_code, body=r.text[:200])
            r.raise_for_status()
        return r.json()


async def delete_event(event_id: str, *, calendar_id: str = "primary") -> bool | None:
    """Borra el evento. True también si ya no estaba (404/410): el objetivo se cumple."""
    headers = await _client_headers()
    if not headers:
        return None
    url = f"{CAL_BASE}/calendars/{calendar_id}/events/{event_id}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.delete(url, headers=headers, params={"sendUpdates": "none"})
        if r.status_code in (404, 410):
            return True
        if r.status_code >= 400:
            logger.error("calendar.delete_event.error", status=r.status_code, body=r.text[:200])
            r.raise_for_status()
        return True


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
