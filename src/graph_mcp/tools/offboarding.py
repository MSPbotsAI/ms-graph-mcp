"""Offboarding composite tools: OneDrive content transfer and directory
synchronization trigger (PRD-20422).

Neither tool has a natural home in users.py/groups.py/sites.py the way
graph_remove_manager (users.py) is offboarding-specific without living in a
dedicated "lifecycle" module — one is a cross-drive copy operation, the
other a directory-sync trigger.

graph_transfer_onedrive is deliberately stateless like every other tool in
this server (stateless_http=True, server.py) — it has no job id to track
between calls, so "is it done yet" and "retry" are the same call: each
invocation re-lists both drives and diffs by name+size, acting only on
items not already present at the destination.

graph_start_cloud_sync only supports Azure AD Connect **cloud sync** — the
one directory-sync mechanism Microsoft Graph can actually trigger. A tenant
using classic on-premises AD Connect instead has no Graph API surface for
this at all; the tool says so explicitly rather than silently doing nothing
or claiming success (see docs/tickets/PRD-20422.md, AC10/Open Questions).
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import GraphClient, GraphError
from ._common import NO_TOKEN

# Per Mandy Du's 2026-10-14 comment on PRD-20422 (Rewst GraphQL snapshot of
# Ginko "08 User Offboarding", 99 workflows, cross-checked against EMD W3):
# the tenant's Azure AD Connect cloud-sync agent service principal is the
# one whose `homepage` contains this fragment and is still enabled; its
# correct synchronization job is the one whose templateId matches it and
# whose schedule is Active. This is evidence from Rewst's own working
# implementation, not a guess -- see docs/tickets/PRD-20422.md "Ticket
# thread" for the full quoted comment.
_CLOUD_SYNC_AGENT_HOMEPAGE_FRAGMENT = "AD2AADProvisioning"
_CLOUD_SYNC_JOB_TEMPLATE_ID = "AD2AADProvisioning"


async def _resolve_drive(client: GraphClient, user_id: str, role: str) -> dict | str:
    """Resolve a user's OneDrive id, or an error_envelope naming which side
    (source/destination) failed -- AC5/AC7 both fold into the same
    drive_not_found code, distinguished only by the `role` extra field.
    """
    try:
        drive = await client.get(f"/users/{user_id}/drive", params={"$select": "id,driveType"})
    except GraphError:
        return error_envelope(
            "drive_not_found",
            f'No OneDrive provisioned for the {role} user "{user_id}".',
            False,
            extra={"role": role, "user_id": user_id},
        )
    return {"id": drive.get("id")}


async def _list_all_children(client: GraphClient, path: str) -> list[dict]:
    """Walk every @odata.nextLink page internally -- unlike this repo's
    other list tools (deliberately single-page-per-call), this tool acts on
    every item in one invocation and does not let the caller resume a list.
    """
    items: list[dict] = []
    next_path: str | None = path
    params: dict | None = {"$select": "id,name,size"}
    while next_path:
        result = await client.get(next_path, params=params)
        items.extend(result.get("value", []) if isinstance(result, dict) else [])
        next_path = result.get("@odata.nextLink") if isinstance(result, dict) else None
        params = None  # the nextLink URL already carries its own query string
    return items


async def _resolve_or_create_destination_folder(
    client: GraphClient, dest_drive_id: str, folder_name: str
) -> dict | str:
    try:
        folder = await client.get(
            f"/drives/{dest_drive_id}/root:/{folder_name}:", params={"$select": "id,name"}
        )
        return {"id": folder.get("id"), "name": folder.get("name")}
    except GraphError as e:
        if e.status_code != 404:
            return e.to_envelope()
    try:
        folder = await client.post(
            f"/drives/{dest_drive_id}/root/children",
            {"name": folder_name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"},
        )
    except GraphError as e:
        return e.to_envelope()
    return {"id": folder.get("id"), "name": folder.get("name")}


def _rollup_transfer_status(results: list[dict]) -> str:
    """AC2-AC4's status roll-up: any still-copying item makes the whole
    call in_progress; all-already-present is completed; all-freshly-started
    is started; a mix of freshly-started and already-present (no
    in_progress) is also in_progress -- nothing to report as fully done yet.
    """
    actions = {r["action"] for r in results}
    if "in_progress" in actions:
        return "in_progress"
    if actions == {"already_present"}:
        return "completed"
    if "already_present" in actions and "copy_started" in actions:
        return "in_progress"
    return "started"


async def _discover_cloud_sync_service_principal(client: GraphClient) -> dict | str:
    """Find candidate Azure AD Connect cloud-sync agent service principals.
    Returns {"candidates": [...]} (possibly empty or with more than one) or
    a GraphError envelope -- the caller applies AC10/AC11's distinct
    zero/ambiguous messages.
    """
    try:
        result = await client.get(
            "/servicePrincipals",
            params={"$select": "id,homepage,accountEnabled", "$filter": "accountEnabled eq true"},
        )
    except GraphError as e:
        return e.to_envelope()
    candidates = [
        sp
        for sp in (result.get("value", []) if isinstance(result, dict) else [])
        if _CLOUD_SYNC_AGENT_HOMEPAGE_FRAGMENT in (sp.get("homepage") or "")
    ]
    return {"candidates": candidates}


async def _discover_cloud_sync_job(client: GraphClient, service_principal_id: str) -> dict | str:
    try:
        result = await client.get(
            f"/servicePrincipals/{service_principal_id}/synchronization/jobs"
        )
    except GraphError as e:
        return e.to_envelope()
    jobs = result.get("value", []) if isinstance(result, dict) else []
    candidates = [
        job
        for job in jobs
        if job.get("templateId") == _CLOUD_SYNC_JOB_TEMPLATE_ID
        and (job.get("schedule") or {}).get("state") == "Active"
    ]
    return {"candidates": candidates}


_NO_SYNC_JOB_MESSAGE = (
    "No active Azure AD Connect cloud-sync job found for this tenant. This MCP cannot "
    "reach classic on-premises Azure AD Connect -- if this tenant uses that instead, run "
    "Start-ADSyncSyncCycle on the Connect server."
)


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool(
        annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True)
    )
    async def graph_transfer_onedrive(
        source_user_id: Annotated[
            str,
            Field(
                description="Object ID or UPN of the departing user whose OneDrive content "
                "is being transferred."
            ),
        ],
        destination_user_id: Annotated[
            str, Field(description="Object ID or UPN of the user or admin receiving the content.")
        ],
        destination_folder_name: Annotated[
            str | None,
            Field(
                description="Name of the top-level folder created in the destination's "
                'OneDrive root. Defaults to "offboarded-{source_user_id}".'
            ),
        ] = None,
    ) -> str:
        """Copy a departing user's OneDrive root content into a destination
        user's or admin's OneDrive, for offboarding workflows 08/03.

        Safe to call repeatedly: each call re-lists both drives and only
        copies items not already present at the destination, so calling it
        again *is* the status check. Returns started/in_progress/
        completed/no_content, never a bare boolean. Copies into a new
        folder -- no ownership transfer, no sharing/version history.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        source_drive = await _resolve_drive(client, source_user_id, "source")
        if isinstance(source_drive, str):
            return source_drive

        dest_drive = await _resolve_drive(client, destination_user_id, "destination")
        if isinstance(dest_drive, str):
            return dest_drive

        try:
            source_items = await _list_all_children(
                client, f"/drives/{source_drive['id']}/root/children"
            )
        except GraphError as e:
            return e.to_envelope()

        if not source_items:
            return dump_json_capped(
                {
                    "source_user_id": source_user_id,
                    "destination_user_id": destination_user_id,
                    "status": "no_content",
                    "items": [],
                }
            )

        folder_name = destination_folder_name or f"offboarded-{source_user_id}"
        dest_folder = await _resolve_or_create_destination_folder(
            client, dest_drive["id"], folder_name
        )
        if isinstance(dest_folder, str):
            return dest_folder

        try:
            dest_children = await _list_all_children(
                client, f"/drives/{dest_drive['id']}/items/{dest_folder['id']}/children"
            )
        except GraphError as e:
            return e.to_envelope()
        dest_by_name = {c.get("name"): c.get("size") for c in dest_children}

        results: list[dict] = []
        for item in source_items:
            name = item.get("name")
            size = item.get("size")
            if name in dest_by_name:
                if dest_by_name[name] == size:
                    results.append({"name": name, "size": size, "action": "already_present"})
                else:
                    results.append({"name": name, "size": size, "action": "in_progress"})
                continue
            try:
                await client.post(
                    f"/drives/{source_drive['id']}/items/{item.get('id')}/copy",
                    {"parentReference": {"driveId": dest_drive["id"], "id": dest_folder["id"]}},
                )
            except GraphError as e:
                return e.to_envelope()
            results.append({"name": name, "size": size, "action": "copy_started"})

        return dump_json_capped(
            {
                "source_user_id": source_user_id,
                "destination_user_id": destination_user_id,
                "destination_folder": dest_folder,
                "status": _rollup_transfer_status(results),
                "items": results,
            }
        )

    @mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=False, idempotentHint=False
        )
    )
    async def graph_start_cloud_sync(
        user_id: Annotated[
            str,
            Field(
                description="Object ID or UPN of the user whose directory change should "
                "trigger tenant synchronization."
            ),
        ],
        sync_job_id: Annotated[
            str | None,
            Field(
                description="Escape hatch: an explicit Azure AD Connect cloud-sync job id, "
                "bypassing job auto-discovery -- use after a sync_job_ambiguous response, or "
                "when auto-discovery picks the wrong job. The owning service principal is "
                "still auto-discovered."
            ),
        ] = None,
    ) -> str:
        """Start the tenant's Azure AD Connect cloud-sync job after an
        offboarding directory change, for offboarding workflows 08/03.

        Only supports Azure AD Connect cloud sync. A tenant on classic
        on-premises AD Connect instead gets no_sync_job_configured naming
        the on-prem Start-ADSyncSyncCycle alternative, never a silent
        no-op. Returns status=not_applicable (not an error) when the
        user's onPremisesSyncEnabled is false -- nothing to sync.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        try:
            user = await client.get(
                f"/users/{user_id}", params={"$select": "onPremisesSyncEnabled"}
            )
        except GraphError as e:
            return e.to_envelope()

        if not user.get("onPremisesSyncEnabled"):
            return dump_json_capped(
                {
                    "user_id": user_id,
                    "status": "not_applicable",
                    "reason": "onPremisesSyncEnabled is false for this user -- nothing to "
                    "synchronize.",
                }
            )

        sp_result = await _discover_cloud_sync_service_principal(client)
        if isinstance(sp_result, str):
            return sp_result
        sp_candidates = sp_result["candidates"]
        if not sp_candidates:
            return error_envelope("no_sync_job_configured", _NO_SYNC_JOB_MESSAGE, False)
        if len(sp_candidates) > 1:
            return error_envelope(
                "sync_job_ambiguous",
                "Multiple candidate Azure AD Connect cloud-sync service principals were "
                "found; specify sync_job_id explicitly once the correct one is confirmed.",
                False,
                extra={"candidates": [sp.get("id") for sp in sp_candidates]},
            )
        service_principal_id = sp_candidates[0]["id"]

        if sync_job_id:
            job_id = sync_job_id
        else:
            job_result = await _discover_cloud_sync_job(client, service_principal_id)
            if isinstance(job_result, str):
                return job_result
            job_candidates = job_result["candidates"]
            if not job_candidates:
                return error_envelope("no_sync_job_configured", _NO_SYNC_JOB_MESSAGE, False)
            if len(job_candidates) > 1:
                return error_envelope(
                    "sync_job_ambiguous",
                    "Multiple candidate Azure AD Connect cloud-sync jobs were found; specify "
                    "sync_job_id explicitly.",
                    False,
                    extra={"candidates": [job.get("id") for job in job_candidates]},
                )
            job_id = job_candidates[0]["id"]

        try:
            await client.post(
                f"/servicePrincipals/{service_principal_id}/synchronization/jobs/{job_id}/start"
            )
        except GraphError as e:
            return e.to_envelope()

        return dump_json_capped(
            {
                "user_id": user_id,
                "status": "started",
                "service_principal_id": service_principal_id,
                "job_id": job_id,
            }
        )
