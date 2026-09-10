"""tools/list snapshot + error-envelope mapping tests.

No network calls: tool enumeration goes through FastMCP's in-process
list_tools(), and the error-code mapping is tested directly against
GraphError, independent of any real HTTP request.
"""

import json

import pytest

from graph_mcp.api_client import GraphError
from graph_mcp.config import Settings
from graph_mcp.server import create_mcp_server

EXPECTED_TOOLS = {
    "graph_check_user_exists": set(),
    "graph_create_user": {
        "display_name",
        "user_principal_name",
        "mail_nickname",
        "password",
        "usage_location",
    },
    "graph_reset_password": {"user_id", "new_password"},
    "graph_get_user": {"user_id"},
    "graph_update_user": {"user_id"},
    "graph_assign_manager": {"user_id", "manager_id"},
    "graph_list_auth_methods": {"user_id"},
    "graph_revoke_sessions": {"user_id"},
    "graph_assign_groups": {"user_id", "group_ids"},
    "graph_list_user_groups": {"user_id"},
    "graph_list_groups": set(),
    "graph_remove_group_member": {"user_id", "group_ids"},
    "graph_list_owned_groups": {"user_id"},
    "graph_check_license_stock": set(),
    "graph_assign_license": {"user_id"},
    "graph_send_mail": {"to_recipients", "subject", "body"},
    "graph_search_sites": {"query"},
    "graph_list_drive_items": {"drive_id"},
    "graph_get_file": {"drive_id", "item_id"},
    "graph_read_file_text": {"drive_id", "item_id"},
    "graph_write_file_text": {"drive_id", "item_id", "content"},
    "graph_create_file_text": {"drive_id", "path", "content"},
    "graph_delete_file": {"drive_id", "item_id"},
    "graph_list_managed_devices": {"user_id"},
    "graph_remove_managed_device": {"device_id"},
    "graph_list_calendar_events": {"start_datetime", "end_datetime"},
    "graph_get_user_availability": {"user_ids", "start_datetime", "end_datetime"},
    "graph_create_calendar_event": {"subject", "start_datetime", "end_datetime"},
    "graph_cancel_calendar_event": {"event_id"},
}

# Tools that are not plain read-only queries (writes / mutations).
_NON_READ_ONLY = {
    "graph_create_user",
    "graph_reset_password",
    "graph_update_user",
    "graph_assign_manager",
    "graph_revoke_sessions",
    "graph_assign_groups",
    "graph_remove_group_member",
    "graph_assign_license",
    "graph_send_mail",
    "graph_write_file_text",
    "graph_create_file_text",
    "graph_delete_file",
    "graph_remove_managed_device",
    "graph_create_calendar_event",
    "graph_cancel_calendar_event",
}


@pytest.mark.asyncio
async def test_tools_list_snapshot():
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_TOOLS), f"unexpected tool set: {names}"
    # 29: original <=20 guideline, +2 for SharePoint create/delete (PRD-17756),
    # +3 for graph_list_owned_groups/graph_list_managed_devices/
    # graph_remove_managed_device (PRD-17403 offboarding tool-gap audit),
    # +4 for the calendar domain (PRD-18631): listing events, free/busy across
    # mailboxes, creating and cancelling an event are four distinct Graph
    # endpoints with disjoint arguments — merging any pair would mean one tool
    # whose required params depend on a mode flag, which reads worse to an agent.
    assert len(names) <= 29, "tool count should stay within the SOP's <=20 guideline (+9 justified)"

    by_name = {t.name: t for t in tools}
    for name, expected_required in EXPECTED_TOOLS.items():
        tool = by_name[name]
        required = set(tool.inputSchema.get("required", []))
        assert required == expected_required, f"{name}: required={required}"
        assert tool.annotations is not None
        if name not in _NON_READ_ONLY:
            assert tool.annotations.readOnlyHint is True, f"{name}: expected readOnlyHint=True"
        assert len(tool.description or "") <= 500, f"{name}: description too long"
        first_line = (tool.description or "").strip().splitlines()[0]
        assert len(first_line) <= 100, f"{name}: first line too long: {first_line!r}"
        # No leaked implementation-detail lines like "API: GET /xxx".
        assert "API:" not in (tool.description or ""), f"{name}: leaked API detail in description"


@pytest.mark.asyncio
async def test_service_instructions_present_and_bounded():
    mcp = create_mcp_server(Settings())
    assert mcp.instructions
    assert len(mcp.instructions) <= 1500


@pytest.mark.parametrize(
    "status_code,expected_code,expected_retryable",
    [
        (0, "upstream_error", True),
        (400, "invalid_argument", False),
        (401, "unauthorized", False),
        (403, "unauthorized", False),
        (404, "not_found", False),
        (422, "invalid_argument", False),
        (429, "rate_limited", True),
        (500, "upstream_error", True),
        (503, "upstream_error", True),
    ],
)
def test_error_envelope_mapping(status_code, expected_code, expected_retryable):
    err = GraphError(status_code, "boom")
    envelope = json.loads(err.to_envelope())
    assert envelope["error"]["code"] == expected_code
    assert envelope["error"]["retryable"] is expected_retryable
    assert envelope["error"]["message"] == "boom"


class _CapturingClient:
    """Minimal GraphClient stand-in that records the request instead of making it."""

    def __init__(self, result: dict | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.headers: list[dict] = []
        self.bodies: list[dict] = []
        self.methods: list[str] = []
        self._result = result if result is not None else {"value": []}

    async def get(
        self,
        path: str,
        params: dict | None = None,
        extra_headers: dict | None = None,
    ) -> dict:
        self.methods.append("GET")
        self.calls.append((path, params or {}))
        self.headers.append(extra_headers or {})
        return self._result

    async def post(
        self, path: str, body: dict | None = None, extra_headers: dict | None = None
    ) -> dict:
        self.methods.append("POST")
        self.calls.append((path, {}))
        self.bodies.append(body or {})
        self.headers.append(extra_headers or {})
        return self._result

    async def delete(self, path: str) -> dict:
        self.methods.append("DELETE")
        self.calls.append((path, {}))
        return self._result


def _register(module) -> tuple[object, _CapturingClient]:
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(name="test")
    client = _CapturingClient()
    module.register(mcp, lambda: client)
    return mcp, client


@pytest.mark.asyncio
async def test_user_lookup_escapes_apostrophe():
    """An apostrophe in a UPN must be doubled, not left to close the OData literal
    early — o'brien@contoso.com is a real name shape, and unescaped it turns the
    whole $filter into a 400."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_check_user_exists", {"user_principal_name": "o'brien@contoso.com"})
    assert client.calls[0][1]["$filter"] == "userPrincipalName eq 'o''brien@contoso.com'"

    client.calls.clear()
    await mcp.call_tool("graph_check_user_exists", {"mail": "o'brien@contoso.com"})
    assert client.calls[0][1]["$filter"] == "mail eq 'o''brien@contoso.com'"


@pytest.mark.asyncio
async def test_group_search_escapes_apostrophe():
    from graph_mcp.tools import groups

    mcp, client = _register(groups)
    await mcp.call_tool("graph_list_groups", {"display_name": "Bob's Team", "exact": True})
    assert client.calls[0][1]["$filter"] == "displayName eq 'Bob''s Team'"

    client.calls.clear()
    await mcp.call_tool("graph_list_groups", {"display_name": "Bob's Team"})
    assert client.calls[0][1]["$filter"] == "startswith(displayName,'Bob''s Team')"


class _QueuedClient:
    """GraphClient stand-in returning pre-set results in call order — used
    where a tool makes more than one distinct request (e.g. a user-id
    lookup, a paginated /groups scan, then a per-group /owners check), so
    a single fixed result like _CapturingClient's isn't enough."""

    def __init__(self, results: list):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append((path, params or {}))
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_list_owned_groups_enumerates_groups_and_checks_owners():
    """graph_list_owned_groups has no app-only-safe, correctly-paginating
    query for "groups owned by user X" (see groups.py's comment — Graph's
    /groups/delta was tried and found to have a real pagination bug against
    a live tenant: it re-returns the same page instead of advancing). So it
    falls back to enumerating all of /groups (paginating via
    @odata.nextLink) and checking each group's /owners. This verifies that
    enumeration follows nextLink across pages, checks every group's
    owners, and correctly keeps only the ones owned by the target user
    with the right owner_count."""
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import groups

    mcp = FastMCP(name="test")
    client = _QueuedClient(
        [
            {"id": "user-123"},  # /users/{id} resolution
            {
                "value": [
                    {"id": "g1", "displayName": "Owned Solo"},
                    {"id": "g2", "displayName": "Not Owned"},
                ],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/groups?$skiptoken=page2",
            },
            {"value": [{"id": "g3", "displayName": "Owned Shared"}]},  # page 2, no nextLink
            {"value": [{"id": "user-123"}]},  # /groups/g1/owners
            {"value": [{"id": "someone-else"}]},  # /groups/g2/owners
            {"value": [{"id": "user-123"}, {"id": "other"}]},  # /groups/g3/owners
        ]
    )
    groups.register(mcp, lambda: client)

    _, structured = await mcp.call_tool("graph_list_owned_groups", {"user_id": "carl@contoso.com"})
    payload = json.loads(structured["result"])

    assert payload["count"] == 2
    assert {g["id"] for g in payload["groups"]} == {"g1", "g3"}
    owner_counts = {g["id"]: g["owner_count"] for g in payload["groups"]}
    assert owner_counts == {"g1": 1, "g3": 2}
    # not present in output — "Not Owned" (g2) was correctly excluded
    assert "g2" not in {g["id"] for g in payload["groups"]}

    # 6 calls: resolve user_id, 2 pages of /groups, then one /owners
    # check per group found (3 groups total across both pages).
    assert [c[0] for c in client.calls] == [
        "/users/carl@contoso.com",
        "/groups",
        "https://graph.microsoft.com/v1.0/groups?$skiptoken=page2",
        "/groups/g1/owners",
        "/groups/g2/owners",
        "/groups/g3/owners",
    ]


class _FailingPostClient:
    """Stand-in whose POST fails with a given status, so the cancel tool's
    fallback path can be exercised. DELETE succeeds."""

    def __init__(self, post_status: int):
        self._post_status = post_status
        self.calls: list[tuple[str, str]] = []

    async def post(self, path: str, body: dict | None = None, extra_headers: dict | None = None):
        self.calls.append(("POST", path))
        raise GraphError(self._post_status, "boom")

    async def delete(self, path: str):
        self.calls.append(("DELETE", path))
        return None


def _calendar_mcp(client) -> object:
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import calendar

    mcp = FastMCP(name="test")
    calendar.register(mcp, lambda: client)
    return mcp


@pytest.mark.asyncio
async def test_list_calendar_events_targets_user_and_sets_timezone_preference():
    """user_id must switch the path off /me (an app-only token has no /me), and
    the requested time zone has to ride along as a Prefer header, otherwise Graph
    answers in UTC no matter what the agent asked for."""
    client = _CapturingClient()
    mcp = _calendar_mcp(client)

    await mcp.call_tool(
        "graph_list_calendar_events",
        {
            "start_datetime": "2026-09-15T09:00:00",
            "end_datetime": "2026-09-15T18:00:00",
            "user_id": "alice@contoso.com",
            "timezone": "China Standard Time",
        },
    )
    path, params = client.calls[0]
    assert path == "/users/alice@contoso.com/calendarView"
    assert params["startDateTime"] == "2026-09-15T09:00:00"
    assert params["endDateTime"] == "2026-09-15T18:00:00"
    assert params["$top"] == 25
    assert params["$orderby"] == "start/dateTime"
    assert client.headers[0] == {"Prefer": 'outlook.timezone="China Standard Time"'}

    client.calls.clear()
    await mcp.call_tool(
        "graph_list_calendar_events",
        {"start_datetime": "2026-09-15T09:00:00", "end_datetime": "2026-09-15T18:00:00"},
    )
    assert client.calls[0][0] == "/me/calendarView"


@pytest.mark.asyncio
async def test_list_calendar_events_caps_limit_in_schema():
    mcp = create_mcp_server(Settings())
    tool = {t.name: t for t in await mcp.list_tools()}["graph_list_calendar_events"]
    assert tool.inputSchema["properties"]["limit"]["maximum"] == 200


@pytest.mark.asyncio
async def test_get_user_availability_splits_datetime_and_timezone():
    """getSchedule takes dateTimeTimeZone objects, not ISO strings — a flat
    string here is a 400 from Graph."""
    client = _CapturingClient()
    mcp = _calendar_mcp(client)

    await mcp.call_tool(
        "graph_get_user_availability",
        {
            "user_ids": ["alice@contoso.com", "bob@contoso.com"],
            "start_datetime": "2026-09-15T09:00:00",
            "end_datetime": "2026-09-15T18:00:00",
            "interval_minutes": 60,
            "caller_user_id": "alice@contoso.com",
        },
    )
    assert client.calls[0][0] == "/users/alice@contoso.com/calendar/getSchedule"
    assert client.bodies[0] == {
        "schedules": ["alice@contoso.com", "bob@contoso.com"],
        "startTime": {"dateTime": "2026-09-15T09:00:00", "timeZone": "UTC"},
        "endTime": {"dateTime": "2026-09-15T18:00:00", "timeZone": "UTC"},
        "availabilityViewInterval": 60,
    }


@pytest.mark.asyncio
async def test_get_user_availability_rejects_too_many_mailboxes():
    client = _CapturingClient()
    mcp = _calendar_mcp(client)

    _, structured = await mcp.call_tool(
        "graph_get_user_availability",
        {
            "user_ids": [f"u{i}@contoso.com" for i in range(21)],
            "start_datetime": "2026-09-15T09:00:00",
            "end_datetime": "2026-09-15T18:00:00",
        },
    )
    payload = json.loads(structured["result"])
    assert payload["error"]["code"] == "invalid_argument"
    assert client.calls == []  # rejected before any request went out


@pytest.mark.asyncio
async def test_create_calendar_event_builds_attendees_and_teams_meeting():
    client = _CapturingClient(result={"id": "evt-1", "webLink": "https://x", "onlineMeeting": {"joinUrl": "https://teams/x"}})
    mcp = _calendar_mcp(client)

    _, structured = await mcp.call_tool(
        "graph_create_calendar_event",
        {
            "subject": "Quarterly review",
            "start_datetime": "2026-09-15T14:00:00",
            "end_datetime": "2026-09-15T15:00:00",
            "attendees": ["alice@contoso.com"],
            "optional_attendees": ["bob@contoso.com"],
            "user_id": "carl@contoso.com",
            "is_online_meeting": True,
            "location": "Room 3",
        },
    )
    assert client.calls[0][0] == "/users/carl@contoso.com/events"
    body = client.bodies[0]
    assert body["attendees"] == [
        {"emailAddress": {"address": "alice@contoso.com"}, "type": "required"},
        {"emailAddress": {"address": "bob@contoso.com"}, "type": "optional"},
    ]
    assert body["start"] == {"dateTime": "2026-09-15T14:00:00", "timeZone": "UTC"}
    assert body["isOnlineMeeting"] is True
    assert body["onlineMeetingProvider"] == "teamsForBusiness"
    assert body["location"] == {"displayName": "Room 3"}
    # An event with no body/reminder must not send empty keys Graph would reject.
    assert "body" not in body and "reminderMinutesBeforeStart" not in body

    payload = json.loads(structured["result"])
    assert payload["event_id"] == "evt-1"
    assert payload["join_url"] == "https://teams/x"
    assert payload["attendees_invited"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("post_status", [400, 403])
async def test_cancel_calendar_event_falls_back_to_delete(post_status):
    """/cancel is organizer-only and rejects appointments with no attendees;
    the user still meant "get this off the calendar", so a rejected cancel
    must fall through to a delete rather than surfacing the 400."""
    client = _FailingPostClient(post_status)
    mcp = _calendar_mcp(client)

    _, structured = await mcp.call_tool(
        "graph_cancel_calendar_event", {"event_id": "evt-1", "user_id": "carl@contoso.com"}
    )
    payload = json.loads(structured["result"])
    assert payload == {"event_id": "evt-1", "cancelled": True, "method": "deleted"}
    assert client.calls == [
        ("POST", "/users/carl@contoso.com/events/evt-1/cancel"),
        ("DELETE", "/users/carl@contoso.com/events/evt-1"),
    ]


@pytest.mark.asyncio
async def test_cancel_calendar_event_is_idempotent_on_missing_event():
    client = _FailingPostClient(404)
    mcp = _calendar_mcp(client)

    _, structured = await mcp.call_tool("graph_cancel_calendar_event", {"event_id": "gone"})
    payload = json.loads(structured["result"])
    assert payload["cancelled"] is True and payload["method"] == "already_absent"
    assert client.calls == [("POST", "/me/events/gone/cancel")]  # no pointless delete


@pytest.mark.asyncio
async def test_cancel_calendar_event_surfaces_other_errors():
    client = _FailingPostClient(429)
    mcp = _calendar_mcp(client)

    _, structured = await mcp.call_tool("graph_cancel_calendar_event", {"event_id": "evt-1"})
    payload = json.loads(structured["result"])
    assert payload["error"]["code"] == "rate_limited"
    assert len(client.calls) == 1  # did not fall through to a delete
