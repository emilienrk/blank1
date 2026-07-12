"""CalendarCapability Microsoft 365 (Graph REST via httpx, décision D2, T5)."""

from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.capabilities import CalendarEvent, CalendarEventDraft
from app.connectors.client_base import graph_request
from app.connectors.providers.microsoft.mail import address_of, parse_graph_datetime
from app.connectors.tenant_models import ConnectorConnection


def _parse_when(node: dict[str, Any]) -> datetime:
    value = str(node.get("dateTime", ""))
    parsed = parse_graph_datetime(value)
    if parsed.tzinfo is None or str(node.get("timeZone", "UTC")).upper() == "UTC":
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def graph_event_to_calendar_event(payload: dict[str, Any]) -> CalendarEvent:
    """Mapping Microsoft Graph → modèle normalisé (mêmes champs que Google)."""
    location = payload.get("location", {})
    location_name: str | None = None
    if isinstance(location, dict):
        display_name = cast(dict[str, Any], location).get("displayName")
        if display_name:
            location_name = str(display_name)
    raw_attendees = payload.get("attendees", [])
    attendees = [
        address
        for address in (address_of(item) for item in cast(list[Any], raw_attendees))
        if address
    ]
    body_preview = payload.get("bodyPreview")
    return CalendarEvent(
        provider_raw_id=str(payload["id"]),
        title=str(payload.get("subject", "")),
        description=str(body_preview) if body_preview else None,
        location=location_name,
        starts_at=_parse_when(payload["start"]),
        ends_at=_parse_when(payload["end"]),
        attendees=attendees,
    )


def draft_to_graph_event(draft: CalendarEventDraft) -> dict[str, Any]:
    body: dict[str, Any] = {
        "subject": draft.title,
        "start": {
            "dateTime": draft.starts_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
        "end": {
            "dateTime": draft.ends_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        },
    }
    if draft.description is not None:
        body["body"] = {"contentType": "Text", "content": draft.description}
    if draft.location is not None:
        body["location"] = {"displayName": draft.location}
    if draft.attendees:
        body["attendees"] = [
            {"emailAddress": {"address": address}, "type": "required"}
            for address in draft.attendees
        ]
    return body


class MicrosoftCalendar:
    def __init__(self, session: AsyncSession, connection: ConnectorConnection) -> None:
        self._session = session
        self._connection = connection

    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        params = {
            "startDateTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "endDateTime": end.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "$orderby": "start/dateTime",
        }
        response = await graph_request(
            self._session, self._connection, "GET", "/me/calendarView", params=params
        )
        payload: dict[str, Any] = response.json()
        return [graph_event_to_calendar_event(item) for item in payload.get("value", [])]

    async def create_event(self, event: CalendarEventDraft) -> CalendarEvent:
        response = await graph_request(
            self._session, self._connection, "POST", "/me/events", json=draft_to_graph_event(event)
        )
        return graph_event_to_calendar_event(response.json())
