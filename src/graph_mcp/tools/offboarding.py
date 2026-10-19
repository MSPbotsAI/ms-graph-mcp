"""Offboarding composite tools: OneDrive archive-and-share and directory
synchronization trigger (PRD-20422).

graph_transfer_onedrive follows the Rewst flow Ginko already runs ("Grant
OneDrive Sharing Permissions", see Mandy Du's 2026-10-14 comment on
PRD-20422): everything in the departing user's OneDrive root is moved into
one archive folder *inside that same OneDrive*, and the folder is shared
with the recipient. Nothing is copied to the recipient's drive and
ownership does not change -- the first INT round (copy into the recipient's
drive) needed access to two drives and failed in real offboarding.

Access: Graph answers 403 when the caller cannot open the user's OneDrive.
That is the normal case on the delegated ms-graph connector -- even a Global
Admin is not a site collection admin of other people's OneDrives -- so 403
is reported as access_denied with that hint, never folded into "no
OneDrive" (the first round did exactly that and hid the cause). The
app-only ms-graph-app connector with Files.ReadWrite.All has no such limit.

Retry: stateless like every tool here (stateless_http=True, server.py).
The folder is reused by name, items already inside it are left alone, and
the share is skipped when the recipient already has it, so calling again
finishes whatever the previous call left.

graph_start_cloud_sync only supports Azure AD Connect **cloud sync** -- the
one directory-sync mechanism Microsoft Graph can actually trigger. A tenant
using classic on-premises AD Connect instead has no Graph API surface for
this at all; the tool says so explicitly rather than silently doing nothing
or claiming success (see docs/tickets/PRD-20422.md, AC10/Open Questions).
"""

from collections.abc import Callable
from typing import Annotated
from urllib.parse import quote

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

_DEFAULT_ARCHIVE_FOLDER = "Offboarding_Transfer"
_SHARE_ROLES = {"write", "owner"}

_ACCESS_DENIED_HINT = (
    "The caller cannot open this user's OneDrive. On the delegated ms-graph connector the "
    "signed-in admin must be a site collection admin of this user's OneDrive (Global Admin "
    "alone is not enough); or use the ms-graph-app connector with the Files.ReadWrite.All "
    "application permission."
)


def _access_denied(e: GraphError, step: str) -> str:
    return error_envelope(
        "access_denied",
        _ACCESS_DENIED_HINT,
        False,
        extra={"step": step, "graph_status": e.status_code, "graph_message": e.message},
    )


async def _resolve_user(client: GraphClient, user_id: str, role: str) -> dict | str:
    try:
        user = await client.get(
            f"/users/{user_id}", params={"$select": "id,mail,userPrincipalName"}
        )
    except GraphError as e:
        if e.status_code == 404:
            return error_envelope(
                "user_not_found",
                f'The {role} user "{user_id}" does not exist in this tenant.',
                False,
                extra={"role": role, "user_id": user_id},
            )
        return e.to_envelope()
    return user


async def _list_all_children(
    client: GraphClient, path: str, select: str | None = None
) -> list[dict]:
    """Walk every @odata.nextLink page internally -- unlike this repo's
    other list tools (deliberately single-page-per-call), this tool acts on
    every item in one invocation and does not let the caller resume a list.
    """
    return await _list_all(client, path, {"$select": select} if select else None)


async def _list_all(client: GraphClient, path: str, params: dict | None) -> list[dict]:
    """Follow @odata.nextLink until the collection is exhausted."""
    items: list[dict] = []
    next_path: str | None = path
    while next_path:
        result = await client.get(next_path, params=params)
        items.extend(result.get("value", []) if isinstance(result, dict) else [])
        next_path = result.get("@odata.nextLink") if isinstance(result, dict) else None
        params = None  # the nextLink URL already carries its own query string
    return items


def _recipient_has_access(permissions: list[dict], recipient: dict) -> bool:
    ids = {recipient.get("id")}
    emails = {
        (v or "").lower() for v in (recipient.get("mail"), recipient.get("userPrincipalName")) if v
    }
    for perm in permissions:
        if not _SHARE_ROLES & set(perm.get("roles") or []):
            continue
        users = [
            (perm.get("grantedToV2") or {}).get("user") or {},
            (perm.get("grantedTo") or {}).get("user") or {},
        ]
        users += [(i.get("user") or {}) for i in perm.get("grantedToIdentitiesV2") or []]
        for u in users:
            if u.get("id") in ids or (u.get("email") or "").lower() in emails:
                return True
        if ((perm.get("invitation") or {}).get("email") or "").lower() in emails:
            return True
    return False


async def _discover_cloud_sync_service_principal(client: GraphClient) -> dict | str:
    """Find candidate Azure AD Connect cloud-sync agent service principals.
    Returns {"candidates": [...]} (possibly empty or with more than one) or
    a GraphError envelope -- the caller applies AC10/AC11's distinct
    zero/ambiguous messages.
    """
    # Every page, not just the first: Graph pages /servicePrincipals at 100
    # by default and the agent's principal can sit anywhere in a tenant with
    # more than that. homepage does not support $filter, so the match stays
    # client-side.
    try:
        service_principals = await _list_all(
            client,
            "/servicePrincipals",
            {
                "$select": "id,homepage,accountEnabled",
                "$filter": "accountEnabled eq true",
                "$top": "999",
            },
        )
    except GraphError as e:
        return e.to_envelope()
    candidates = [
        sp
        for sp in service_principals
        if _CLOUD_SYNC_AGENT_HOMEPAGE_FRAGMENT in (sp.get("homepage") or "")
    ]
    return {"candidates": candidates}


async def _discover_cloud_sync_job(client: GraphClient, service_principal_id: str) -> dict | str:
    try:
        jobs = await _list_all(
            client, f"/servicePrincipals/{service_principal_id}/synchronization/jobs", None
        )
    except GraphError as e:
        return e.to_envelope()
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
            Field(description="Object ID or UPN of the departing user whose OneDrive is archived."),
        ],
        recipient_user_id: Annotated[
            str,
            Field(
                description="Object ID or UPN of the user (e.g. the manager) who gets write "
                "access to the archive folder."
            ),
        ],
        folder_name: Annotated[
            str,
            Field(
                description="Archive folder created in the root of the departing user's "
                "OneDrive; reused if it already exists."
            ),
        ] = _DEFAULT_ARCHIVE_FOLDER,
    ) -> str:
        """Offboarding: move everything in the departing user's OneDrive root
        into an archive folder in that same OneDrive and share it with the
        recipient (write). No copy, no ownership change; the OneDrive is
        deleted ~30 days after license removal. Status: completed / partial
        (call again to retry) / no_onedrive / no_content. access_denied: on
        delegated ms-graph the admin must be site collection admin of that
        OneDrive, or use ms-graph-app.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        source = await _resolve_user(client, source_user_id, "source")
        if isinstance(source, str):
            return source
        recipient = await _resolve_user(client, recipient_user_id, "recipient")
        if isinstance(recipient, str):
            return recipient

        base = {"source_user_id": source_user_id, "recipient_user_id": recipient_user_id}

        try:
            drive = await client.get(f"/users/{source['id']}/drive", params={"$select": "id"})
        except GraphError as e:
            if e.status_code in (401, 403):
                return _access_denied(e, "open_drive")
            if e.status_code == 404:
                return dump_json_capped(
                    {
                        **base,
                        "status": "no_onedrive",
                        "reason": "The departing user has no OneDrive; nothing to transfer.",
                    }
                )
            return e.to_envelope()
        drive_id = drive.get("id")

        try:
            folder = await client.get(
                f"/drives/{drive_id}/root:/{quote(folder_name)}:",
                params={"$select": "id,name,folder"},
            )
        except GraphError as e:
            if e.status_code in (401, 403):
                return _access_denied(e, "find_folder")
            if e.status_code != 404:
                return e.to_envelope()
            folder = None
        if folder is not None and "folder" not in folder:
            return error_envelope(
                "invalid_argument",
                f'"{folder_name}" already exists in the OneDrive root and is a file, not a '
                "folder; pass a different folder_name.",
                False,
            )

        try:
            root_items = await _list_all_children(
                client, f"/drives/{drive_id}/root/children", "id,name"
            )
        except GraphError as e:
            if e.status_code in (401, 403):
                return _access_denied(e, "list_root")
            return e.to_envelope()
        to_move = [i for i in root_items if folder is None or i.get("id") != folder.get("id")]

        if folder is None and not to_move:
            return dump_json_capped({**base, "status": "no_content", "moved": [], "failed": []})

        if folder is None:
            try:
                folder = await client.post(
                    f"/drives/{drive_id}/root/children",
                    {
                        "name": folder_name,
                        "folder": {},
                        "@microsoft.graph.conflictBehavior": "fail",
                    },
                )
            except GraphError as e:
                if e.status_code in (401, 403):
                    return _access_denied(e, "create_folder")
                return e.to_envelope()
        folder_id = folder.get("id")

        moved: list[str] = []
        failed: list[dict] = []
        for item in to_move:
            try:
                await client.patch(
                    f"/drives/{drive_id}/items/{item.get('id')}",
                    {"parentReference": {"id": folder_id}},
                )
            except GraphError as e:
                failed.append(
                    {"name": item.get("name"), "graph_status": e.status_code, "error": e.message}
                )
                continue
            moved.append(item.get("name"))

        share: dict
        try:
            permissions = await _list_all_children(
                client, f"/drives/{drive_id}/items/{folder_id}/permissions"
            )
            if _recipient_has_access(permissions, recipient):
                share = {"status": "already_shared"}
            else:
                email = recipient.get("mail") or recipient.get("userPrincipalName")
                target = {"email": email} if email else {"objectId": recipient["id"]}
                await client.post(
                    f"/drives/{drive_id}/items/{folder_id}/invite",
                    {
                        "recipients": [target],
                        "roles": ["write"],
                        "requireSignIn": True,
                        "sendInvitation": True,
                        "message": "Files from an offboarded user's OneDrive have been shared "
                        "with you.",
                    },
                )
                share = {"status": "shared", "email": email}
        except GraphError as e:
            share = {"status": "failed", "graph_status": e.status_code, "error": e.message}

        status = "completed" if not failed and share["status"] != "failed" else "partial"
        return dump_json_capped(
            {
                **base,
                "status": status,
                "folder": {"id": folder_id, "name": folder.get("name") or folder_name},
                "share": share,
                "moved_count": len(moved),
                "failed": failed,
                "moved": moved,
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
