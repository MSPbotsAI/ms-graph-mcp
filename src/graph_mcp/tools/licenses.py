import json
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from ..api_client import GraphClient, GraphError

_NO_TOKEN = "Error: No Graph access token. Send the X-Ms-Graph-Token header."


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool()
    async def graph_check_license_stock(
        sku_id: str | None = None,
        sku_part_number: str | None = None,
    ) -> str:
        """Query the tenant's subscribed SKUs to check license availability.

        Returns all SKUs when neither filter is provided.
        Required Graph scope: Organization.Read.All.

        Each SKU in the result includes an "available" field computed as
        prepaidUnits.enabled - consumedUnits.

        Note: Graph does not support $filter on /subscribedSkus — filtering
        is applied client-side after fetching all SKUs.

        Args:
            sku_id: Filter to a specific SKU by its GUID.
            sku_part_number: Filter by part number (e.g. "ENTERPRISEPACK"). Case-insensitive.
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

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

            return json.dumps({"skus": enriched, "total_skus": len(enriched)}, indent=2)
        except GraphError as e:
            return f"Error: {e}"

    @mcp.tool()
    async def graph_assign_license(
        user_id: str,
        sku_id: str,
        disabled_plans: list[str] | None = None,
    ) -> str:
        """Assign a license SKU to an Entra ID user.

        The user must have usageLocation set (set by graph_create_user).
        Required Graph scope: User.ReadWrite.All.

        Args:
            user_id: Object ID or UPN of the user to license.
            sku_id: SKU GUID to assign (from graph_check_license_stock).
            disabled_plans: Optional list of service plan GUIDs to disable within the SKU.
        """
        client = client_factory()
        if client is None:
            return _NO_TOKEN

        license_entry: dict = {"skuId": sku_id}
        if disabled_plans:
            license_entry["disabledPlans"] = disabled_plans

        body = {
            "addLicenses": [license_entry],
            "removeLicenses": [],
        }

        try:
            result = await client.post(f"/users/{user_id}/assignLicense", body)
            return json.dumps(result, indent=2)
        except GraphError as e:
            return f"Error: {e}"
