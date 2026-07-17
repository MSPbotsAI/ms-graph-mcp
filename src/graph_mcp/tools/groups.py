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
