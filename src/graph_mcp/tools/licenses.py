from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import GraphClient, GraphError
from ._common import NO_TOKEN


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_check_license_stock(
        sku_id: Annotated[str | None, Field(description="Filter to a specific SKU by its GUID.")] = None,
        sku_part_number: Annotated[
            str | None,
            Field(description='Filter by part number, e.g. "ENTERPRISEPACK" (case-insensitive).'),
        ] = None,
    ) -> str:
        """Check the tenant's license SKU inventory and remaining availability.

        Returns all subscribed SKUs when no filter is given; each includes an
        available count (prepaid enabled minus consumed).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            result = await client.get(
                "/subscribedSkus",
                params={
                    "$select": "skuId,skuPartNumber,prepaidUnits,consumedUnits,capabilityStatus"
                },
            )
            skus = result.get("value", [])

            if sku_id:
                skus = [s for s in skus if s.get("skuId") == sku_id]
            elif sku_part_number:
                skus = [
                    s
                    for s in skus
                    if s.get("skuPartNumber", "").upper() == sku_part_number.upper()
                ]

            enriched = []
            for sku in skus:
                enabled = sku.get("prepaidUnits", {}).get("enabled", 0)
                consumed = sku.get("consumedUnits", 0)
                enriched.append({**sku, "available": max(0, enabled - consumed)})

            return dump_json_capped({"skus": enriched, "total_skus": len(enriched)})
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
    )
    async def graph_assign_license(
        user_id: Annotated[str, Field(description="Object ID or UPN of the user to license.")],
        sku_id: Annotated[
            str | None,
            Field(
                description="SKU GUID to add (from graph_check_license_stock). Omit if only removing licenses."
            ),
        ] = None,
        disabled_plans: Annotated[
            list[str] | None,
            Field(
                description="Service plan GUIDs to disable within the SKU being added; ignored if sku_id is omitted."
            ),
        ] = None,
        remove_sku_ids: Annotated[
            list[str] | None, Field(description="SKU GUIDs to remove from the user.")
        ] = None,
    ) -> str:
        """Add and/or remove license SKUs on an Entra ID user in a single call.

        Microsoft Graph only accepts one combined add+remove request per
        call, so both directions are exposed here. At least one of sku_id or
        remove_sku_ids must be given. The user must have usage_location set
        (via graph_create_user/graph_update_user) before a SKU can be added.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        if not sku_id and not remove_sku_ids:
            return error_envelope(
                "invalid_argument",
                "Provide sku_id (to add), remove_sku_ids (to remove), or both.",
                False,
            )

        add_licenses = []
        if sku_id:
            license_entry: dict = {"skuId": sku_id}
            if disabled_plans:
                license_entry["disabledPlans"] = disabled_plans
            add_licenses.append(license_entry)

        body = {
            "addLicenses": add_licenses,
            "removeLicenses": remove_sku_ids or [],
        }

        try:
            result = await client.post(f"/users/{user_id}/assignLicense", body)
            return dump_json_capped(result)
        except GraphError as e:
            return e.to_envelope()
