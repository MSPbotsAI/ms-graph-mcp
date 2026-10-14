from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import GraphClient, GraphError
from ._common import NO_TOKEN, odata_quote, skiptoken

_DIRECTORY_OBJECT_URL = "https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"

# Our own ceiling on how many results a list tool returns to the agent
# (SOP: default <=50, hard cap <=200). Separate from Graph's own $top
# per-page maximum, which is documented at 999 for these endpoints and is
# used below purely to size individual page requests.
_DEFAULT_MAX_RESULTS = 50
_HARD_CAP_MAX_RESULTS = 200
_GRAPH_TOP_MAX = 999
# Safety cap on how many /groups list pages graph_list_owned_groups will
# walk (at $top=999/page this covers ~50k groups) — a runaway pagination
# loop should stop rather than hang the request indefinitely.
_MAX_GROUP_LIST_PAGES = 50


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
    )
    async def graph_assign_groups(
        user_id: Annotated[str, Field(description="Object ID of the user to add.")],
        group_ids: Annotated[
            list[str], Field(description="List of group object IDs to add the user to.")
        ],
    ) -> str:
        """Add an Entra ID user to one or more groups.

        Processes every group_id and returns a per-group result; a user
        already in a group is treated as success (idempotent).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        member_url = _DIRECTORY_OBJECT_URL.format(user_id=user_id)
        results = []

        for group_id in group_ids:
            try:
                await client.post(
                    f"/groups/{group_id}/members/$ref",
                    {"@odata.id": member_url},
                )
                results.append({"group_id": group_id, "status": "added"})
            except GraphError as e:
                # Graph returns 400 when the user is already a member
                if e.status_code == 400 and "already exist" in str(e.message).lower():
                    results.append({"group_id": group_id, "status": "already_member"})
                else:
                    results.append(
                        {"group_id": group_id, "status": "error", "detail": e.message}
                    )

        return dump_json_capped({"user_id": user_id, "results": results})

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
    )
    async def graph_add_group_owner(
        group_id: Annotated[str, Field(description="Object ID of the group or Team.")],
        user_id: Annotated[str, Field(description="Object ID of the user to set as owner.")],
    ) -> str:
        """Add an Entra ID user as an owner of a group or Team.

        Already-an-owner is treated as success (idempotent), mirroring
        graph_assign_groups. Teams is backed by an M365 Group in Graph, so
        this covers both. Enforces no approval step — any SOP-level
        approval must happen before calling this (PRD-20480).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        owner_url = _DIRECTORY_OBJECT_URL.format(user_id=user_id)

        try:
            await client.post(
                f"/groups/{group_id}/owners/$ref",
                {"@odata.id": owner_url},
            )
            return dump_json_capped(
                {"group_id": group_id, "user_id": user_id, "status": "added"}
            )
        except GraphError as e:
            # Graph returns 400 when the user is already an owner — mirror
            # graph_assign_groups's existing "already exist" handling.
            if e.status_code == 400 and "already exist" in str(e.message).lower():
                return dump_json_capped(
                    {"group_id": group_id, "user_id": user_id, "status": "already_owner"}
                )
            return dump_json_capped(
                {
                    "group_id": group_id,
                    "user_id": user_id,
                    "status": "error",
                    "detail": e.message,
                }
            )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_user_groups(
        user_id: Annotated[str, Field(description="User's id (GUID) or userPrincipalName.")],
        max_results: Annotated[
            int, Field(description="Max groups per page (default 50, hard cap 200).")
        ] = _DEFAULT_MAX_RESULTS,
        page_token: Annotated[
            str | None,
            Field(description="next_page_token from the previous page; omit for the first page."),
        ] = None,
    ) -> str:
        """List groups a user directly belongs to, one page at a time.

        Use to mirror memberships onto a new hire, or to route a group
        through Graph, Exchange or AD during offboarding — each entry
        includes groupTypes, mailEnabled and sync_source ("on-premises" for
        AD-synced groups, else "cloud"). has_more=true: call again with
        next_page_token (one Graph page per call — PRD-20393).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        max_results = max(1, min(max_results, _HARD_CAP_MAX_RESULTS))
        params: dict = {
            "$select": "id,displayName,groupTypes,securityEnabled,mailEnabled,onPremisesSyncEnabled",
            "$top": str(min(max_results, _GRAPH_TOP_MAX)),
        }
        if page_token:
            params["$skiptoken"] = page_token

        try:
            result = await client.get(f"/users/{user_id}/memberOf", params=params)
            groups = []
            for obj in result.get("value", []):
                # memberOf can also return directoryRoles; keep only groups.
                if not (obj.get("@odata.type", "").endswith("group") or "groupTypes" in obj):
                    continue
                obj["sync_source"] = (
                    "on-premises" if obj.get("onPremisesSyncEnabled") else "cloud"
                )
                groups.append(obj)
            next_token = skiptoken(result.get("@odata.nextLink"))
            return dump_json_capped(
                {
                    "user_id": user_id,
                    "count": len(groups),
                    "groups": groups,
                    "has_more": next_token is not None,
                    "next_page_token": next_token,
                }
            )
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_groups(
        display_name: Annotated[
            str | None,
            Field(
                description="Filter by display name; prefix match unless exact=True. Omit to list all groups (still capped by max_results)."
            ),
        ] = None,
        exact: Annotated[
            bool, Field(description="When True, match display_name exactly instead of by prefix.")
        ] = False,
        max_results: Annotated[
            int, Field(description="Max groups per page (default 50, hard cap 200).")
        ] = _DEFAULT_MAX_RESULTS,
        page_token: Annotated[
            str | None,
            Field(description="next_page_token from the previous page; omit for the first page."),
        ] = None,
    ) -> str:
        """List or search Entra ID groups by display name, one page at a time.

        Use to resolve a group name to its object id for graph_assign_groups.
        When has_more is true, call again with next_page_token to get the
        next page. (PRD-20393: see graph_list_user_groups's docstring for the
        same one-page-per-call contract change.)
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        max_results = max(1, min(max_results, _HARD_CAP_MAX_RESULTS))
        params: dict = {
            "$select": "id,displayName,groupTypes,securityEnabled,mailEnabled",
            "$top": str(min(max_results, _GRAPH_TOP_MAX)),
        }
        if page_token:
            params["$skiptoken"] = page_token
        if display_name:
            quoted = odata_quote(display_name)
            if exact:
                params["$filter"] = f"displayName eq '{quoted}'"
            else:
                params["$filter"] = f"startswith(displayName,'{quoted}')"

        try:
            result = await client.get("/groups", params=params)
            groups = result.get("value", [])[:max_results]
            next_token = skiptoken(result.get("@odata.nextLink"))
            return dump_json_capped(
                {
                    "count": len(groups),
                    "groups": groups,
                    "has_more": next_token is not None,
                    "next_page_token": next_token,
                }
            )
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_owned_groups(
        user_id: Annotated[str, Field(description="User's id (GUID) or userPrincipalName.")],
    ) -> str:
        """List Entra ID groups a user owns, each enriched with its total
        owner count.

        Use before offboarding: a group this user owns becomes an
        ownerless "orphan" group the moment they're removed, unless
        someone else also owns it. A result with owner_count == 1 means
        this user is the sole owner — reassign an owner before disabling
        the account, or flag the group for cleanup.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        # Graph has no query that reverse-looks-up "groups owned by user X"
        # under Application permissions: /users/{id}/ownedObjects is
        # documented as unsupported for app-only, /groups?$filter=
        # owners/any(...) is rejected outright by Graph ("owners" isn't a
        # filterable property on Group), and /groups/delta with owners in
        # $select — which looked like the answer on paper — turned out to
        # have a real pagination bug when tested against a live tenant: it
        # re-returns the same ~200 groups on every page instead of
        # advancing, confirmed independently of whether owners is selected.
        # Plain /groups listing doesn't have that bug (verified: matches
        # /groups/$count exactly, one page, no repeats), so the only
        # approach that's actually verified reliable is enumerate all
        # groups, then check each one's /owners — N+1, but there's no
        # working alternative.
        try:
            user = await client.get(f"/users/{user_id}", params={"$select": "id"})
        except GraphError as e:
            return e.to_envelope()
        resolved_id = user.get("id") if isinstance(user, dict) else None
        if not resolved_id:
            return error_envelope(
                "not_found", f"User '{user_id}' could not be resolved to an id.", False
            )

        all_groups: list = []
        try:
            result = await client.get(
                "/groups",
                params={
                    "$select": "id,displayName,groupTypes,securityEnabled,mailEnabled",
                    "$top": str(_GRAPH_TOP_MAX),
                },
            )
            pages = 0
            while result:
                all_groups.extend(result.get("value", []))
                pages += 1
                next_link = result.get("@odata.nextLink")
                if not next_link or pages >= _MAX_GROUP_LIST_PAGES:
                    break
                result = await client.get(next_link)
        except GraphError as e:
            return e.to_envelope()

        owned_groups: list = []
        for group in all_groups:
            try:
                owners = await client.get(
                    f"/groups/{group['id']}/owners", params={"$select": "id"}
                )
            except GraphError:
                # A transient error checking one group's owners shouldn't
                # fail the whole scan — we just can't confirm this one, so
                # it's left out rather than risk a false positive/negative.
                continue
            owner_ids = {o["id"] for o in owners.get("value", []) if isinstance(o, dict)}
            if resolved_id in owner_ids:
                group["owner_count"] = len(owner_ids)
                owned_groups.append(group)

        return dump_json_capped(
            {"user_id": user_id, "count": len(owned_groups), "groups": owned_groups}
        )

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
    )
    async def graph_remove_group_member(
        user_id: Annotated[str, Field(description="Object ID of the user to remove.")],
        group_ids: Annotated[
            list[str], Field(description="List of group object IDs to remove the user from.")
        ],
    ) -> str:
        """Remove an Entra ID user from one or more groups.

        Removing a group could cut access to whatever it gates (a shared
        mailbox, a Teams channel, a SharePoint site) — resolve the real
        group_id(s) via graph_list_user_groups or graph_list_groups first;
        never guess one. Processes every group_id and returns a per-group
        result; a user not in a group is treated as success (idempotent).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        results = []

        for group_id in group_ids:
            try:
                await client.delete(f"/groups/{group_id}/members/{user_id}/$ref")
                results.append({"group_id": group_id, "status": "removed"})
            except GraphError as e:
                # Graph returns 404 when the user isn't a member of the group
                if e.status_code == 404:
                    results.append({"group_id": group_id, "status": "not_a_member"})
                else:
                    results.append(
                        {"group_id": group_id, "status": "error", "detail": e.message}
                    )

        return dump_json_capped({"user_id": user_id, "results": results})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_get_group(
        group_id: Annotated[str, Field(description="Group object id (GUID).")],
    ) -> str:
        """Read one Entra ID group's details by id, incl. mail-enabled and sync source.

        sync_source is "on-premises" for AD-synced groups (membership must be
        changed in on-prem AD) or "cloud". Use to confirm a group id still
        exists and is the expected type before graph_assign_groups.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            result = await client.get(
                f"/groups/{group_id}",
                params={
                    "$select": (
                        "id,displayName,description,mail,mailNickname,groupTypes,"
                        "securityEnabled,mailEnabled,visibility,membershipRule,"
                        "onPremisesSyncEnabled,onPremisesLastSyncDateTime"
                    )
                },
            )
            if isinstance(result, dict):
                result["sync_source"] = (
                    "on-premises" if result.get("onPremisesSyncEnabled") else "cloud"
                )
            return dump_json_capped(result)
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_group_members(
        group_id: Annotated[str, Field(description="Group object id (GUID).")],
        max_results: Annotated[
            int, Field(description="Max members per page (default 50, hard cap 200).")
        ] = _DEFAULT_MAX_RESULTS,
        page_token: Annotated[
            str | None,
            Field(description="next_page_token from the previous page; omit for the first page."),
        ] = None,
    ) -> str:
        """List a group's direct members, one page at a time.

        Members can be users, nested groups, devices or service principals
        (see member_type). When has_more is true, call again with
        next_page_token to get the next page.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        max_results = max(1, min(max_results, _HARD_CAP_MAX_RESULTS))
        params: dict = {
            "$select": "id,displayName,userPrincipalName,mail,accountEnabled",
            "$top": str(max_results),
        }
        if page_token:
            params["$skiptoken"] = page_token

        try:
            result = await client.get(f"/groups/{group_id}/members", params=params)
            members = []
            for m in result.get("value", [])[:max_results]:
                odata_type = m.pop("@odata.type", "") or ""
                members.append({**m, "member_type": odata_type.removeprefix("#microsoft.graph.")})
            next_token = skiptoken(result.get("@odata.nextLink"))
            return dump_json_capped(
                {
                    "group_id": group_id,
                    "count": len(members),
                    "members": members,
                    "has_more": next_token is not None,
                    "next_page_token": next_token,
                }
            )
        except GraphError as e:
            return e.to_envelope()
