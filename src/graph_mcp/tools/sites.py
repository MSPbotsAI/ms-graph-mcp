"""SharePoint site/document tools (Microsoft Graph Sites & Drives APIs).

Scope note: these tools cover TEXT-based files only (.txt/.md/.csv/.json and
similar) — reading decodes content as UTF-8, writing rejects anything that
doesn't already look like a text file. Binary Office documents (.docx/.xlsx/
.pdf) are deliberately out of scope: reading one back as inline tool output
would mean stuffing a base64-encoded blob through the calling agent's own
context window, which this fleet's tools cap around 20,000 characters —
workable for a short text file, not for a multi-hundred-KB document.
graph_get_file's returned downloadUrl is the escape hatch for binary
content: a pre-authenticated, time-limited direct link a caller can fetch
independently of this MCP's own response-size limits.
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

        Use for "send me a link to this file", "how big is that document",
        "what type of file is this" — or as a size/type check before
        graph_read_file_text. The returned downloadUrl works for ANY file
        (including .docx/.xlsx/.pdf), fetched outside this MCP. For "what
        does this document say" (actual text content), use
        graph_read_file_text instead — it works only for small text files.
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

        Use for "what does onboarding-checklist.md say", "read that SOP
        text file", "summarize this .md doc". Rejects (clear error, no
        truncation/garbling) anything over 200,000 bytes or failing UTF-8
        decoding — i.e. a binary Office document (.docx/.xlsx/.pdf). For
        those, use graph_get_file's downloadUrl — this tool can't read them.
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
        .csv/.json). No "create a new file" tool exists in this server.

        Use for "update review-notes.md with these findings", "save my
        edits to that file". Whole-document write, not a patch/append: the
        given content REPLACES everything the file had — fetch the current
        text with graph_read_file_text first unless deliberately replacing
        it wholesale. Refuses non-text targets (binary Office documents).
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
