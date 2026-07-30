import json
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from ..api_client import GraphClient, GraphError

_NO_TOKEN = "Error: No Graph access token. Send the X-Ms-Graph-Token header."

_DIRECTORY_OBJECT_URL = "https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool()
    async def graph_assign_groups(
        user_id: str,
        group_ids: list[str],
    ) -> str:
        """Add an Entra ID user to one or more groups.

        Required Graph scopes: GroupMember.ReadWrite.All for standard groups;
        Group.ReadWrite.All for role-assignable groups.

        Processes all group_ids and returns per-group results. Already-a-member
        errors are treated as success (idempotent).

        Args:
            user_id: Object ID of the user to add.
            group_ids: List of group object IDs to add the user to.
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

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
                if e.status_code == 400 and "already exist" in str(e).lower():
                    results.append({"group_id": group_id, "status": "already_member"})
                else:
                    results.append({"group_id": group_id, "status": "error", "detail": str(e)})

        return json.dumps({"user_id": user_id, "results": results}, indent=2)

    @mcp.tool()
    async def graph_list_user_groups(user_id: str, max_results: int = 200) -> str:
        """List the groups an Entra ID user is a direct member of.

        Use this to read a "model" employee's group memberships so they can be
        mirrored onto a new hire (feed the returned group ids into
        graph_assign_groups). Results are paginated internally via @odata.nextLink
        and capped at max_results.

        Required Graph scope: GroupMember.Read.All or Directory.Read.All.

        Args:
            user_id: The user's id (GUID) or userPrincipalName.
            max_results: Hard cap on the number of groups returned (default 200).
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

        try:
            groups: list = []
            result = await client.get(
                f"/users/{user_id}/memberOf",
                params={
                    "$select": "id,displayName,groupTypes,securityEnabled,mailEnabled",
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
            return json.dumps(
                {"user_id": user_id, "count": len(groups), "groups": groups},
                indent=2,
            )
        except GraphError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def graph_list_groups(
        display_name: str | None = None,
        exact: bool = False,
        max_results: int = 100,
    ) -> str:
        """List / search Entra ID groups, optionally by display name.

        Use this to resolve a human-readable group name to its object id so it can
        be passed to graph_assign_groups. Pagination is capped at max_results so an
        unfiltered call never walks the entire directory (which would time out on a
        large tenant); narrow the result set with display_name.

        Required Graph scope: Group.Read.All or Directory.Read.All.

        Args:
            display_name: Filter by display name. Prefix match by default; set
                exact=True for an exact match. Omit to list all groups (capped).
            exact: When True, match display_name exactly; otherwise prefix match.
            max_results: Hard cap on the number of groups returned (default 100).
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

        page_size = min(max_results, 999)
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
            while result:
                groups.extend(result.get("value", []))
                if len(groups) >= max_results:
                    groups = groups[:max_results]
                    break
                next_link = result.get("@odata.nextLink")
                if not next_link:
                    break
                result = await client.get(next_link)
            return json.dumps({"count": len(groups), "groups": groups}, indent=2)
        except GraphError as e:
            return f"Error: {e}"
