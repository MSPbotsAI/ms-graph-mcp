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
    "graph_list_users": set(),
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
    "graph_remove_manager": {"user_id"},
    "graph_list_auth_methods": {"user_id"},
    "graph_revoke_sessions": {"user_id"},
    "graph_assign_groups": {"user_id", "group_ids"},
    "graph_list_user_groups": {"user_id"},
    "graph_list_groups": set(),
    "graph_remove_group_member": {"user_id", "group_ids"},
    "graph_list_owned_groups": {"user_id"},
    "graph_get_group": {"group_id"},
    "graph_list_group_members": {"group_id"},
    "graph_check_license_stock": set(),
    "graph_assign_license": {"user_id"},
    "graph_reprocess_license": {"user_id"},
    "graph_list_domains": set(),
    "graph_send_mail": {"to_recipients", "subject", "body"},
    "graph_list_messages": set(),
    "graph_get_message": {"message_id"},
    "graph_read_mail_attachment": {"message_id", "attachment_id"},
    "graph_search_sites": {"query"},
    "graph_list_drive_items": {"drive_id"},
    "graph_get_file": {"drive_id", "item_id"},
    "graph_read_file_text": {"drive_id", "item_id"},
    "graph_read_site_file_text": {"site_query", "file_name"},
    "graph_write_file_text": {"drive_id", "item_id", "content"},
    "graph_create_file_text": {"drive_id", "path", "content"},
    "graph_delete_file": {"drive_id", "item_id"},
    "graph_list_managed_devices": {"user_id"},
    "graph_remove_managed_device": {"device_id"},
    "graph_list_calendar_events": {"start_datetime", "end_datetime"},
    "graph_get_user_availability": {"user_ids", "start_datetime", "end_datetime"},
    "graph_create_calendar_event": {"subject", "start_datetime", "end_datetime"},
    "graph_cancel_calendar_event": {"event_id"},
    "graph_transfer_onedrive": {"source_user_id", "destination_user_id"},
    "graph_start_cloud_sync": {"user_id"},
}

# Tools that are not plain read-only queries (writes / mutations).
_NON_READ_ONLY = {
    "graph_create_user",
    "graph_reset_password",
    "graph_update_user",
    "graph_assign_manager",
    "graph_remove_manager",
    "graph_revoke_sessions",
    "graph_assign_groups",
    "graph_remove_group_member",
    "graph_assign_license",
    "graph_reprocess_license",
    "graph_send_mail",
    "graph_write_file_text",
    "graph_create_file_text",
    "graph_delete_file",
    "graph_remove_managed_device",
    "graph_create_calendar_event",
    "graph_cancel_calendar_event",
    "graph_transfer_onedrive",
    "graph_start_cloud_sync",
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
    # +1 for graph_list_users (PRD-19290): the user tools could all only act on
    # a user you had already identified exactly — no way to enumerate or
    # prefix-search the directory, which is a different question from
    # graph_check_user_exists's "does this exact UPN exist".
    # +2 for the mail read pair (PRD-19103): the mail domain could only SEND.
    # Listing and opening stay separate because a list must not carry bodies —
    # that is the whole point of the split — and folding them into one tool
    # whose return shape flips on whether message_id was passed is exactly the
    # mode-flag shape the calendar note above rejects.
    # +1 for graph_read_mail_attachment: the mail flow listed attachments but
    # could not open one, so a spreadsheet emailed in was a dead end. It stays
    # separate from graph_get_message because it returns file content, not a
    # message, and pages by row.
    # +3 for the W2 onboarding gap list (PRD-20219): graph_list_domains (pick
    # the UPN domain), graph_get_group (verify a template user's group id
    # before assigning it) and graph_reprocess_license (unstick a failed
    # group-based license). Each is a distinct endpoint none of the existing
    # tools can reach without a mode flag.
    # +1 for graph_list_group_members (PRD-20223, Ginko parity): the group
    # tools only walked membership from the user side; nothing listed who is
    # in a given group. proxyAddresses lookup was added as a parameter on
    # graph_check_user_exists, not a new tool — same question, another key.
    # +1 for graph_remove_manager (PRD-20393): offboarding needed to clear a
    # manager without assigning a replacement; graph_assign_manager's
    # manager_id stays required rather than becoming an optional "mode flag"
    # (see the calendar/mail notes above).
    # +1 for graph_read_site_file_text (PRD-20413): collapses the three-call
    # site -> drive -> file chain into one call so the model never has to
    # copy a driveId between calls — the id-copying is what caused the
    # reopened PRD-20211 intermittent "malformed drive id" failure.
    # +2 for the offboarding transfer/sync pair (PRD-20422):
    # graph_transfer_onedrive and graph_start_cloud_sync are disjoint Graph
    # operations (drive copy vs. synchronization-job start) with no shared
    # mode flag; deferred from PRD-20393 items 7-8 pending Ginko
    # confirmation of production behavior.
    assert len(names) <= 41, "tool count should stay within SOP's <=20 guideline (+21 justified)"

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

    async def patch(self, path: str, body: dict | None = None) -> dict:
        self.methods.append("PATCH")
        self.calls.append((path, {}))
        self.bodies.append(body or {})
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
async def test_list_users_builds_filter_and_caps_results():
    """display_name and account_enabled must combine into one `and` filter, the
    apostrophe must be escaped like every other OData literal, and max_results
    must be clamped to the 200 hard cap rather than passed through to $top."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_list_users", {"display_name": "O'Brien"})
    assert client.calls[0][0] == "/users"
    assert client.calls[0][1]["$filter"] == "startswith(displayName,'O''Brien')"

    client.calls.clear()
    await mcp.call_tool("graph_list_users", {"display_name": "Alice", "exact": True})
    assert client.calls[0][1]["$filter"] == "displayName eq 'Alice'"

    client.calls.clear()
    await mcp.call_tool("graph_list_users", {"display_name": "Alice", "account_enabled": False})
    assert (
        client.calls[0][1]["$filter"]
        == "startswith(displayName,'Alice') and accountEnabled eq false"
    )

    # No arguments at all => plain listing, no $filter key.
    client.calls.clear()
    await mcp.call_tool("graph_list_users", {})
    assert "$filter" not in client.calls[0][1]

    # Over the hard cap: $top must reflect the clamped 200, not the 5000 asked for.
    client.calls.clear()
    await mcp.call_tool("graph_list_users", {"max_results": 5000})
    assert client.calls[0][1]["$top"] == "200"


@pytest.mark.asyncio
async def test_list_users_stops_paginating_at_max_results():
    """A tenant with more users than max_results must come back truncated with
    has_more=true, not walk every page of the directory."""
    from graph_mcp.tools import users

    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(name="test")
    client = _QueuedClient(
        [
            {
                "value": [{"id": f"u{i}"} for i in range(50)],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=page2",
            },
            {"value": [{"id": f"v{i}"} for i in range(50)]},
        ]
    )
    users.register(mcp, lambda: client)

    _, structured = await mcp.call_tool("graph_list_users", {"max_results": 10})
    payload = json.loads(structured["result"])

    assert payload["count"] == 10
    assert payload["has_more"] is True
    # Only the first page was fetched — pagination stopped once the cap was hit.
    assert len(client.calls) == 1


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


def _mail_mcp(result: dict | None = None):
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import mail

    mcp = FastMCP(name="test")
    client = _CapturingClient(result)
    mail.register(mcp, lambda: client)
    return mcp, client


@pytest.mark.asyncio
async def test_list_messages_builds_filter_and_defaults_to_inbox():
    mcp, client = _mail_mcp()

    await mcp.call_tool(
        "graph_list_messages",
        {
            "from_address": "o'brien@contoso.com",
            "received_after": "2026-09-01T00:00:00Z",
            "unread_only": True,
            "limit": 5,
        },
    )
    path, params = client.calls[0]
    assert path == "/me/mailFolders/inbox/messages"
    assert params["$filter"] == (
        "from/emailAddress/address eq 'o''brien@contoso.com' and "
        "receivedDateTime ge 2026-09-01T00:00:00Z and isRead eq false"
    )
    assert params["$orderby"] == "receivedDateTime desc"
    assert params["$top"] == 5
    # A list must never carry bodies — that is what graph_get_message is for.
    assert "body" not in params["$select"].split(",")


@pytest.mark.asyncio
async def test_list_messages_across_all_folders_for_a_thread():
    mcp, client = _mail_mcp()

    await mcp.call_tool(
        "graph_list_messages", {"folder": None, "conversation_id": "AAQk=", "user_id": "glenn@x.ai"}
    )
    path, params = client.calls[0]
    assert path == "/users/glenn@x.ai/messages"
    assert params["$filter"] == "conversationId eq 'AAQk='"


@pytest.mark.asyncio
async def test_list_messages_rejects_search_combined_with_filters():
    """Graph rejects $search alongside $filter on messages with a 400 whose text
    explains nothing. Catching it here costs one round trip less and tells the
    agent what to do instead."""
    mcp, client = _mail_mcp()

    _, structured = await mcp.call_tool(
        "graph_list_messages", {"search": "renewal", "unread_only": True}
    )
    payload = json.loads(structured["result"])
    assert payload["error"]["code"] == "invalid_argument"
    assert "unread_only" in payload["error"]["message"]
    assert client.calls == []  # never reached Graph


@pytest.mark.asyncio
async def test_list_messages_search_quotes_term_and_drops_orderby():
    mcp, client = _mail_mcp()

    await mcp.call_tool("graph_list_messages", {"search": 'say "hi" to contoso'})
    _, params = client.calls[0]
    # An embedded quote would close the search literal early.
    assert params["$search"] == '"say  hi  to contoso"'
    assert "$orderby" not in params  # not accepted alongside $search


@pytest.mark.asyncio
async def test_list_messages_rejects_malformed_datetime():
    mcp, client = _mail_mcp()

    _, structured = await mcp.call_tool("graph_list_messages", {"received_after": "last Tuesday"})
    payload = json.loads(structured["result"])
    assert payload["error"]["code"] == "invalid_argument"
    assert client.calls == []


@pytest.mark.asyncio
async def test_get_message_truncates_a_huge_body():
    """A single message has no list field for dump_json_capped to trim, so an
    untruncated 400KB thread would be discarded wholesale and replaced by a
    'too large' notice. The head of the message is what the agent actually
    needs."""
    from graph_mcp.tools.mail import _MAX_BODY_CHARS

    long_body = "x" * (_MAX_BODY_CHARS + 5_000)
    mcp, client = _mail_mcp(
        {"id": "m1", "subject": "Re: renewal", "body": {"contentType": "text", "content": long_body}}
    )

    _, structured = await mcp.call_tool("graph_get_message", {"message_id": "m1"})
    payload = json.loads(structured["result"])
    body = payload["message"]["body"]
    assert body["truncated"] is True
    assert body["original_length"] == len(long_body)
    assert len(body["content"]) == _MAX_BODY_CHARS
    assert payload["message"]["subject"] == "Re: renewal"
    # Plain text by default: HTML costs several times the tokens for the same words.
    assert client.headers[0]["Prefer"] == 'outlook.body-content-type="text"'


@pytest.mark.asyncio
async def test_get_message_attachments_never_pull_content_bytes():
    mcp, client = _mail_mcp({"id": "m1", "value": [{"id": "a1", "name": "quote.pdf"}]})

    await mcp.call_tool(
        "graph_get_message", {"message_id": "m1", "include_attachments": True, "body_format": "html"}
    )
    assert client.calls[1][0] == "/me/messages/m1/attachments"
    select = client.calls[1][1]["$select"]
    assert "contentBytes" not in select
    assert client.headers[0]["Prefer"] == 'outlook.body-content-type="html"'


def _b64(n: int, fill: bytes = b"a") -> str:
    import base64

    return base64.b64encode(fill * n).decode()


async def _send(mcp, **extra):
    args = {"to_recipients": ["bob@contoso.com"], "subject": "s", "body": "b", **extra}
    _, structured = await mcp.call_tool("graph_send_mail", args)
    return json.loads(structured["result"])


@pytest.mark.asyncio
async def test_send_mail_attaches_one_file_correctly():
    mcp, client = _mail_mcp({})
    content = _b64(10)
    result = await _send(
        mcp,
        attachments=[{"filename": "quote.pdf", "content_type": "application/pdf", "content_base64": content}],
    )
    assert result == {"status": "sent"}
    assert client.calls[0][0] == "/me/sendMail"
    assert client.bodies[0]["message"]["attachments"] == [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": "quote.pdf",
            "contentType": "application/pdf",
            "contentBytes": content,
        }
    ]


@pytest.mark.asyncio
async def test_send_mail_attaches_multiple_files_up_to_limit():
    mcp, client = _mail_mcp({})
    atts = [{"filename": f"f{i}.txt", "content_base64": _b64(100, bytes([65 + i]))} for i in range(5)]
    await _send(mcp, attachments=atts)
    sent = client.bodies[0]["message"]["attachments"]
    assert [a["name"] for a in sent] == [a["filename"] for a in atts]
    assert [a["contentBytes"] for a in sent] == [a["content_base64"] for a in atts]
    # No content_type given -> guessed from the extension.
    assert all(a["contentType"] == "text/plain" for a in sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, []])
async def test_send_mail_without_attachments_matches_prior_payload_shape(value):
    mcp, client = _mail_mcp({})
    await _send(mcp)
    await _send(mcp, attachments=value)
    expected = {
        "message": {
            "subject": "s",
            "body": {"contentType": "Text", "content": "b"},
            "toRecipients": [{"emailAddress": {"address": "bob@contoso.com"}}],
        },
        "saveToSentItems": True,
    }
    assert client.bodies == [expected, expected]


@pytest.mark.asyncio
async def test_send_mail_rejects_invalid_base64_attachment():
    mcp, client = _mail_mcp({})
    result = await _send(mcp, attachments=[{"filename": "bad.bin", "content_base64": "not base64!!"}])
    assert result["error"]["code"] == "invalid_argument"
    assert "bad.bin" in result["error"]["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_send_mail_rejects_attachment_over_500kb():
    mcp, client = _mail_mcp({})
    result = await _send(mcp, attachments=[{"filename": "big.pdf", "content_base64": _b64(500_001)}])
    assert result["error"]["code"] == "invalid_argument"
    assert "per-file" in result["error"]["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_send_mail_rejects_more_than_5_attachments():
    mcp, client = _mail_mcp({})
    atts = [{"filename": f"f{i}.txt", "content_base64": _b64(1)} for i in range(6)]
    result = await _send(mcp, attachments=atts)
    assert result["error"]["code"] == "invalid_argument"
    assert "5 attachments max" in result["error"]["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_send_mail_allows_attachment_at_exactly_500kb_boundary():
    mcp, client = _mail_mcp({})
    result = await _send(mcp, attachments=[{"filename": "edge.bin", "content_base64": _b64(500_000)}])
    assert result == {"status": "sent"}
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_send_mail_rejects_attachment_missing_filename():
    mcp, client = _mail_mcp({})
    result = await _send(mcp, attachments=[{"content_base64": _b64(1)}])
    assert result["error"]["code"] == "invalid_argument"
    assert "'filename'" in result["error"]["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_send_mail_attachments_do_not_leak_across_calls():
    mcp1, client1 = _mail_mcp({})
    mcp2, client2 = _mail_mcp({})
    first = _b64(10, b"x")
    await _send(mcp1, attachments=[{"filename": "one.txt", "content_base64": first}])
    await _send(mcp2, attachments=[{"filename": "two.txt", "content_base64": _b64(10, b"y")}])
    assert first not in json.dumps(client2.bodies)
    assert [a["name"] for a in client2.bodies[0]["message"]["attachments"]] == ["two.txt"]


@pytest.mark.asyncio
async def test_send_mail_rejects_attachments_over_700kb_total():
    mcp, client = _mail_mcp({})
    atts = [{"filename": f"p{i}.bin", "content_base64": _b64(350_001)} for i in range(2)]
    result = await _send(mcp, attachments=atts)
    assert result["error"]["code"] == "invalid_argument"
    assert "combined" in result["error"]["message"]
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variant",
    [
        "mime_wrapped",
        "data_uri",
        "url_safe",
        "no_padding",
        "surrounding_whitespace",
    ],
)
async def test_send_mail_accepts_common_base64_variants(variant):
    # PRD-20416: agents hand over base64 from `base64` CLI (76-col wrapped),
    # data: URIs, the URL-safe alphabet or with padding dropped. All decode to
    # the same bytes, so the mail must go out with standard base64.
    import base64

    raw = bytes(range(256)) * 40 + b"<html>\xe6\x8a\xa5\xe5\x91\x8a</html>?>"
    std = base64.b64encode(raw).decode()
    value = {
        "mime_wrapped": base64.encodebytes(raw).decode(),
        "data_uri": "data:text/html;base64," + std,
        "url_safe": base64.urlsafe_b64encode(raw).decode(),
        "no_padding": std.rstrip("="),
        "surrounding_whitespace": f"\n  {std}  \r\n",
    }[variant]
    assert value != std
    mcp, client = _mail_mcp({})
    result = await _send(mcp, attachments=[{"filename": "r.html", "content_base64": value}])
    assert result == {"status": "sent"}
    sent = client.bodies[0]["message"]["attachments"][0]
    assert sent["contentBytes"] == std
    assert sent["contentType"] == "text/html"


@pytest.mark.asyncio
async def test_send_mail_encodes_content_text_attachment():
    import base64

    html = "<html><body><h1>月度报告</h1></body></html>"
    mcp, client = _mail_mcp({})
    result = await _send(mcp, attachments=[{"filename": "report.html", "content_text": html}])
    assert result == {"status": "sent"}
    sent = client.bodies[0]["message"]["attachments"][0]
    assert base64.b64decode(sent["contentBytes"], validate=True).decode("utf-8") == html
    assert sent["contentType"] == "text/html"
    assert sent["name"] == "report.html"


@pytest.mark.asyncio
async def test_send_mail_content_text_counts_utf8_bytes_against_limit():
    mcp, client = _mail_mcp({})
    # 166,667 three-byte chars = 500,001 bytes > 500 KB, though under 500k chars.
    result = await _send(mcp, attachments=[{"filename": "big.txt", "content_text": "报" * 166_667}])
    assert result["error"]["code"] == "invalid_argument"
    assert "per-file" in result["error"]["message"]
    assert client.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "att",
    [
        {"filename": "x.txt"},
        {"filename": "x.txt", "content_text": "a", "content_base64": "YQ=="},
        {"filename": "x.txt", "content_text": ""},
    ],
)
async def test_send_mail_requires_exactly_one_content_field(att):
    mcp, client = _mail_mcp({})
    result = await _send(mcp, attachments=[att])
    assert result["error"]["code"] == "invalid_argument"
    assert "exactly one of 'content_base64' or 'content_text'" in result["error"]["message"]
    assert client.calls == []


@pytest.mark.asyncio
async def test_send_mail_invalid_base64_points_to_content_text():
    mcp, client = _mail_mcp({})
    result = await _send(
        mcp, attachments=[{"filename": "r.html", "content_base64": "<html>raw</html>"}]
    )
    assert result["error"]["code"] == "invalid_argument"
    assert "content_text" in result["error"]["message"]
    assert client.calls == []


class _AttachmentClient(_CapturingClient):
    def __init__(self, meta: dict, data: bytes = b""):
        super().__init__(meta)
        self.data = data
        self.content_paths: list[str] = []

    async def get_content(self, path: str) -> bytes:
        self.content_paths.append(path)
        return self.data


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    import io

    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in sheets.items():
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def _read_attachment(meta: dict, data: bytes = b"", **args):
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import mail

    mcp = FastMCP(name="test")
    client = _AttachmentClient(meta, data)
    mail.register(mcp, lambda: client)
    _, structured = await mcp.call_tool(
        "graph_read_mail_attachment", {"message_id": "m1", "attachment_id": "a1", **args}
    )
    return json.loads(structured["result"]), client


def _file_meta(name: str, size: int = 1000) -> dict:
    return {"@odata.type": "#microsoft.graph.fileAttachment", "name": name, "size": size}


@pytest.mark.asyncio
async def test_read_attachment_parses_xlsx_rows():
    import datetime as dt

    data = _xlsx(
        {
            "Summary": [["Client", "Hours", "Date"], ["Contoso", 12.5, dt.date(2026, 10, 1)]],
            "Raw": [["x"]],
        }
    )
    payload, client = await _read_attachment(_file_meta("report.xlsx", len(data)), data)
    assert payload["sheets"] == ["Summary", "Raw"]
    assert payload["sheet"] == "Summary"
    assert payload["rows"][0] == ["Client", "Hours", "Date"]
    assert payload["rows"][1][:2] == ["Contoso", 12.5]
    assert payload["rows"][1][2].startswith("2026-10-01")
    assert payload["total_rows"] == 2 and payload["has_more"] is False
    # Metadata first without contentBytes, then the raw $value stream.
    assert "contentBytes" not in client.calls[0][1]["$select"]
    assert client.content_paths == ["/me/messages/m1/attachments/a1/$value"]


@pytest.mark.asyncio
async def test_read_attachment_picks_sheet_and_pages():
    data = _xlsx({"A": [["h"]], "B": [["h"]] + [[i] for i in range(1, 11)]})
    payload, _ = await _read_attachment(
        _file_meta("r.xlsx"), data, sheet="B", start_row=4, max_rows=3, user_id="u@x.ai"
    )
    assert payload["sheet"] == "B"
    assert payload["rows"] == [[3], [4], [5]]
    assert payload["total_rows"] == 11 and payload["has_more"] is True

    payload, _ = await _read_attachment(_file_meta("r.xlsx"), data, sheet="Nope")
    assert payload["error"]["code"] == "invalid_argument"
    assert "A" in payload["error"]["message"] and "B" in payload["error"]["message"]


@pytest.mark.asyncio
async def test_read_attachment_shrinks_page_to_fit_and_keeps_counters_true():
    from graph_mcp._json import MAX_CHARS

    wide = [["y" * 250] * 4 for _ in range(200)]
    data = _xlsx({"S": wide})
    payload, _ = await _read_attachment(_file_meta("big.xlsx"), data)
    n = payload["returned_rows"]
    assert 0 < n < 200
    assert len(payload["rows"]) == n
    assert payload["has_more"] is True
    assert len(json.dumps(payload, separators=(",", ":"), ensure_ascii=False)) <= MAX_CHARS


@pytest.mark.asyncio
async def test_read_attachment_csv_and_text():
    payload, _ = await _read_attachment(
        _file_meta("t.csv"), "\ufeffname,qty\n\nwidget,3\n".encode()
    )
    assert payload["rows"] == [["name", "qty"], ["widget", "3"]]

    payload, _ = await _read_attachment(_file_meta("notes.txt"), "hello 你好".encode())
    assert payload["content"] == "hello 你好"


@pytest.mark.asyncio
async def test_read_attachment_rejects_unsupported_before_downloading():
    for meta in (
        _file_meta("contract.pdf"),
        _file_meta("huge.xlsx", 50 * 1024 * 1024),
        {"@odata.type": "#microsoft.graph.itemAttachment", "name": "Fwd: hi", "size": 10},
    ):
        payload, client = await _read_attachment(meta, b"")
        assert payload["error"]["code"] == "invalid_argument", meta
        assert client.content_paths == []


@pytest.mark.asyncio
async def test_read_attachment_reports_corrupt_xlsx():
    payload, _ = await _read_attachment(_file_meta("bad.xlsx"), b"PK\x03\x04 truncated zip")
    assert payload["error"]["code"] == "invalid_argument"


def _xls(rows: list[list]) -> bytes:
    import io

    import xlwt

    book = xlwt.Workbook()
    ws = book.add_sheet("Data")
    date_style = xlwt.easyxf(num_format_str="YYYY-MM-DD")
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if hasattr(value, "year"):
                ws.write(r, c, value, date_style)
            else:
                ws.write(r, c, value)
    buf = io.BytesIO()
    book.save(buf)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_read_attachment_parses_legacy_xls():
    import datetime as dt

    data = _xls([["Client", "Qty", "Date"], ["Contoso", 3, dt.datetime(2026, 10, 1)]])
    payload, _ = await _read_attachment(_file_meta("old.xls"), data)
    assert payload["format"] == "xls"
    assert payload["sheets"] == ["Data"]
    assert payload["rows"][0] == ["Client", "Qty", "Date"]
    # Whole numbers stay ints (xlrd hands back 3.0) and dates are not raw serials.
    assert payload["rows"][1] == ["Contoso", 3, "2026-10-01T00:00:00"]


@pytest.mark.asyncio
async def test_read_attachment_detects_format_from_content_not_name():
    # An .xlsx renamed .xls, and one with no extension at all.
    data = _xlsx({"S": [["a", "b"]]})
    for name in ("renamed.xls", "export"):
        payload, _ = await _read_attachment(_file_meta(name), data)
        assert payload["format"] == "xlsx" and payload["rows"] == [["a", "b"]], name

    # A real .xls named .xlsx.
    payload, _ = await _read_attachment(_file_meta("wrong.xlsx"), _xls([["x", 1]]))
    assert payload["format"] == "xls" and payload["rows"] == [["x", 1]]

    # Reporting systems often export tab-separated text as .xls.
    payload, _ = await _read_attachment(_file_meta("report.xls"), b"name\tqty\nwidget\t3\n")
    assert payload["format"] == "csv"
    assert payload["rows"] == [["name", "qty"], ["widget", "3"]]

    # ...or an HTML table; returned as text rather than mangled into rows.
    payload, _ = await _read_attachment(_file_meta("report.xls"), b"<html><table></table></html>")
    assert payload["format"] == "text"


@pytest.mark.asyncio
async def test_read_attachment_rejects_ole_file_that_is_not_xls():
    ole_garbage = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 600
    payload, _ = await _read_attachment(_file_meta("x.xls"), ole_garbage)
    assert payload["error"]["code"] == "invalid_argument"


# --- PRD-20219: W2 onboarding gaps -------------------------------------------

_PROFILE_ARGS = {
    "office_location": "HQ 3F",
    "business_phones": ["+1 555 0100"],
    "mobile_phone": "+1 555 0199",
    "street_address": "1 Main St",
    "city": "Springfield",
    "state": "IL",
    "postal_code": "62701",
    "company_name": "Acme",
    "employee_hire_date": "2026-10-20",
}

_BASE_CREATE_ARGS = {
    "display_name": "Alice Smith",
    "user_principal_name": "alice@contoso.com",
    "mail_nickname": "alice",
    "password": "Tmp-Pa55word!",
    "usage_location": "US",
}


@pytest.mark.asyncio
async def test_create_user_sends_address_phone_company_and_hire_date():
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_create_user", {**_BASE_CREATE_ARGS, **_PROFILE_ARGS})

    assert client.calls[0][0] == "/users"
    body = client.bodies[0]
    assert body["officeLocation"] == "HQ 3F"
    assert body["businessPhones"] == ["+1 555 0100"]
    assert body["mobilePhone"] == "+1 555 0199"
    assert body["streetAddress"] == "1 Main St"
    assert body["city"] == "Springfield"
    assert body["state"] == "IL"
    assert body["postalCode"] == "62701"
    assert body["companyName"] == "Acme"
    # Graph rejects a bare date for this DateTimeOffset field.
    assert body["employeeHireDate"] == "2026-10-20T00:00:00Z"


@pytest.mark.asyncio
async def test_create_user_without_new_fields_keeps_prior_body_shape():
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_create_user", _BASE_CREATE_ARGS)

    assert set(client.bodies[0]) == {
        "displayName",
        "userPrincipalName",
        "mailNickname",
        "accountEnabled",
        "usageLocation",
        "passwordProfile",
    }


@pytest.mark.asyncio
async def test_create_user_rejects_malformed_hire_date_without_calling_graph():
    from graph_mcp.tools import users

    mcp, client = _register(users)
    result = await mcp.call_tool(
        "graph_create_user", {**_BASE_CREATE_ARGS, "employee_hire_date": "20/10/2026"}
    )

    assert client.calls == []
    assert json.loads(result[0][0].text)["error"]["code"] == "invalid_argument"


# --- PRD-20412: confirm forced password-change at first sign-in -------------


@pytest.mark.asyncio
async def test_create_user_response_confirms_forced_password_change():
    """AC1: Graph's own 201 response never includes passwordProfile (its
    documented default property set omits it), so without an explicit
    confirmation field the caller has no way to tell the account was
    created with a forced password change — even though the body we sent
    already requested it."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    result = await mcp.call_tool("graph_create_user", _BASE_CREATE_ARGS)
    payload = json.loads(result[0][0].text)

    assert payload["passwordChangeRequiredAtNextSignIn"] is True


@pytest.mark.asyncio
async def test_create_user_description_documents_forced_password_change():
    """AC2: a caller that only reads the tool definition (no source access)
    must be able to tell every created account requires a password change
    at next sign-in, and that this cannot be turned off per call."""
    mcp = create_mcp_server(Settings())
    tools = {t.name: t for t in await mcp.list_tools()}
    description = " ".join((tools["graph_create_user"].description or "").lower().split())

    assert "password" in description
    assert "next sign-in" in description
    assert "not configurable" in description


@pytest.mark.asyncio
async def test_create_user_error_envelope_has_no_password_confirmation():
    """AC3: an account that was never created cannot have had any setting
    take effect on it — the confirmation field must not leak into the
    error path."""
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import users

    client = _FailingPostClient(400)
    mcp = FastMCP(name="test")
    users.register(mcp, lambda: client)

    result = await mcp.call_tool("graph_create_user", _BASE_CREATE_ARGS)
    payload = json.loads(result[0][0].text)

    assert "error" in payload
    assert "passwordChangeRequiredAtNextSignIn" not in payload


@pytest.mark.asyncio
async def test_create_user_response_is_additive_only():
    """AC4: every field present in today's response must still be present,
    unchanged — the confirmation field is additive only, nothing is
    renamed or removed."""
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import users

    original_result = {
        "id": "87d349ed-44d7-43e1-9a83-5f2406dee5bd",
        "displayName": "Alice Smith",
        "userPrincipalName": "alice@contoso.com",
        "mail": "alice@contoso.com",
    }
    client = _CapturingClient(result=original_result)
    mcp = FastMCP(name="test")
    users.register(mcp, lambda: client)

    result = await mcp.call_tool("graph_create_user", _BASE_CREATE_ARGS)
    payload = json.loads(result[0][0].text)

    for key, value in original_result.items():
        assert payload[key] == value
    assert payload["passwordChangeRequiredAtNextSignIn"] is True
    assert set(payload) == set(original_result) | {"passwordChangeRequiredAtNextSignIn"}


@pytest.mark.asyncio
async def test_create_user_confirmation_is_per_call_not_shared_state():
    """AC5: two consecutive calls with different tokens/passwords/names must
    not leak into each other's response — the merge must build a fresh dict
    per call, never touch shared/module-level state."""
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import users

    client_a = _CapturingClient(result={"id": "user-a", "displayName": "Alice Smith"})
    mcp_a = FastMCP(name="test-a")
    users.register(mcp_a, lambda: client_a)

    client_b = _CapturingClient(result={"id": "user-b", "displayName": "Bob Jones"})
    mcp_b = FastMCP(name="test-b")
    users.register(mcp_b, lambda: client_b)

    result_a = await mcp_a.call_tool(
        "graph_create_user", {**_BASE_CREATE_ARGS, "display_name": "Alice Smith"}
    )
    result_b = await mcp_b.call_tool(
        "graph_create_user",
        {
            **_BASE_CREATE_ARGS,
            "display_name": "Bob Jones",
            "user_principal_name": "bob@contoso.com",
            "mail_nickname": "bob",
            "password": "Other-Pa55word!",
        },
    )

    payload_a = json.loads(result_a[0][0].text)
    payload_b = json.loads(result_b[0][0].text)

    assert payload_a == {
        "id": "user-a",
        "displayName": "Alice Smith",
        "passwordChangeRequiredAtNextSignIn": True,
    }
    assert payload_b == {
        "id": "user-b",
        "displayName": "Bob Jones",
        "passwordChangeRequiredAtNextSignIn": True,
    }
    # each call's own body stayed its own, too.
    assert client_a.bodies[0]["userPrincipalName"] == "alice@contoso.com"
    assert client_b.bodies[0]["userPrincipalName"] == "bob@contoso.com"


@pytest.mark.asyncio
async def test_update_user_patches_only_the_given_profile_field():
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_update_user", {"user_id": "u1", "street_address": "2 Elm St"})

    assert client.methods == ["PATCH"]
    assert client.calls[0][0] == "/users/u1"
    assert client.bodies[0] == {"streetAddress": "2 Elm St"}


@pytest.mark.asyncio
async def test_update_user_keeps_full_iso_hire_date():
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool(
        "graph_update_user",
        {"user_id": "u1", "employee_hire_date": "2026-10-20T09:00:00+08:00", "company_name": "Acme"},
    )

    assert client.bodies[0] == {
        "employeeHireDate": "2026-10-20T09:00:00+08:00",
        "companyName": "Acme",
    }


@pytest.mark.asyncio
async def test_update_user_writes_employee_id():
    """PRD-20321 / AC1: graph_update_user(employee_id=...) must PATCH employeeId."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_update_user", {"user_id": "u1", "employee_id": "E12345"})

    assert client.methods == ["PATCH"]
    assert client.bodies[0] == {"employeeId": "E12345"}


@pytest.mark.asyncio
async def test_update_user_clears_employee_id():
    """PRD-20321 / AC2: clear_employee_id=True must PATCH employeeId to null,
    not merely omit it (omitting it would leave the existing value untouched)."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_update_user", {"user_id": "u1", "clear_employee_id": True})

    assert client.methods == ["PATCH"]
    assert client.bodies[0] == {"employeeId": None}


@pytest.mark.asyncio
async def test_update_user_rejects_employee_id_and_clear_together():
    """PRD-20321 / AC3: passing both is ambiguous -- must error before calling Graph."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    result = await mcp.call_tool(
        "graph_update_user",
        {"user_id": "u1", "employee_id": "E12345", "clear_employee_id": True},
    )

    assert client.calls == []
    assert json.loads(result[0][0].text)["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_get_user_returns_given_name_and_surname():
    """PRD-20321 / AC4: givenName/surname (and employeeId, AC1/AC2's read side)
    must be requested from Graph and passed through, confirming the gap was
    connector-side ($select never asked for them), not account-data-side."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    client._result = {
        "id": "u1",
        "givenName": "Alice",
        "surname": "Smith",
        "employeeId": "E12345",
    }
    result = json.loads((await mcp.call_tool("graph_get_user", {"user_id": "u1"}))[0][0].text)

    assert result["givenName"] == "Alice"
    assert result["surname"] == "Smith"
    assert result["employeeId"] == "E12345"
    select = client.calls[0][1]["$select"]
    assert "givenName" in select
    assert "surname" in select
    assert "employeeId" in select


# --- PRD-20393: offboarding Graph interface changes -------------------------


@pytest.mark.asyncio
async def test_list_user_groups_paginates_with_skiptoken():
    """AC1/TC1: chaining next_page_token across calls must return every
    group exactly once and terminate with has_more=false, next_page_token=null."""
    from graph_mcp.tools import groups

    mcp, client = _register(groups)
    client._result = {
        "value": [{"id": "g1", "groupTypes": [], "mailEnabled": False}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/users/u1/memberOf?$skiptoken=P1",
    }
    first = json.loads(
        (await mcp.call_tool("graph_list_user_groups", {"user_id": "u1"}))[0][0].text
    )
    assert first["has_more"] is True
    assert first["next_page_token"] == "P1"
    assert [g["id"] for g in first["groups"]] == ["g1"]

    client._result = {"value": [{"id": "g2", "groupTypes": [], "mailEnabled": False}]}
    second = json.loads(
        (
            await mcp.call_tool("graph_list_user_groups", {"user_id": "u1", "page_token": "P1"})
        )[0][0].text
    )
    assert second["has_more"] is False
    assert second["next_page_token"] is None
    assert [g["id"] for g in second["groups"]] == ["g2"]
    assert client.calls[1][1]["$skiptoken"] == "P1"


@pytest.mark.asyncio
async def test_list_user_groups_returns_group_type_mail_enabled_and_sync_source():
    """AC2/TC2: every group entry must report groupTypes, mailEnabled and a
    sync_source derived from onPremisesSyncEnabled."""
    from graph_mcp.tools import groups

    mcp, client = _register(groups)
    client._result = {
        "value": [
            {
                "id": "g1",
                "groupTypes": ["Unified"],
                "mailEnabled": True,
                "onPremisesSyncEnabled": True,
            },
            {"id": "g2", "groupTypes": [], "mailEnabled": False, "onPremisesSyncEnabled": None},
        ]
    }
    result = json.loads(
        (await mcp.call_tool("graph_list_user_groups", {"user_id": "u1"}))[0][0].text
    )

    by_id = {g["id"]: g for g in result["groups"]}
    assert by_id["g1"]["sync_source"] == "on-premises"
    assert by_id["g1"]["mailEnabled"] is True
    assert by_id["g1"]["groupTypes"] == ["Unified"]
    assert by_id["g2"]["sync_source"] == "cloud"
    assert "onPremisesSyncEnabled" in client.calls[0][1]["$select"]


@pytest.mark.asyncio
async def test_list_groups_paginates_with_skiptoken():
    """AC3/TC3: graph_list_groups must follow the same one-page-per-call
    pagination contract as graph_list_user_groups/graph_list_group_members."""
    from graph_mcp.tools import groups

    mcp, client = _register(groups)
    client._result = {
        "value": [{"id": "g1"}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/groups?$skiptoken=P1",
    }
    first = json.loads((await mcp.call_tool("graph_list_groups", {}))[0][0].text)
    assert first["has_more"] is True
    assert first["next_page_token"] == "P1"

    client._result = {"value": [{"id": "g2"}]}
    second = json.loads(
        (await mcp.call_tool("graph_list_groups", {"page_token": "P1"}))[0][0].text
    )
    assert second["has_more"] is False
    assert second["next_page_token"] is None
    assert client.calls[1][1]["$skiptoken"] == "P1"


@pytest.mark.asyncio
async def test_list_users_paginates_with_skiptoken():
    """AC4/TC4: graph_list_users must follow the one-page-per-call contract,
    chaining next_page_token rather than walking pages internally."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    client._result = {
        "value": [{"id": "u1"}],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/users?$skiptoken=P1",
    }
    first = json.loads((await mcp.call_tool("graph_list_users", {}))[0][0].text)
    assert first["has_more"] is True
    assert first["next_page_token"] == "P1"

    client._result = {"value": [{"id": "u2"}]}
    second = json.loads(
        (await mcp.call_tool("graph_list_users", {"page_token": "P1"}))[0][0].text
    )
    assert second["has_more"] is False
    assert second["next_page_token"] is None
    assert client.calls[1][1]["$skiptoken"] == "P1"


@pytest.mark.asyncio
async def test_update_user_omitted_new_fields_are_not_sent():
    """AC5/TC5: omitting the five new fields must not add any of their keys
    to the PATCH body -- only whatever else was actually passed."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_update_user", {"user_id": "u1", "job_title": "Engineer"})

    assert client.methods == ["PATCH"]
    assert client.bodies[0] == {"jobTitle": "Engineer"}
    for key in ("employeeId", "userPrincipalName", "displayName", "givenName", "surname"):
        assert key not in client.bodies[0]


@pytest.mark.asyncio
async def test_update_user_explicit_null_clears_field():
    """AC6/TC6: clearing one of the five fields sends that key as null and no
    other new key.

    Implemented via clear_given_name (a boolean flag mirroring employee_id's
    already-shipped clear_employee_id, PRD-20321) rather than a literal JSON
    null on given_name. Deviation from AC6's literal "explicit null" wording
    is deliberate and documented in docs/tickets/PRD-20393.md Self-test: it
    keeps graph_update_user internally consistent (employee_id already uses
    this shape) and matches docs/modules/users.md's explicit guidance to
    reuse it for future tri-state fields, discovered only after this ticket's
    design was written (PRD-20321 merged into master the day before/same day
    as this design). user_principal_name/display_name intentionally have no
    clear_* flag at all -- see test_update_user_allows_verified_upn_domain's
    module and the Self-test note -- because Graph documents both as
    not-nullable; a null PATCH for either would always be rejected by a real
    tenant regardless of what this fake client would accept.
    """
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_update_user", {"user_id": "u1", "clear_given_name": True})

    assert client.methods == ["PATCH"]
    assert client.bodies[0] == {"givenName": None}


@pytest.mark.asyncio
async def test_update_user_rejects_given_name_and_clear_together():
    """Dev-added (source: dev, not in the groomed AC set): given_name and
    clear_given_name are mutually exclusive, mirroring employee_id's existing
    validation -- flagging here rather than silently assuming it."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    result = await mcp.call_tool(
        "graph_update_user",
        {"user_id": "u1", "given_name": "Alice", "clear_given_name": True},
    )

    assert client.calls == []
    assert json.loads(result[0][0].text)["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_update_user_rejects_unverified_upn_domain():
    """AC7/TC7: a UPN on a domain that is not verified for this tenant must
    be rejected before any PATCH call is made to Graph."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    client._result = {"value": [{"id": "contoso.com", "isVerified": True}]}
    result = await mcp.call_tool(
        "graph_update_user", {"user_id": "u1", "user_principal_name": "alice@evil.example"}
    )

    assert client.methods == ["GET"]
    assert json.loads(result[0][0].text)["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_update_user_allows_verified_upn_domain():
    """AC8/TC8: a UPN on a verified tenant domain must be sent through to
    the PATCH call."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    client._result = {"value": [{"id": "contoso.com", "isVerified": True}]}
    await mcp.call_tool(
        "graph_update_user", {"user_id": "u1", "user_principal_name": "alice@contoso.com"}
    )

    assert client.methods == ["GET", "PATCH"]
    assert client.calls[0][0] == "/domains"
    assert client.bodies[0] == {"userPrincipalName": "alice@contoso.com"}


@pytest.mark.asyncio
async def test_remove_manager_deletes_manager_ref():
    """AC9/TC9: removing a user's manager issues DELETE .../manager/$ref and
    reports status=removed."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    result = json.loads(
        (await mcp.call_tool("graph_remove_manager", {"user_id": "u1"}))[0][0].text
    )

    assert client.methods == ["DELETE"]
    assert client.calls[0][0] == "/users/u1/manager/$ref"
    assert result == {"status": "removed", "user_id": "u1"}


class _DeleteRaisingClient:
    """GraphClient stand-in whose DELETE always raises a given status, to
    exercise graph_remove_manager's 404-is-not-an-error path (AC10)."""

    def __init__(self, status_code: int):
        self._status_code = status_code
        self.calls: list[str] = []

    async def delete(self, path: str):
        self.calls.append(path)
        raise GraphError(self._status_code, "boom")


@pytest.mark.asyncio
async def test_remove_manager_is_idempotent_on_missing_manager():
    """AC10/TC10: a 404 from Graph (no manager set) must come back as
    status=no_manager, not an error envelope, and be safe to call again."""
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import users

    mcp = FastMCP(name="test")
    client = _DeleteRaisingClient(404)
    users.register(mcp, lambda: client)

    result = json.loads(
        (await mcp.call_tool("graph_remove_manager", {"user_id": "u1"}))[0][0].text
    )
    assert result == {"status": "no_manager", "user_id": "u1"}

    # Calling again is equally successful -- idempotent.
    result2 = json.loads(
        (await mcp.call_tool("graph_remove_manager", {"user_id": "u1"}))[0][0].text
    )
    assert result2 == {"status": "no_manager", "user_id": "u1"}


@pytest.mark.asyncio
async def test_get_user_default_select_includes_new_fields():
    """AC11/TC11: with no select param, the default $select must include
    proxyAddresses, identities and the on-premises sync fields, alongside
    today's default set (and the $expand=manager it already carried)."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_get_user", {"user_id": "u1"})

    select = client.calls[0][1]["$select"]
    for field in (
        "proxyAddresses",
        "identities",
        "onPremisesSyncEnabled",
        "onPremisesLastSyncDateTime",
        "onPremisesDistinguishedName",
    ):
        assert field in select, f"{field} missing from default $select"
    assert "$expand" in client.calls[0][1]


@pytest.mark.asyncio
async def test_get_user_custom_select_narrows_fields():
    """AC12/TC12: a select param must produce a $select of exactly those
    fields plus id, and the default $expand=manager must not be added."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_get_user", {"user_id": "u1", "select": ["displayName", "mail"]})

    assert client.calls[0][1]["$select"] == "id,displayName,mail"
    assert "$expand" not in client.calls[0][1]


class _RaisingGetClient:
    """GraphClient stand-in whose GET always raises, to confirm the three
    list tools return the structured error envelope instead of propagating
    an exception when Graph fails mid-pagination (AC13)."""

    def __init__(self, status_code: int = 503):
        self._status_code = status_code

    async def get(self, path: str, params: dict | None = None, extra_headers: dict | None = None):
        raise GraphError(self._status_code, "boom")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name,args",
    [
        ("graph_list_user_groups", {"user_id": "u1"}),
        ("graph_list_groups", {}),
        ("graph_list_users", {}),
    ],
)
async def test_list_tools_surface_graph_error_instead_of_raising(tool_name, args):
    """AC13/TC13: a GraphError from the single-page fetch must come back as
    the existing error envelope, not propagate as an exception, for all
    three list tools touched by this ticket."""
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import groups, users

    mcp = FastMCP(name="test")
    client = _RaisingGetClient(503)
    groups.register(mcp, lambda: client)
    users.register(mcp, lambda: client)

    result = await mcp.call_tool(tool_name, args)
    payload = json.loads(result[0][0].text)
    assert payload["error"]["code"] == "upstream_error"


@pytest.mark.asyncio
async def test_update_user_still_rejects_when_nothing_to_update():
    """AC14/TC14 (regression): with none of the old or new fields set, the
    existing "nothing to update" rejection must still fire -- the five new
    fields and their clear_* companions must not count as always-present."""
    from graph_mcp.tools import users

    mcp, client = _register(users)
    result = await mcp.call_tool("graph_update_user", {"user_id": "u1"})

    assert client.calls == []
    assert json.loads(result[0][0].text)["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_list_domains_filters_unverified_by_default():
    from graph_mcp.tools import domains

    mcp, client = _register(domains)
    client._result = {
        "value": [
            {"id": "contoso.com", "isDefault": True, "isVerified": True},
            {"id": "pending.example", "isDefault": False, "isVerified": False},
        ]
    }
    result = json.loads((await mcp.call_tool("graph_list_domains", {}))[0][0].text)
    assert client.calls[0][0] == "/domains"
    assert [d["id"] for d in result["domains"]] == ["contoso.com"]

    result = json.loads(
        (await mcp.call_tool("graph_list_domains", {"verified_only": False}))[0][0].text
    )
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_get_group_reads_by_id():
    from graph_mcp.tools import groups

    mcp, client = _register(groups)
    await mcp.call_tool("graph_get_group", {"group_id": "00000000-0000-0000-0000-000000000000"})

    assert client.methods == ["GET"]
    assert client.calls[0][0] == "/groups/00000000-0000-0000-0000-000000000000"


@pytest.mark.asyncio
async def test_reprocess_license_posts_without_body():
    from graph_mcp.tools import licenses

    mcp, client = _register(licenses)
    result = json.loads(
        (await mcp.call_tool("graph_reprocess_license", {"user_id": "u1"}))[0][0].text
    )

    assert client.methods == ["POST"]
    assert client.calls[0][0] == "/users/u1/reprocessLicenseAssignment"
    assert client.bodies[0] == {}
    assert result == {"status": "success", "user_id": "u1"}


# --- PRD-20223: Ginko parity -------------------------------------------------


@pytest.mark.asyncio
async def test_check_user_exists_by_proxy_address_uses_advanced_query():
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_check_user_exists", {"proxy_address": "o'brien@contoso.com"})

    path, params = client.calls[0]
    assert path == "/users"
    # Bare address is matched as smtp:, apostrophe doubled for OData.
    assert params["$filter"] == "proxyAddresses/any(x:x eq 'smtp:o''brien@contoso.com')"
    assert params["$count"] == "true"
    assert client.headers[0] == {"ConsistencyLevel": "eventual"}


@pytest.mark.asyncio
async def test_check_user_exists_keeps_explicit_proxy_prefix():
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_check_user_exists", {"proxy_address": "SIP:alice@contoso.com"})

    assert client.calls[0][1]["$filter"] == "proxyAddresses/any(x:x eq 'SIP:alice@contoso.com')"


@pytest.mark.asyncio
async def test_check_user_exists_rejects_more_than_one_key():
    from graph_mcp.tools import users

    mcp, client = _register(users)
    result = await mcp.call_tool(
        "graph_check_user_exists", {"mail": "a@contoso.com", "proxy_address": "a@contoso.com"}
    )

    assert client.calls == []
    assert json.loads(result[0][0].text)["error"]["code"] == "invalid_argument"


@pytest.mark.asyncio
async def test_check_user_exists_by_upn_has_no_advanced_query_header():
    from graph_mcp.tools import users

    mcp, client = _register(users)
    await mcp.call_tool("graph_check_user_exists", {"user_principal_name": "a@contoso.com"})

    assert client.headers[0] == {}
    assert "$count" not in client.calls[0][1]


@pytest.mark.asyncio
async def test_get_group_derives_sync_source():
    from graph_mcp.tools import groups

    mcp, client = _register(groups)
    client._result = {"id": "g1", "mailEnabled": True, "onPremisesSyncEnabled": True}
    result = json.loads((await mcp.call_tool("graph_get_group", {"group_id": "g1"}))[0][0].text)
    assert result["sync_source"] == "on-premises"
    assert "onPremisesLastSyncDateTime" in client.calls[0][1]["$select"]

    client._result = {"id": "g2", "mailEnabled": False, "onPremisesSyncEnabled": None}
    result = json.loads((await mcp.call_tool("graph_get_group", {"group_id": "g2"}))[0][0].text)
    assert result["sync_source"] == "cloud"


@pytest.mark.asyncio
async def test_list_group_members_returns_only_the_skiptoken():
    from graph_mcp.tools import groups

    mcp, client = _register(groups)
    client._result = {
        "value": [
            {"@odata.type": "#microsoft.graph.user", "id": "u1", "displayName": "Alice"},
            {"@odata.type": "#microsoft.graph.group", "id": "g9", "displayName": "Nested"},
        ],
        "@odata.nextLink": "https://graph.microsoft.com/v1.0/groups/g1/members?$top=2&$skiptoken=ABC%2B1",
    }
    result = json.loads(
        (await mcp.call_tool("graph_list_group_members", {"group_id": "g1", "max_results": 2}))[0][0].text
    )

    assert client.calls[0][0] == "/groups/g1/members"
    assert client.calls[0][1]["$top"] == "2"
    assert [m["member_type"] for m in result["members"]] == ["user", "group"]
    assert result["has_more"] is True
    assert result["next_page_token"] == "ABC+1"


@pytest.mark.asyncio
async def test_list_group_members_next_page_passes_skiptoken_and_caps_page():
    from graph_mcp.tools import groups

    mcp, client = _register(groups)
    client._result = {"value": []}
    result = json.loads(
        (
            await mcp.call_tool(
                "graph_list_group_members",
                {"group_id": "g1", "max_results": 5000, "page_token": "ABC+1"},
            )
        )[0][0].text
    )

    params = client.calls[0][1]
    assert params["$skiptoken"] == "ABC+1"
    assert params["$top"] == "200"
    assert result == {
        "group_id": "g1",
        "count": 0,
        "members": [],
        "has_more": False,
        "next_page_token": None,
    }


# ---------------------------------------------------------------------------
# graph_read_site_file_text (PRD-20413)
# ---------------------------------------------------------------------------


class _SiteFileClient:
    """GraphClient stand-in for graph_read_site_file_text's multi-step
    resolution chain. `get` returns queued responses in call order — a dict
    to return, or a GraphError instance to raise; `get_content` always
    returns the fixed byte string passed at construction."""

    def __init__(self, steps: list, content: bytes = b""):
        self._steps = list(steps)
        self.calls: list[tuple[str, dict]] = []
        self.methods: list[str] = []
        self.content_calls: list[str] = []
        self._content = content

    async def get(self, path: str, params: dict | None = None, extra_headers: dict | None = None):
        self.methods.append("GET")
        self.calls.append((path, params or {}))
        step = self._steps.pop(0)
        if isinstance(step, GraphError):
            raise step
        return step

    async def get_content(self, path: str) -> bytes:
        self.methods.append("GET")
        self.content_calls.append(path)
        return self._content


def _sites_mcp(client):
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import sites

    mcp = FastMCP(name="test")
    sites.register(mcp, lambda: client)
    return mcp


async def _read_site_file(client, **args):
    mcp = _sites_mcp(client)
    full_args = {"site_query": "permission matrix", "file_name": "Permission Matrix.csv", **args}
    _, structured = await mcp.call_tool("graph_read_site_file_text", full_args)
    return json.loads(structured["result"])


@pytest.mark.asyncio
async def test_read_site_file_text_happy_path_exact_site_match():
    site_hit = {
        "id": "site1",
        "name": "pm",
        "displayName": "Permission Matrix",
        "webUrl": "https://x/sites/pm",
    }
    drive = {"id": "drive1", "name": "Documents"}
    file_item = {
        "id": "item1",
        "name": "Permission Matrix.csv",
        "size": 38,
        "file": {"mimeType": "text/csv"},
        "lastModifiedDateTime": "2026-10-01T00:00:00Z",
    }
    client = _SiteFileClient(
        steps=[{"value": [site_hit]}, drive, file_item],
        content=b"Job Title,Collaboration\nEngineer,Full",
    )
    payload = await _read_site_file(client)

    assert payload["content"] == "Job Title,Collaboration\nEngineer,Full"
    assert payload["site"] == {
        "id": "site1",
        "displayName": "Permission Matrix",
        "webUrl": "https://x/sites/pm",
    }
    assert payload["drive"] == {"id": "drive1", "name": "Documents"}
    assert payload["file"]["id"] == "item1"
    assert payload["file"]["size"] == 38
    # The only request args the caller supplied are site_query/file_name —
    # no driveId/item id ever appears in what produced this result.
    assert client.calls[0][0] == "/sites"
    assert client.calls[0][1]["search"] == "permission matrix"


@pytest.mark.asyncio
async def test_read_site_file_text_falls_back_to_single_keyword_hit():
    site_hit = {
        "id": "site1",
        "name": "pm-hub",
        "displayName": "PM Hub",
        "webUrl": "https://x/sites/pmhub",
    }
    drive = {"id": "drive1", "name": "Documents"}
    file_item = {
        "id": "item1",
        "name": "Permission Matrix.csv",
        "size": 7,
        "lastModifiedDateTime": "2026-10-01T00:00:00Z",
    }
    client = _SiteFileClient(steps=[{"value": [site_hit]}, drive, file_item], content=b"a,b\n1,2")
    payload = await _read_site_file(client)

    assert "error" not in payload
    assert payload["site"]["id"] == "site1"
    assert payload["content"] == "a,b\n1,2"


@pytest.mark.asyncio
async def test_read_site_file_text_site_not_found_no_further_calls():
    client = _SiteFileClient(steps=[{"value": []}])
    payload = await _read_site_file(client)

    assert payload["error"]["code"] == "site_not_found"
    assert payload["error"]["searched_for"] == "permission matrix"
    assert len(client.calls) == 1
    assert client.content_calls == []


@pytest.mark.asyncio
async def test_read_site_file_text_site_ambiguous_lists_candidates():
    hits = [
        {"id": "s1", "displayName": "PM Alpha", "webUrl": "https://x/a"},
        {"id": "s2", "displayName": "PM Beta", "webUrl": "https://x/b"},
    ]
    client = _SiteFileClient(steps=[{"value": hits}])
    payload = await _read_site_file(client)

    assert payload["error"]["code"] == "site_ambiguous"
    assert payload["error"]["candidates"] == hits
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_read_site_file_text_site_id_skips_search():
    site = {"id": "site1", "displayName": "Permission Matrix", "webUrl": "https://x/pm"}
    drive = {"id": "drive1", "name": "Documents"}
    file_item = {
        "id": "item1",
        "name": "Permission Matrix.csv",
        "size": 7,
        "lastModifiedDateTime": "2026-10-01T00:00:00Z",
    }
    client = _SiteFileClient(steps=[site, drive, file_item], content=b"a,b\n1,2")
    payload = await _read_site_file(client, site_id="site1")

    assert payload["site"]["id"] == "site1"
    assert client.calls[0][0] == "/sites/site1"
    assert all("search" not in params for _, params in client.calls)


@pytest.mark.asyncio
async def test_read_site_file_text_file_not_found_lists_folder_contents():
    site_hit = {"id": "site1", "displayName": "Permission Matrix", "webUrl": "https://x/pm"}
    drive = {"id": "drive1", "name": "Documents"}
    client = _SiteFileClient(
        steps=[
            {"value": [site_hit]},
            drive,
            GraphError(404, "not found"),
            {"value": [{"name": "a.csv"}, {"name": "b.csv"}]},
        ]
    )
    payload = await _read_site_file(client)

    assert payload["error"]["code"] == "file_not_found"
    assert payload["error"]["files_in_folder"] == ["a.csv", "b.csv"]
    assert client.content_calls == []


@pytest.mark.asyncio
async def test_read_site_file_text_path_is_folder_not_file():
    site_hit = {"id": "site1", "displayName": "Permission Matrix", "webUrl": "https://x/pm"}
    drive = {"id": "drive1", "name": "Documents"}
    folder_item = {"id": "folder1", "name": "Permission Matrix.csv", "folder": {"childCount": 2}}
    client = _SiteFileClient(steps=[{"value": [site_hit]}, drive, folder_item])
    payload = await _read_site_file(client)

    assert payload["error"]["code"] == "file_not_found"
    assert payload["error"]["reason"] == "path is a folder, not a file"
    assert client.content_calls == []


@pytest.mark.asyncio
async def test_read_site_file_text_no_document_library():
    site_hit = {"id": "site1", "displayName": "Permission Matrix", "webUrl": "https://x/pm"}
    client = _SiteFileClient(steps=[{"value": [site_hit]}, GraphError(404, "no drive")])
    payload = await _read_site_file(client)

    assert payload["error"]["code"] == "file_not_found"
    assert payload["error"]["reason"] == "site has no default document library"
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_read_site_file_text_empty_file_no_content_fetch():
    site_hit = {"id": "site1", "displayName": "Permission Matrix", "webUrl": "https://x/pm"}
    drive = {"id": "drive1", "name": "Documents"}
    file_item = {
        "id": "item1",
        "name": "Permission Matrix.csv",
        "size": 0,
        "lastModifiedDateTime": "2026-10-01T00:00:00Z",
    }
    client = _SiteFileClient(steps=[{"value": [site_hit]}, drive, file_item])
    payload = await _read_site_file(client)

    assert payload["error"]["code"] == "file_empty"
    assert payload["error"]["file"] == {
        "name": "Permission Matrix.csv",
        "lastModifiedDateTime": "2026-10-01T00:00:00Z",
    }
    assert client.content_calls == []


@pytest.mark.asyncio
async def test_read_site_file_text_rejects_file_over_200kb():
    site_hit = {"id": "site1", "displayName": "Permission Matrix", "webUrl": "https://x/pm"}
    drive = {"id": "drive1", "name": "Documents"}
    file_item = {"id": "item1", "name": "big.csv", "size": 200_001}
    client = _SiteFileClient(steps=[{"value": [site_hit]}, drive, file_item])
    payload = await _read_site_file(client, file_name="big.csv")

    assert payload["error"]["code"] == "file_too_large"
    assert payload["error"]["size"] == 200_001
    assert payload["error"]["limit"] == 200_000
    assert client.content_calls == []


@pytest.mark.asyncio
async def test_read_site_file_text_allows_file_at_exactly_200kb_boundary():
    site_hit = {"id": "site1", "displayName": "Permission Matrix", "webUrl": "https://x/pm"}
    drive = {"id": "drive1", "name": "Documents"}
    file_item = {"id": "item1", "name": "exact.csv", "size": 200_000}
    client = _SiteFileClient(steps=[{"value": [site_hit]}, drive, file_item], content=b"ok")
    payload = await _read_site_file(client, file_name="exact.csv")

    assert "error" not in payload
    assert payload["content"] == "ok"
    assert client.content_calls == ["/drives/drive1/items/item1/content"]


@pytest.mark.asyncio
async def test_read_site_file_text_binary_content_returns_not_text():
    site_hit = {"id": "site1", "displayName": "Permission Matrix", "webUrl": "https://x/pm"}
    drive = {"id": "drive1", "name": "Documents"}
    file_item = {
        "id": "item1",
        "name": "report.xlsx",
        "size": 10,
        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
    }
    client = _SiteFileClient(
        steps=[{"value": [site_hit]}, drive, file_item], content=b"\xff\xfe\x00\x01"
    )
    payload = await _read_site_file(client, file_name="report.xlsx")

    assert payload["error"]["code"] == "not_text"
    assert (
        payload["error"]["file_type"]
        == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@pytest.mark.asyncio
async def test_read_site_file_text_no_cross_call_state_leak():
    site_a = {"id": "site-a", "displayName": "Tenant A Site", "webUrl": "https://a/x"}
    site_b = {"id": "site-b", "displayName": "Tenant B Site", "webUrl": "https://b/x"}
    client_a = _SiteFileClient(
        steps=[
            {"value": [site_a]},
            {"id": "drive-a", "name": "Documents"},
            {"id": "item-a", "name": "a.csv", "size": 3},
        ],
        content=b"aaa",
    )
    client_b = _SiteFileClient(
        steps=[
            {"value": [site_b]},
            {"id": "drive-b", "name": "Documents"},
            {"id": "item-b", "name": "b.csv", "size": 3},
        ],
        content=b"bbb",
    )

    payload_a = await _read_site_file(client_a, site_query="Tenant A Site", file_name="a.csv")
    payload_b = await _read_site_file(client_b, site_query="Tenant B Site", file_name="b.csv")

    assert payload_a["content"] == "aaa"
    assert payload_b["content"] == "bbb"
    # Each tenant's own client independently issued its own full resolution
    # chain — the second call's site-search call was actually made, not
    # skipped by anything cached from the first call.
    assert client_a.calls[0][0] == "/sites"
    assert client_b.calls[0][0] == "/sites"
    assert len(client_a.calls) == 3
    assert len(client_b.calls) == 3


@pytest.mark.asyncio
async def test_read_site_file_text_only_issues_get_requests():
    site_hit = {"id": "site1", "displayName": "Permission Matrix", "webUrl": "https://x/pm"}
    drive = {"id": "drive1", "name": "Documents"}
    file_item = {"id": "item1", "name": "Permission Matrix.csv", "size": 5}
    client = _SiteFileClient(steps=[{"value": [site_hit]}, drive, file_item], content=b"hello")

    await _read_site_file(client)

    assert client.methods and all(m == "GET" for m in client.methods)


# --- PRD-20422: graph_transfer_onedrive / graph_start_cloud_sync ---------


class _RoutedClient:
    """GraphClient stand-in for offboarding.py's multi-endpoint resolve/
    list/act flows: GET responses are keyed by exact path (a dict to
    return, or a GraphError to raise); POST responses are keyed by exact
    path too, defaulting to {} when the test doesn't care about the body.
    Every call is recorded for assertions on call order/count.
    """

    def __init__(self, get_responses: dict, post_responses: dict | None = None):
        self._get_responses = dict(get_responses)
        self._post_responses = dict(post_responses or {})
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict]] = []

    async def get(self, path, params=None, extra_headers=None):
        self.get_calls.append((path, params or {}))
        resp = self._get_responses[path]
        if isinstance(resp, GraphError):
            raise resp
        return resp

    async def post(self, path, body=None, extra_headers=None):
        self.post_calls.append((path, body or {}))
        resp = self._post_responses.get(path, {})
        if isinstance(resp, GraphError):
            raise resp
        return resp


def _offboarding_mcp(client):
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import offboarding

    mcp = FastMCP(name="test")
    offboarding.register(mcp, lambda: client)
    return mcp


async def _call_offboarding(client, tool_name, **args):
    mcp = _offboarding_mcp(client)
    result = await mcp.call_tool(tool_name, args)
    return json.loads(result[0][0].text)


@pytest.mark.asyncio
async def test_transfer_onedrive_starts_copy_for_new_items():
    """AC2/TC2: a fresh transfer with no destination overlap returns
    status=started and issues one copy per source item."""
    client = _RoutedClient(
        get_responses={
            "/users/u-src/drive": {"id": "d-src"},
            "/users/u-dst/drive": {"id": "d-dst"},
            "/drives/d-src/root/children": {
                "value": [
                    {"id": "item1", "name": "a.txt", "size": 10},
                    {"id": "item2", "name": "b.txt", "size": 20},
                ]
            },
            "/drives/d-dst/root:/offboarded-u-src:": GraphError(404, "not found"),
            "/drives/d-dst/items/folder1/children": {"value": []},
        },
        post_responses={
            "/drives/d-dst/root/children": {"id": "folder1", "name": "offboarded-u-src"},
        },
    )
    payload = await _call_offboarding(
        client, "graph_transfer_onedrive", source_user_id="u-src", destination_user_id="u-dst"
    )

    assert payload["status"] == "started"
    assert payload["destination_folder"] == {"id": "folder1", "name": "offboarded-u-src"}
    assert payload["items"] == [
        {"name": "a.txt", "size": 10, "action": "copy_started"},
        {"name": "b.txt", "size": 20, "action": "copy_started"},
    ]
    copy_paths = [p for p, _ in client.post_calls if p.endswith("/copy")]
    assert copy_paths == [
        "/drives/d-src/items/item1/copy",
        "/drives/d-src/items/item2/copy",
    ]


@pytest.mark.asyncio
async def test_transfer_onedrive_is_idempotent_when_all_items_already_present():
    """AC3/TC3: a repeat call where every item already matches name+size
    returns status=completed and issues zero copy calls."""
    client = _RoutedClient(
        get_responses={
            "/users/u-src/drive": {"id": "d-src"},
            "/users/u-dst/drive": {"id": "d-dst"},
            "/drives/d-src/root/children": {
                "value": [{"id": "item1", "name": "a.txt", "size": 10}]
            },
            "/drives/d-dst/root:/offboarded-u-src:": {"id": "folder1", "name": "offboarded-u-src"},
            "/drives/d-dst/items/folder1/children": {
                "value": [{"name": "a.txt", "size": 10}]
            },
        },
    )
    payload = await _call_offboarding(
        client, "graph_transfer_onedrive", source_user_id="u-src", destination_user_id="u-dst"
    )

    assert payload["status"] == "completed"
    assert payload["items"] == [{"name": "a.txt", "size": 10, "action": "already_present"}]
    assert client.post_calls == []


@pytest.mark.asyncio
async def test_transfer_onedrive_does_not_recopy_items_mid_transfer():
    """AC4/TC4: a destination item present at a different size is still
    mid-copy -- status=in_progress, no duplicate copy issued for it."""
    client = _RoutedClient(
        get_responses={
            "/users/u-src/drive": {"id": "d-src"},
            "/users/u-dst/drive": {"id": "d-dst"},
            "/drives/d-src/root/children": {
                "value": [{"id": "item1", "name": "a.txt", "size": 10}]
            },
            "/drives/d-dst/root:/offboarded-u-src:": {"id": "folder1", "name": "offboarded-u-src"},
            "/drives/d-dst/items/folder1/children": {
                "value": [{"name": "a.txt", "size": 5}]
            },
        },
    )
    payload = await _call_offboarding(
        client, "graph_transfer_onedrive", source_user_id="u-src", destination_user_id="u-dst"
    )

    assert payload["status"] == "in_progress"
    assert payload["items"] == [{"name": "a.txt", "size": 10, "action": "in_progress"}]
    assert client.post_calls == []


@pytest.mark.asyncio
async def test_transfer_onedrive_reports_drive_not_found_for_source():
    """AC5/TC5: a source with no OneDrive returns drive_not_found naming
    the source and never calls the destination."""
    client = _RoutedClient(
        get_responses={"/users/u-src/drive": GraphError(404, "not found")},
    )
    payload = await _call_offboarding(
        client, "graph_transfer_onedrive", source_user_id="u-src", destination_user_id="u-dst"
    )

    assert payload["error"]["code"] == "drive_not_found"
    assert payload["error"]["role"] == "source"
    assert client.get_calls == [("/users/u-src/drive", {"$select": "id,driveType"})]


@pytest.mark.asyncio
async def test_transfer_onedrive_reports_no_content_for_empty_source():
    """AC6/TC6: an empty source root returns status=no_content and creates
    no destination folder."""
    client = _RoutedClient(
        get_responses={
            "/users/u-src/drive": {"id": "d-src"},
            "/users/u-dst/drive": {"id": "d-dst"},
            "/drives/d-src/root/children": {"value": []},
        },
    )
    payload = await _call_offboarding(
        client, "graph_transfer_onedrive", source_user_id="u-src", destination_user_id="u-dst"
    )

    assert payload["status"] == "no_content"
    assert payload["items"] == []
    assert client.post_calls == []
    assert not any("root:/" in p for p, _ in client.get_calls)


@pytest.mark.asyncio
async def test_transfer_onedrive_reports_drive_not_found_for_destination():
    """AC7/TC7: a destination with no OneDrive returns drive_not_found
    naming the destination and never lists the source's content."""
    client = _RoutedClient(
        get_responses={
            "/users/u-src/drive": {"id": "d-src"},
            "/users/u-dst/drive": GraphError(404, "not found"),
        },
    )
    payload = await _call_offboarding(
        client, "graph_transfer_onedrive", source_user_id="u-src", destination_user_id="u-dst"
    )

    assert payload["error"]["code"] == "drive_not_found"
    assert payload["error"]["role"] == "destination"
    assert not any(p == "/drives/d-src/root/children" for p, _ in client.get_calls)


@pytest.mark.asyncio
async def test_start_cloud_sync_starts_job_for_synced_user():
    """AC8/TC8: a synced user with exactly one discoverable cloud-sync job
    returns status=started with the resolved job_id/service_principal_id."""
    client = _RoutedClient(
        get_responses={
            "/users/u1": {"onPremisesSyncEnabled": True},
            "/servicePrincipals": {
                "value": [
                    {"id": "sp1", "homepage": "https://x/AD2AADProvisioningAgent"},
                ]
            },
            "/servicePrincipals/sp1/synchronization/jobs": {
                "value": [
                    {
                        "id": "job1",
                        "templateId": "AD2AADProvisioning",
                        "schedule": {"state": "Active"},
                    }
                ]
            },
        },
    )
    payload = await _call_offboarding(client, "graph_start_cloud_sync", user_id="u1")

    assert payload["status"] == "started"
    assert payload["service_principal_id"] == "sp1"
    assert payload["job_id"] == "job1"
    assert client.post_calls == [
        ("/servicePrincipals/sp1/synchronization/jobs/job1/start", {})
    ]


@pytest.mark.asyncio
async def test_start_cloud_sync_is_not_applicable_for_cloud_only_user():
    """AC9/TC9: a cloud-only user (onPremisesSyncEnabled=false) returns
    status=not_applicable and issues no further calls."""
    client = _RoutedClient(get_responses={"/users/u1": {"onPremisesSyncEnabled": False}})
    payload = await _call_offboarding(client, "graph_start_cloud_sync", user_id="u1")

    assert payload["status"] == "not_applicable"
    assert "reason" in payload
    assert client.get_calls == [("/users/u1", {"$select": "onPremisesSyncEnabled"})]
    assert client.post_calls == []


@pytest.mark.asyncio
async def test_start_cloud_sync_reports_no_sync_job_configured():
    """AC10/TC10: zero discoverable cloud-sync service principals returns
    no_sync_job_configured naming the on-prem Start-ADSyncSyncCycle
    alternative."""
    client = _RoutedClient(
        get_responses={
            "/users/u1": {"onPremisesSyncEnabled": True},
            "/servicePrincipals": {"value": []},
        },
    )
    payload = await _call_offboarding(client, "graph_start_cloud_sync", user_id="u1")

    assert payload["error"]["code"] == "no_sync_job_configured"
    assert "Start-ADSyncSyncCycle" in payload["error"]["message"]
    assert client.post_calls == []


@pytest.mark.asyncio
async def test_start_cloud_sync_reports_ambiguous_when_multiple_jobs():
    """AC11/TC11: two discoverable cloud-sync jobs on the same service
    principal returns sync_job_ambiguous listing both candidate ids."""
    client = _RoutedClient(
        get_responses={
            "/users/u1": {"onPremisesSyncEnabled": True},
            "/servicePrincipals": {
                "value": [{"id": "sp1", "homepage": "https://x/AD2AADProvisioningAgent"}]
            },
            "/servicePrincipals/sp1/synchronization/jobs": {
                "value": [
                    {
                        "id": "job1",
                        "templateId": "AD2AADProvisioning",
                        "schedule": {"state": "Active"},
                    },
                    {
                        "id": "job2",
                        "templateId": "AD2AADProvisioning",
                        "schedule": {"state": "Active"},
                    },
                ]
            },
        },
    )
    payload = await _call_offboarding(client, "graph_start_cloud_sync", user_id="u1")

    assert payload["error"]["code"] == "sync_job_ambiguous"
    assert payload["error"]["candidates"] == ["job1", "job2"]
    assert client.post_calls == []


@pytest.mark.asyncio
async def test_start_cloud_sync_discovery_is_not_cached_across_tenants():
    """AC12/TC12: two calls against two different fake clients (standing in
    for two tenants' tokens) each resolve their own ids -- neither call's
    sp/job id leaks into the other's request trace, since nothing is cached
    at module/process scope."""
    client_a = _RoutedClient(
        get_responses={
            "/users/u1": {"onPremisesSyncEnabled": True},
            "/servicePrincipals": {
                "value": [{"id": "spA", "homepage": "https://x/AD2AADProvisioningAgent"}]
            },
            "/servicePrincipals/spA/synchronization/jobs": {
                "value": [
                    {
                        "id": "jobA",
                        "templateId": "AD2AADProvisioning",
                        "schedule": {"state": "Active"},
                    }
                ]
            },
        },
    )
    client_b = _RoutedClient(
        get_responses={
            "/users/u1": {"onPremisesSyncEnabled": True},
            "/servicePrincipals": {
                "value": [{"id": "spB", "homepage": "https://x/AD2AADProvisioningAgent"}]
            },
            "/servicePrincipals/spB/synchronization/jobs": {
                "value": [
                    {
                        "id": "jobB",
                        "templateId": "AD2AADProvisioning",
                        "schedule": {"state": "Active"},
                    }
                ]
            },
        },
    )

    payload_a = await _call_offboarding(client_a, "graph_start_cloud_sync", user_id="u1")
    payload_b = await _call_offboarding(client_b, "graph_start_cloud_sync", user_id="u1")

    assert payload_a["service_principal_id"] == "spA"
    assert payload_a["job_id"] == "jobA"
    assert payload_b["service_principal_id"] == "spB"
    assert payload_b["job_id"] == "jobB"
    assert not any("spB" in p or "jobB" in p for p, _ in client_a.get_calls + client_a.post_calls)
    assert not any("spA" in p or "jobA" in p for p, _ in client_b.get_calls + client_b.post_calls)
