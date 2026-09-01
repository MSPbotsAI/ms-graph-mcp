"""Intune-managed device tools (Microsoft Graph deviceManagement API).

Scope note: list + delete only, for the offboarding "does this user still
have an enrolled device" step. Deleting a managedDevice removes Intune's
record of it outright — it does not selectively wipe/retire company data
first (that's a separate action this fleet doesn't expose). Nothing here
touches compliance policies, configuration profiles, or app protection —
those are separate Intune domains with no offboarding use case here.
"""

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
    async def graph_list_managed_devices(
        user_id: Annotated[str, Field(description="User's id (GUID) or userPrincipalName.")],
    ) -> str:
        """List a user's Intune-enrolled (managed) devices.

        Use before offboarding: check whether a departing user still has
        a company-managed device enrolled, and get its id, before
        removing it with graph_remove_managed_device. Requires an active
        Intune license on the tenant — returns an empty list (not an
        error) for a user with no enrolled devices.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(
                f"/users/{user_id}/managedDevices",
                params={
                    "$select": "id,deviceName,operatingSystem,managedDeviceOwnerType,"
                    "complianceState,enrolledDateTime,lastSyncDateTime"
                },
            )
            devices = result.get("value", []) if isinstance(result, dict) else []
            return dump_json_capped({"user_id": user_id, "count": len(devices), "devices": devices})
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True)
    )
    async def graph_remove_managed_device(
        device_id: Annotated[
            str,
            Field(
                description="Managed device's id, from graph_list_managed_devices — "
                "never guess this, resolve it first."
            ),
        ],
    ) -> str:
        """Permanently delete a device's Intune management record.

        This removes Intune's record of the device entirely (not a
        selective corporate-data wipe/retire — this tool doesn't expose
        that separate action). Use for offboarding once
        graph_list_managed_devices has confirmed which device(s) belong
        to the departing user; confirm the exact device_id with the user
        before calling. Idempotent: deleting an already-removed device id
        returns success, not an error.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            await client.delete(f"/deviceManagement/managedDevices/{device_id}")
        except GraphError as e:
            if e.status_code != 404:
                return e.to_envelope()
        return dump_json_capped({"device_id": device_id, "deleted": True})
