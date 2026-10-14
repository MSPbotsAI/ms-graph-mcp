"""SharePoint site/document tools (Microsoft Graph Sites & Drives APIs).

Scope note: these tools cover TEXT-based files only (.txt/.md/.csv/.json and
similar) — reading decodes content as UTF-8, writing/creating rejects
anything that doesn't already look like a text file. Binary Office
documents (.docx/.xlsx/.pdf) are deliberately out of scope: reading one
back as inline tool output would mean stuffing a base64-encoded blob
through the calling agent's own context window, which this fleet's tools
cap around 20,000 characters — workable for a short text file, not for a
multi-hundred-KB document. graph_get_file's returned downloadUrl is the
escape hatch for binary content: a pre-authenticated, time-limited direct
link a caller can fetch independently of this MCP's own response-size
limits. create/write/delete are split into three separate tools rather
than one upsert-style tool, deliberately: graph_create_file_text fails if
something already exists at the path (never silently overwrites),
graph_write_file_text only ever targets an existing item id (never
silently creates), and graph_delete_file is the one place anything is
permanently removed.
"""

from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .._json import dump_json_capped, error_envelope
from ..api_client import GraphClient, GraphError
from ._common import NO_TOKEN

# Real bytes-on-the-wire limits, not our own arbitrary choice:
# - _MAX_READ_BYTES: generous enough for real text/markdown/CSV SOP
#   documents, small enough that decoded UTF-8 plus JSON-envelope overhead
#   stays under dump_json_capped's ~20,000-char cap.
# - _MAX_WRITE_BYTES: Graph's own simple-upload endpoint (PUT .../content)
#   only accepts up to 4MB; above that it requires a resumable upload
#   session, not implemented here. We cap well below 4MB since the content
#   arrives as a single inline tool argument (the calling agent's own
#   context has to hold it first).
_MAX_READ_BYTES = 200_000
_MAX_WRITE_BYTES = 500_000
_MAX_SITE_DRIVE_ENRICH = 5


async def _resolve_site(
    client: GraphClient, site_query: str, site_id: str | None
) -> dict | str:
    """Resolve a site to {"id","displayName","webUrl"}, or return an
    error_envelope string. site_id wins when both are given (PRD-20413 AC5).
    """
    if site_id:
        try:
            site = await client.get(
                f"/sites/{site_id}", params={"$select": "id,name,displayName,webUrl"}
            )
        except GraphError:
            return error_envelope(
                "site_not_found",
                f'No site found for site_id "{site_id}".',
                False,
                extra={"site_id": site_id},
            )
        return {
            "id": site.get("id"),
            "displayName": site.get("displayName"),
            "webUrl": site.get("webUrl"),
        }

    try:
        result = await client.get(
            "/sites", params={"search": site_query, "$select": "id,name,displayName,webUrl"}
        )
    except GraphError as e:
        return e.to_envelope()
    sites = result.get("value", []) if isinstance(result, dict) else []
    if not sites:
        return error_envelope(
            "site_not_found",
            f'No site found matching "{site_query}".',
            False,
            extra={"searched_for": site_query},
        )
    exact = [s for s in sites if (s.get("displayName") or "").casefold() == site_query.casefold()]
    if len(exact) == 1:
        chosen = exact[0]
    elif len(exact) == 0 and len(sites) == 1:
        chosen = sites[0]
    else:
        candidates = [
            {"id": s.get("id"), "displayName": s.get("displayName"), "webUrl": s.get("webUrl")}
            for s in sites
        ]
        return error_envelope(
            "site_ambiguous",
            f'Multiple sites match "{site_query}" and none is an exact name match.',
            False,
            extra={"candidates": candidates},
        )
    return {
        "id": chosen.get("id"),
        "displayName": chosen.get("displayName"),
        "webUrl": chosen.get("webUrl"),
    }


async def _resolve_drive(client: GraphClient, site_id: str) -> dict | str:
    """Resolve a site's default document library. Any failure here
    (including "no default library") folds into file_not_found per the
    PRD-20413 design's Open Questions decision — the ticket's own error
    table has no separate code for "this site has no library at all".
    """
    try:
        drive = await client.get(f"/sites/{site_id}/drive", params={"$select": "id,name"})
    except GraphError:
        return error_envelope(
            "file_not_found",
            "Site has no default document library.",
            False,
            extra={"reason": "site has no default document library"},
        )
    return {"id": drive.get("id"), "name": drive.get("name")}


async def _resolve_file(
    client: GraphClient, drive_id: str, file_name: str, folder_path: str | None
) -> dict | str:
    """Resolve file_name (optionally under folder_path) to a drive item via
    Graph's root:/{path}: addressing (same mechanism graph_create_file_text
    already uses). 404 and folder-instead-of-file both fold into
    file_not_found, per the ticket's four-code error table.
    """
    path = f"{folder_path.strip('/')}/{file_name}" if folder_path else file_name
    try:
        item = await client.get(
            f"/drives/{drive_id}/root:/{path}:",
            params={"$select": "id,name,size,file,folder,lastModifiedDateTime"},
        )
    except GraphError as e:
        if e.status_code != 404:
            return e.to_envelope()
        listing_path = (
            f"/drives/{drive_id}/root:/{folder_path.strip('/')}:/children"
            if folder_path
            else f"/drives/{drive_id}/root/children"
        )
        try:
            listing = await client.get(listing_path, params={"$select": "name"})
            entries = listing.get("value", []) if isinstance(listing, dict) else []
            names = [entry.get("name") for entry in entries]
        except GraphError:
            # The folder itself doesn't exist either — an empty list is the
            # honest answer, not a second error for the caller to handle.
            names = []
        return error_envelope(
            "file_not_found",
            f'No file named "{file_name}" found.',
            False,
            extra={"files_in_folder": names},
        )
    if item.get("folder") is not None:
        return error_envelope(
            "file_not_found",
            f'"{path}" is a folder, not a file.',
            False,
            extra={"reason": "path is a folder, not a file"},
        )
    return item


def register(mcp: FastMCP, client_factory: Callable[[], GraphClient | None]) -> None:

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_search_sites(
        query: Annotated[
            str, Field(description="Search text — matches against the site's name/title/URL.")
        ],
    ) -> str:
        """Find SharePoint sites by name/keyword — the first step for any
        SharePoint request that names a site in words instead of an id.

        Use for "find the HR site", "does Finance have a SharePoint site",
        "what's the URL for the onboarding site". Each result already
        includes driveId/driveName for that site's default document
        library (resolved server-side, first 5 matches only) — pass
        driveId straight into graph_list_drive_items, no separate lookup.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(
                "/sites",
                params={
                    "search": query,
                    "$select": "id,name,displayName,webUrl",
                },
            )
        except GraphError as e:
            return e.to_envelope()
        sites = result.get("value", []) if isinstance(result, dict) else []
        for site in sites[:_MAX_SITE_DRIVE_ENRICH]:
            try:
                drive = await client.get(
                    f"/sites/{site['id']}/drive", params={"$select": "id,name"}
                )
                site["driveId"] = drive.get("id")
                site["driveName"] = drive.get("name")
            except GraphError:
                # A site with no default document library (e.g. a
                # communication site with libraries disabled) is not an
                # error for this call — just leave driveId/driveName unset.
                continue
        return dump_json_capped({"count": len(sites), "sites": sites})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_list_drive_items(
        drive_id: Annotated[
            str, Field(description="Document library's drive id, from graph_search_sites.")
        ],
        folder_id: Annotated[
            str | None,
            Field(description="Folder item id to list inside. Omit to list the library's root."),
        ] = None,
    ) -> str:
        """List the files and folders directly inside a document library,
        or one folder within it — browsing, not searching by name.

        Use for "what's in the Policies folder", "show me the files in
        that library". Each entry's folder{...} vs file{...} key tells you
        which; get a file's id here before graph_get_file/
        graph_read_file_text. Not recursive — to go deeper, call again
        passing that entry's id as folder_id.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        path = (
            f"/drives/{drive_id}/items/{folder_id}/children"
            if folder_id
            else f"/drives/{drive_id}/root/children"
        )
        try:
            result = await client.get(
                path,
                params={
                    "$select": "id,name,size,folder,file,lastModifiedDateTime,webUrl",
                    "$top": "200",
                },
            )
            items = result.get("value", []) if isinstance(result, dict) else []
            return dump_json_capped({"count": len(items), "items": items})
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_get_file(
        drive_id: Annotated[str, Field(description="Document library's drive id.")],
        item_id: Annotated[
            str, Field(description="File's item id, from graph_list_drive_items.")
        ],
    ) -> str:
        """Get a file's metadata (name, size, MIME type) and a
        pre-authenticated temporary download link — NOT the file's content.

        Use for "send me a link to this file" or as a size/type check
        before graph_read_file_text. The returned downloadUrl works for
        ANY file (including .docx/.xlsx/.pdf), fetched outside this MCP.
        For what a document actually says, use graph_read_file_text —
        small text files only.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            result = await client.get(f"/drives/{drive_id}/items/{item_id}")
            return dump_json_capped(result)
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_read_file_text(
        drive_id: Annotated[str, Field(description="Document library's drive id.")],
        item_id: Annotated[
            str, Field(description="File's item id, from graph_list_drive_items.")
        ],
    ) -> str:
        """Read a small text file's actual content (.txt/.md/.csv/.json
        and similar) as a UTF-8 string — the real text, not a link.

        Use for "what does onboarding-checklist.md say" or "summarize
        this .md doc". Rejects (clear error, no truncation) anything over
        200,000 bytes or failing UTF-8 decoding — i.e. a binary Office
        document (.docx/.xlsx/.pdf); for those use graph_get_file's
        downloadUrl instead.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            meta = await client.get(
                f"/drives/{drive_id}/items/{item_id}", params={"$select": "name,size,file"}
            )
        except GraphError as e:
            return e.to_envelope()
        size = meta.get("size")
        if isinstance(size, int) and size > _MAX_READ_BYTES:
            return error_envelope(
                "invalid_argument",
                f"File is {size:,} bytes, over this tool's {_MAX_READ_BYTES:,}-byte text-read "
                "limit. Use graph_get_file to get a downloadUrl and fetch it directly instead.",
                False,
            )
        try:
            raw = await client.get_content(f"/drives/{drive_id}/items/{item_id}/content")
        except GraphError as e:
            return e.to_envelope()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return error_envelope(
                "invalid_argument",
                "File content is not valid UTF-8 text — this is a binary file "
                "(e.g. .docx/.xlsx/.pdf), which this tool cannot read. Use "
                "graph_get_file to get a downloadUrl and fetch it directly instead.",
                False,
            )
        return dump_json_capped({"name": meta.get("name"), "size": size, "content": text})

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
    async def graph_read_site_file_text(
        site_query: Annotated[
            str,
            Field(
                description='Site name or keyword, e.g. "permission matrix". Tries an '
                "exact, case-insensitive displayName match first; falls back to the "
                "single keyword-search hit if there is exactly one. Still required "
                "even when site_id is given (site_id then wins for resolution)."
            ),
        ],
        file_name: Annotated[
            str, Field(description='File name, e.g. "Permission Matrix.csv" (case-insensitive).')
        ],
        folder_path: Annotated[
            str | None,
            Field(
                description='Folder path inside the document library, e.g. "HR/Matrix". '
                "Omit to look in the library root."
            ),
        ] = None,
        site_id: Annotated[
            str | None,
            Field(description="Site id, if already known — skips the site search entirely."),
        ] = None,
    ) -> str:
        """Read a SharePoint text file in ONE call, by site + file name —
        no driveId or item id needed.

        Resolves site -> drive -> file -> content server-side, so no id is
        copied through the model. Same limits as graph_read_file_text
        (<=200,000 bytes, UTF-8 only). Errors: site_not_found,
        site_ambiguous, file_not_found, file_empty, file_too_large,
        not_text — each with extra structured context.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN

        site = await _resolve_site(client, site_query, site_id)
        if isinstance(site, str):
            return site

        drive = await _resolve_drive(client, site["id"])
        if isinstance(drive, str):
            return drive

        file_item = await _resolve_file(client, drive["id"], file_name, folder_path)
        if isinstance(file_item, str):
            return file_item

        size = file_item.get("size")
        if isinstance(size, int) and size == 0:
            return error_envelope(
                "file_empty",
                f'"{file_item.get("name")}" exists but is empty (0 bytes).',
                False,
                extra={
                    "file": {
                        "name": file_item.get("name"),
                        "lastModifiedDateTime": file_item.get("lastModifiedDateTime"),
                    }
                },
            )
        if isinstance(size, int) and size > _MAX_READ_BYTES:
            return error_envelope(
                "file_too_large",
                f"File is {size:,} bytes, over this tool's {_MAX_READ_BYTES:,}-byte "
                "text-read limit.",
                False,
                extra={"size": size, "limit": _MAX_READ_BYTES},
            )

        item_id = file_item.get("id")
        try:
            raw = await client.get_content(f"/drives/{drive['id']}/items/{item_id}/content")
        except GraphError as e:
            return e.to_envelope()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            file_type = (file_item.get("file") or {}).get("mimeType")
            return error_envelope(
                "not_text",
                "File content is not valid UTF-8 text — this is a binary file "
                "(e.g. .docx/.xlsx/.pdf), which this tool cannot read.",
                False,
                extra={"file_type": file_type},
            )
        return dump_json_capped(
            {
                "site": site,
                "drive": drive,
                "file": {
                    "id": item_id,
                    "name": file_item.get("name"),
                    "size": size,
                    "lastModifiedDateTime": file_item.get("lastModifiedDateTime"),
                },
                "content": text,
            }
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=True))
    async def graph_write_file_text(
        drive_id: Annotated[str, Field(description="Document library's drive id.")],
        item_id: Annotated[
            str,
            Field(
                description="Existing file's item id to overwrite, from "
                "graph_list_drive_items. This tool only overwrites an existing "
                "file — it cannot create a new one."
            ),
        ],
        content: Annotated[
            str,
            Field(
                description=f"Complete replacement content (UTF-8 text), up to "
                f"{_MAX_WRITE_BYTES:,} bytes encoded. Whole-file overwrite, not a "
                "patch/append — pass the full final text every time."
            ),
        ],
    ) -> str:
        """Overwrite an EXISTING text file's entire content (.txt/.md/
        .csv/.json). For a path that doesn't exist yet, use
        graph_create_file_text — this tool refuses to guess.

        Whole-document write, not a patch/append: the given content
        REPLACES everything the file had — fetch the current text with
        graph_read_file_text first unless deliberately replacing it
        wholesale. Refuses non-text targets (binary Office documents).
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            return error_envelope(
                "invalid_argument",
                f"Content is {len(encoded):,} bytes, over this tool's "
                f"{_MAX_WRITE_BYTES:,}-byte limit.",
                False,
            )
        try:
            meta = await client.get(
                f"/drives/{drive_id}/items/{item_id}", params={"$select": "name,file"}
            )
        except GraphError as e:
            return e.to_envelope()
        mime_type = ((meta.get("file") or {}).get("mimeType") or "").lower()
        if mime_type and not (mime_type.startswith("text/") or "json" in mime_type):
            return error_envelope(
                "invalid_argument",
                f"Target file's current type ({mime_type}) is not text-based — "
                "this tool only overwrites plain-text files, refusing to avoid "
                "corrupting a binary document.",
                False,
            )
        try:
            result = await client.put_content(
                f"/drives/{drive_id}/items/{item_id}/content",
                encoded,
                content_type="text/plain; charset=utf-8",
            )
            return dump_json_capped(result)
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, idempotentHint=False))
    async def graph_create_file_text(
        drive_id: Annotated[str, Field(description="Document library's drive id.")],
        path: Annotated[
            str,
            Field(
                description='Full path from the library root, e.g. "notes.md" or '
                '"Scratch/test-file.txt" — parent folders in the path must already '
                "exist. Extension must be .txt/.md/.csv/.json."
            ),
        ],
        content: Annotated[
            str,
            Field(
                description=f"Initial content (UTF-8 text), up to {_MAX_WRITE_BYTES:,} "
                "bytes encoded."
            ),
        ],
    ) -> str:
        """Create a NEW text file (.txt/.md/.csv/.json) at a path that
        doesn't exist yet.

        Use for "create a new scratch file with this content", "add a
        notes.md to that folder". Fails (clear error) if something already
        exists at that exact path — this tool never silently overwrites;
        use graph_write_file_text for an existing file instead. To remove
        a file created this way, use graph_delete_file.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        if not any(path.lower().endswith(ext) for ext in (".txt", ".md", ".csv", ".json")):
            return error_envelope(
                "invalid_argument",
                "path must end in .txt, .md, .csv, or .json — this tool only creates "
                "plain-text files.",
                False,
            )
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_WRITE_BYTES:
            return error_envelope(
                "invalid_argument",
                f"Content is {len(encoded):,} bytes, over this tool's "
                f"{_MAX_WRITE_BYTES:,}-byte limit.",
                False,
            )
        try:
            await client.get(f"/drives/{drive_id}/root:/{path}:")
        except GraphError as e:
            if e.status_code != 404:
                return e.to_envelope()
            # 404 is the expected/good case here — nothing at this path yet.
        else:
            return error_envelope(
                "invalid_argument",
                f"A file already exists at \"{path}\" — use graph_write_file_text to "
                "overwrite it, or pick a different path.",
                False,
            )
        try:
            result = await client.put_content(
                f"/drives/{drive_id}/root:/{path}:/content",
                encoded,
                content_type="text/plain; charset=utf-8",
            )
            return dump_json_capped(result)
        except GraphError as e:
            return e.to_envelope()

    @mcp.tool(annotations=ToolAnnotations(destructiveHint=True, idempotentHint=True))
    async def graph_delete_file(
        drive_id: Annotated[str, Field(description="Document library's drive id.")],
        item_id: Annotated[
            str,
            Field(
                description="File's item id to delete permanently, from "
                "graph_list_drive_items/graph_get_file/graph_create_file_text's result."
            ),
        ],
    ) -> str:
        """Permanently delete a file (moves it to the site's recycle bin,
        same as deleting it in the SharePoint UI — recoverable there for a
        limited time, but gone from this MCP's view immediately).

        Confirm the exact file with the user before calling on anything
        that wasn't clearly created for throwaway/test purposes.
        Idempotent: deleting an already-deleted item id returns success,
        not an error.
        """
        client = client_factory()
        if client is None:
            return NO_TOKEN
        try:
            await client.delete(f"/drives/{drive_id}/items/{item_id}")
        except GraphError as e:
            if e.status_code != 404:
                return e.to_envelope()
        return dump_json_capped({"driveId": drive_id, "itemId": item_id, "deleted": True})
