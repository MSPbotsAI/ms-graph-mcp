"""Calendar tools (Microsoft Graph calendar / event API).

Scope note: the four tools here cover one business flow — "find a free slot,
book it, cancel it if it falls through": read a time window of someone's
calendar, read free/busy across several mailboxes at once, create an event,
cancel/delete an event. Rescheduling (PATCH), attendee response tracking,
recurrence editing, calendar groups and room/equipment resource booking are
deliberately not exposed — none of them are needed to satisfy the scheduling
use case, and each would be another tool competing for the agent's attention.

Delegated vs app-only: an app-only token (ms-graph-app) has no /me, so every
tool here takes an optional user_id / caller_user_id that MUST be supplied
under that grant. Under the delegated grant, omitting it targets the
signed-in admin's own calendar; touching someone else's calendar needs that
calendar to be shared/delegated to the admin (Calendars.ReadWrite.Shared).
"""

from collections.abc import Callable
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import GraphClient, GraphError
from ._common import NO_TOKEN

# calendarView returns whole events; 200 is the same hard ceiling the rest of
# this fleet uses, and the 20,000-char cap in _json.py still trims below it.
_MAX_EVENTS = 200
# getSchedule takes a mailbox list. Kept small on purpose: every extra mailbox
# adds a full scheduleItems block, and 20 of them already crowd the char cap.
_MAX_SCHEDULES = 20
# Graph's own bounds for availabilityViewInterval (minutes).
_MIN_INTERVAL, _MAX_INTERVAL = 5, 1440

_EVENT_FIELDS = (
    "id,subject,start,end,location,organizer,attendees,isAllDay,isCancelled,"
    "isOnlineMeeting,onlineMeetingUrl,webLink"
)

_USER_ID_DESC = (
    "Target user's id (GUID) or userPrincipalName. Omit to use the token's own signed-in "
    "user — only possible under the delegated grant; an app-only token has no signed-in "
    "user, so it must always pass this."
)
_TZ_DESC = (
    'Time zone name for the times above and in the response, e.g. "UTC", "Pacific Standard '
    'Time", "China Standard Time". An explicit offset inside the datetime value wins over this.'
)


def _dtz(value: str, timezone: str) -> dict:
    return {"dateTime": value, "timeZone": timezone}


def _prefer_tz(timezone: str) -> dict[str, str]:
    return {"Prefer": f'outlook.timezone="{timezone}"'}


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_calendar_events(
        start_datetime: Annotated[
            str,
            Field(
                description="Start of the time window, ISO 8601, e.g. 2026-09-15T09:00:00 or "
                "2026-09-15T09:00:00-08:00."
            ),
        ],
        end_datetime: Annotated[
            str, Field(description="End of the time window, ISO 8601. Must be after start_datetime.")
        ],
        user_id: Annotated[str | None, Field(description=_USER_ID_DESC)] = None,
        limit: Annotated[
            int, Field(description=f"Max events to return (1-{_MAX_EVENTS}).", ge=1, le=_MAX_EVENTS)
        ] = 25,
        timezone: Annotated[str, Field(description=_TZ_DESC)] = "UTC",
    ) -> str:
        """List a user's calendar events inside a time window.

        Expands recurring series into their individual occurrences, so this
        is what to use for "what is on X's calendar next week" and to get
        an event's id before cancelling it. Reading someone else's calendar
        needs an app-only token, or that calendar shared with the caller.
        Returns only the first page of results, newest window first — narrow
        the window rather than raising limit.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        base = f"/users/{user_id}" if user_id else "/me"
        try:
            result = await client.get(
                f"{base}/calendarView",
                params={
                    "startDateTime": start_datetime,
                    "endDateTime": end_datetime,
                    "$select": _EVENT_FIELDS,
                    "$orderby": "start/dateTime",
                    "$top": max(1, min(limit, _MAX_EVENTS)),
                },
                extra_headers=_prefer_tz(timezone),
            )
            events = result.get("value", []) if isinstance(result, dict) else []
            return dump_json_capped(
                {
                    "user_id": user_id or "me",
                    "window": {"start": start_datetime, "end": end_datetime, "timezone": timezone},
                    "count": len(events),
                    "events": events,
                }
            )
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_get_user_availability(
        user_ids: Annotated[
            list[str],
            Field(
                description='Mailbox SMTP addresses to check, e.g. ["alice@contoso.com", '
                f'"bob@contoso.com"]. At most {_MAX_SCHEDULES} per call.'
            ),
        ],
        start_datetime: Annotated[
            str, Field(description="Start of the window to check, ISO 8601, e.g. 2026-09-15T09:00:00.")
        ],
        end_datetime: Annotated[str, Field(description="End of the window to check, ISO 8601.")],
        interval_minutes: Annotated[
            int,
            Field(
                description=f"Slot size of the availabilityView string, {_MIN_INTERVAL}-{_MAX_INTERVAL} minutes.",
                ge=_MIN_INTERVAL,
                le=_MAX_INTERVAL,
            ),
        ] = 30,
        timezone: Annotated[str, Field(description=_TZ_DESC)] = "UTC",
        caller_user_id: Annotated[
            str | None,
            Field(
                description="Mailbox whose calendar service answers the query (id or UPN). Omit to use "
                "the signed-in user — an app-only token has none, so it must pass one; any mailbox in "
                "the tenant works, including one of user_ids."
            ),
        ] = None,
    ) -> str:
        """Check free/busy availability for several people over a time window.

        Use this before booking to find a slot everyone is free in — it is
        cheaper and less privacy-invasive than reading each calendar, and
        works across the whole tenant. Each person gets an availabilityView
        string, one character per slot: 0 free, 1 tentative, 2 busy, 3 out
        of office, 4 working elsewhere. Then book with
        graph_create_calendar_event.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        if not user_ids:
            return error_envelope("invalid_argument", "user_ids must not be empty.", False)
        if len(user_ids) > _MAX_SCHEDULES:
            return error_envelope(
                "invalid_argument",
                f"At most {_MAX_SCHEDULES} mailboxes per call, got {len(user_ids)}; split the request.",
                False,
            )

        base = f"/users/{caller_user_id}" if caller_user_id else "/me"
        payload = {
            "schedules": user_ids,
            "startTime": _dtz(start_datetime, timezone),
            "endTime": _dtz(end_datetime, timezone),
            "availabilityViewInterval": max(_MIN_INTERVAL, min(interval_minutes, _MAX_INTERVAL)),
        }
        try:
            result = await client.post(
                f"{base}/calendar/getSchedule", payload, extra_headers=_prefer_tz(timezone)
            )
            schedules = result.get("value", []) if isinstance(result, dict) else []
            return dump_json_capped(
                {
                    "window": {"start": start_datetime, "end": end_datetime, "timezone": timezone},
                    "interval_minutes": payload["availabilityViewInterval"],
                    "count": len(schedules),
                    "schedules": schedules,
                }
            )
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
    )
    async def graph_create_calendar_event(
        subject: Annotated[str, Field(description="Event subject line.")],
        start_datetime: Annotated[
            str, Field(description="Event start, ISO 8601, e.g. 2026-09-15T14:00:00.")
        ],
        end_datetime: Annotated[str, Field(description="Event end, ISO 8601. Must be after the start.")],
        user_id: Annotated[
            str | None, Field(description="Organizer's calendar. " + _USER_ID_DESC)
        ] = None,
        timezone: Annotated[str, Field(description=_TZ_DESC)] = "UTC",
        attendees: Annotated[
            list[str] | None,
            Field(description='Required attendees\' email addresses, e.g. ["alice@contoso.com"].'),
        ] = None,
        optional_attendees: Annotated[
            list[str] | None, Field(description="Optional attendees' email addresses.")
        ] = None,
        body: Annotated[str | None, Field(description="Event body / description.")] = None,
        body_content_type: Annotated[
            Literal["Text", "HTML"], Field(description='"Text" or "HTML".')
        ] = "Text",
        location: Annotated[
            str | None, Field(description='Location display name, e.g. "Meeting room 3".')
        ] = None,
        is_online_meeting: Annotated[
            bool, Field(description="Attach a Teams meeting link; the join URL comes back in the result.")
        ] = False,
        reminder_minutes_before_start: Annotated[
            int | None, Field(description="Reminder lead time in minutes. Omit for the mailbox default.")
        ] = None,
    ) -> str:
        """Create a calendar event, optionally inviting attendees.

        Invitations are sent the moment this returns and attendees see the
        booking immediately — confirm the subject, exact times, time zone and
        attendee list with the user before calling. Check the slot first with
        graph_get_user_availability; to undo, call
        graph_cancel_calendar_event with the returned id.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        def _attendees(addresses: list[str], kind: str) -> list[dict]:
            return [{"emailAddress": {"address": a}, "type": kind} for a in addresses]

        payload: dict = {
            "subject": subject,
            "start": _dtz(start_datetime, timezone),
            "end": _dtz(end_datetime, timezone),
        }
        invited = _attendees(attendees or [], "required") + _attendees(
            optional_attendees or [], "optional"
        )
        if invited:
            payload["attendees"] = invited
        if body is not None:
            payload["body"] = {"contentType": body_content_type, "content": body}
        if location:
            payload["location"] = {"displayName": location}
        if is_online_meeting:
            payload["isOnlineMeeting"] = True
            payload["onlineMeetingProvider"] = "teamsForBusiness"
        if reminder_minutes_before_start is not None:
            payload["isReminderOn"] = True
            payload["reminderMinutesBeforeStart"] = reminder_minutes_before_start

        base = f"/users/{user_id}" if user_id else "/me"
        try:
            result = await client.post(f"{base}/events", payload)
        except GraphError as e:
            return e.to_envelope()

        created = result if isinstance(result, dict) else {}
        online = created.get("onlineMeeting") or {}
        return dump_json_capped(
            {
                "event_id": created.get("id"),
                "subject": created.get("subject"),
                "start": created.get("start"),
                "end": created.get("end"),
                "web_link": created.get("webLink"),
                "join_url": online.get("joinUrl"),
                "attendees_invited": len(invited),
            }
        )

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
    )
    async def graph_cancel_calendar_event(
        event_id: Annotated[
            str,
            Field(
                description="Event id from graph_list_calendar_events or "
                "graph_create_calendar_event — never guess it, resolve it first."
            ),
        ],
        user_id: Annotated[
            str | None, Field(description="Calendar the event lives on. " + _USER_ID_DESC)
        ] = None,
        comment: Annotated[
            str | None, Field(description="Note included in the cancellation sent to attendees.")
        ] = None,
    ) -> str:
        """Cancel a meeting (notifying attendees) or delete an appointment.

        Meetings the caller organizes are cancelled, which mails every
        attendee; an event with no attendees, or one the caller does not
        organize, is deleted from that calendar instead — the result says
        which happened. Not recoverable, so confirm the exact event with the
        user first. Idempotent: an already-gone event still returns success.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        base = f"/users/{user_id}" if user_id else "/me"

        def _done(method: str) -> str:
            return dump_json_capped({"event_id": event_id, "cancelled": True, "method": method})

        try:
            await client.post(f"{base}/events/{event_id}/cancel", {"Comment": comment or ""})
            return _done("cancelled")
        except GraphError as e:
            if e.status_code == 404:
                return _done("already_absent")
            # /cancel is organizer-only and rejects events without attendees;
            # falling back to a plain delete is what the user meant either way.
            if e.status_code not in (400, 403):
                return e.to_envelope()

        try:
            await client.delete(f"{base}/events/{event_id}")
        except GraphError as e:
            if e.status_code != 404:
                return e.to_envelope()
            return _done("already_absent")
        return _done("deleted")
