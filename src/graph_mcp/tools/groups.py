from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import GraphClient, GraphError
from ._common import NO_TOKEN

_DIRECTORY_OBJECT_URL = "https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"

# Our own ceiling on how many results a list tool returns to the agent
# (SOP: default <=50, hard cap <=200). Separate from Graph's own $top
# per-page maximum, which is documented at 999 for these endpoints and is
# used below purely to size individual page requests.
_DEFAULT_MAX_RESULTS = 50
_HARD_CAP_MAX_RESULTS = 200
_GRAPH_TOP_MAX = 999


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

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_user_groups(
        user_id: Annotated[str, Field(description="User's id (GUID) or userPrincipalName.")],
        max_results: Annotated[
            int, Field(description="Max groups to return (default 50, hard cap 200).")
        ] = _DEFAULT_MAX_RESULTS,
    ) -> str:
        """List the Entra ID groups a user is a direct member of.

        Use to read an existing user's group memberships, e.g. to mirror
        them onto a new hire via graph_assign_groups.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        max_results = min(max_results, _HARD_CAP_MAX_RESULTS)

        try:
            groups: list = []
            result = await client.get(
                f"/users/{user_id}/memberOf",
                params={
                    "$select": "id,displayName,groupTypes,securityEnabled,mailEnabled",
                    "$top": str(min(max_results, _GRAPH_TOP_MAX)),
                },
            )
            capped = False
            while result and not capped:
                for obj in result.get("value", []):
                    # memberOf can also return directoryRoles; keep only groups.
                    if obj.get("@odata.type", "").endswith("group") or "groupTypes" in obj:
                        groups.append(obj)
                        if len(groups) >= max_results:
                            capped = True
                            break
                if capped:
                    break
                next_link = result.get("@odata.nextLink")
                if not next_link:
                    break
                result = await client.get(next_link)
            has_more = capped or bool(result and result.get("@odata.nextLink"))
            return dump_json_capped(
                {"user_id": user_id, "count": len(groups), "groups": groups, "has_more": has_more}
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
            int, Field(description="Max groups to return (default 50, hard cap 200).")
        ] = _DEFAULT_MAX_RESULTS,
    ) -> str:
        """List or search Entra ID groups by display name.

        Use to resolve a group name to its object id for graph_assign_groups.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        max_results = min(max_results, _HARD_CAP_MAX_RESULTS)
        page_size = min(max_results, _GRAPH_TOP_MAX)
        params: dict = {
            "$select": "id,displayName,groupTypes,securityEnabled,mailEnabled",
            "$top": str(page_size),
        }
        if display_name:
            if exact:
                params["$filter"] = f"displayName eq '{display_name}'"
            else:
                params["$filter"] = f"startswith(displayName,'{display_name}')"

        try:
            groups: list = []
            result = await client.get("/groups", params=params)
            has_more = False
            while result:
                groups.extend(result.get("value", []))
                if len(groups) >= max_results:
                    groups = groups[:max_results]
                    has_more = True
                    break
                next_link = result.get("@odata.nextLink")
                if not next_link:
                    break
                result = await client.get(next_link)
            return dump_json_capped({"count": len(groups), "groups": groups, "has_more": has_more})
        except GraphError as e:
            return e.to_envelope()

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
