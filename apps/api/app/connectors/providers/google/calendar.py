"""CalendarCapability Google (Calendar API via google-api-python-client, T5)."""

# google-api-python-client n'expose pas de types.
# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingModuleSource=false

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import throttle
from app.connectors.capabilities import CalendarEvent, CalendarEventDraft
from app.connectors.client_base import fresh_access_token, run_sync_call
from app.connectors.registry import get_provider
from app.connectors.tenant_models import ConnectorConnection


def _parse_when(when: dict[str, Any]) -> datetime:
    if "dateTime" in when:
        return datetime.fromisoformat(str(when["dateTime"]))
    # Événement « journée entière » : minuit UTC (seul dénominateur commun).
    day = date.fromisoformat(str(when["date"]))
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def google_event_to_calendar_event(payload: dict[str, Any]) -> CalendarEvent:
    """Mapping Google Calendar → modèle normalisé."""
    attendees = [
        str(attendee["email"]) for attendee in payload.get("attendees", []) if attendee.get("email")
    ]
    return CalendarEvent(
        provider_raw_id=str(payload["id"]),
        title=str(payload.get("summary", "")),
        description=payload.get("description"),
        location=payload.get("location"),
        starts_at=_parse_when(payload["start"]),
        ends_at=_parse_when(payload["end"]),
        attendees=attendees,
    )


def draft_to_google_event(draft: CalendarEventDraft) -> dict[str, Any]:
    body: dict[str, Any] = {
        "summary": draft.title,
        "start": {"dateTime": draft.starts_at.isoformat()},
        "end": {"dateTime": draft.ends_at.isoformat()},
    }
    if draft.description is not None:
        body["description"] = draft.description
    if draft.location is not None:
        body["location"] = draft.location
    if draft.attendees:
        body["attendees"] = [{"email": email} for email in draft.attendees]
    return body


class GoogleCalendar:
    def __init__(self, session: AsyncSession, connection: ConnectorConnection) -> None:
        self._session = session
        self._connection = connection
        self._manifest = get_provider(connection.provider)

    async def _execute(self, call: Callable[[Any], Any]) -> Any:
        access_token = await fresh_access_token(self._session, self._connection)

        async def attempt() -> Any:
            def sync_call() -> Any:
                from google.oauth2.credentials import Credentials

                credentials = Credentials(token=access_token)  # pyright: ignore[reportCallIssue]
                client = build("calendar", "v3", credentials=credentials, cache_discovery=False)
                try:
                    return call(client)
                except HttpError as exc:
                    raise throttle.ProviderResponseError(
                        exc.status_code, detail="calendar"
                    ) from exc

            return await run_sync_call(sync_call)

        return await throttle.run_with_backoff(self._manifest, self._connection.id, attempt)

    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        def list_call(client: Any) -> Any:
            items: list[dict[str, Any]] = []
            page_token: str | None = None
            while True:
                response = (
                    client.events()
                    .list(
                        calendarId="primary",
                        timeMin=start.isoformat(),
                        timeMax=end.isoformat(),
                        singleEvents=True,
                        orderBy="startTime",
                        pageToken=page_token,
                    )
                    .execute()
                )
                items.extend(response.get("items", []))
                page_token = response.get("nextPageToken")
                if page_token is None:
                    return items

        payloads = await self._execute(list_call)
        return [google_event_to_calendar_event(payload) for payload in payloads]

    async def create_event(self, event: CalendarEventDraft) -> CalendarEvent:
        body = draft_to_google_event(event)
        payload = await self._execute(
            lambda client: client.events().insert(calendarId="primary", body=body).execute()
        )
        return google_event_to_calendar_event(payload)
