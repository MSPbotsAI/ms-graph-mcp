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
}


@pytest.mark.asyncio
async def test_tools_list_snapshot():
    mcp = create_mcp_server(Settings())
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert names == set(EXPECTED_TOOLS), f"unexpected tool set: {names}"
    # 25: original <=20 guideline, +2 for SharePoint create/delete (PRD-17756),
    # +3 for graph_list_owned_groups/graph_list_managed_devices/
    # graph_remove_managed_device (PRD-17403 offboarding tool-gap audit).
    assert len(names) <= 25, "tool count should stay within the SOP's <=20 guideline (+5 justified)"

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
        self._result = result if result is not None else {"value": []}

    async def get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append((path, params or {}))
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
    lookup followed by a paginated /groups/delta scan), so a single fixed
    result like _CapturingClient's isn't enough."""

    def __init__(self, results: list):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append((path, params or {}))
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_list_owned_groups_paginates_delta_and_filters_by_owner():
    """graph_list_owned_groups has no app-only-safe way to ask Graph
    "groups owned by user X" directly (see groups.py's comment), so it
    walks a full /groups/delta scan and filters client-side. This checks
    that filtering, the owner_count it derives from owners@delta, and
    that it follows @odata.nextLink across pages until the delta scan
    ends (no nextLink on the last page)."""
    from mcp.server.fastmcp import FastMCP

    from graph_mcp.tools import groups

    mcp = FastMCP(name="test")
    client = _QueuedClient(
        [
            {"id": "user-123"},  # /users/{id} resolution
            {
                "value": [
                    {"id": "g1", "displayName": "Owned Solo", "owners@delta": [{"id": "user-123"}]},
                    {
                        "id": "g2",
                        "displayName": "Not Owned",
                        "owners@delta": [{"id": "someone-else"}],
                    },
                ],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/groups/delta?$skiptoken=page2",
            },
            {
                "value": [
                    {
                        "id": "g3",
                        "displayName": "Owned Shared",
                        "owners@delta": [{"id": "user-123"}, {"id": "other"}],
                    },
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/groups/delta?$deltatoken=done",
            },
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

    # 3 calls total: resolve user_id, then 2 delta pages (stops once a
    # page has no @odata.nextLink, i.e. only @odata.deltaLink remains).
    assert [c[0] for c in client.calls] == [
        "/users/carl@contoso.com",
        "/groups/delta",
        "https://graph.microsoft.com/v1.0/groups/delta?$skiptoken=page2",
    ]
