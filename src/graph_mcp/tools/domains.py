from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped
from ..api_client import GraphClient, GraphError
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_domains(
        verified_only: Annotated[
            bool, Field(description="Return only verified domains (usable in a UPN).")
        ] = True,
    ) -> str:
        """List the tenant's domains, e.g. to pick the email domain for a new user.

        The default domain is flagged isDefault=true.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            result = await client.get(
                "/domains",
                params={"$select": "id,isDefault,isInitial,isVerified,authenticationType"},
            )
            domains = result.get("value", []) if isinstance(result, dict) else []
            if verified_only:
                domains = [d for d in domains if d.get("isVerified")]
            return dump_json_capped({"count": len(domains), "domains": domains})
        except GraphError as e:
            return e.to_envelope()
