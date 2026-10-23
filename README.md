# graph-mcp

Microsoft Graph MCP server — exposes Azure Entra ID user, group, and license management, mailbox read and mail sending, SharePoint document read/write, and calendar scheduling as MCP tools over HTTP-SSE, covering the full user onboarding/offboarding lifecycle plus basic SharePoint document access and meeting booking.

<div align="center">

## MSPbots Connector

**The MCP Gateway built for MSPs: visible, controlled, and your data stays where it belongs.**

[**Get started at connect.mspbots.ai →**](https://connect.mspbots.ai)

</div>

---

MSPbots Connector gives your AI clients one MCP URL to reach your entire MSP stack (RMM, PSA, documentation, security, finance), and gives you control over who can call which tool and a record of everything that was called.

**Hosted service: [connect.mspbots.ai](https://connect.mspbots.ai)** · Free to use.

### Why Connector

![MSPbots Connector](https://stgoss.mspbots.ai/files/mcp/mspbots-mcp.png)

| | |
|---|---|
| **Visible** | Every tool call is logged: who, which vendor, when. Usage dashboard, CSV export, SIEM streaming. |
| **Controlled** | Role-based, per-tool allowlist. Deny by default. |
| **Protected** | Credentials encrypted per record. AI clients never see your vendor passwords or tokens. |
| **MSP-native** | Built for the MSP stack only: RMM, PSA, documentation, security, finance. |

### Quick start

1. **Sign up** at [connect.mspbots.ai](https://connect.mspbots.ai).
2. **Connect your vendors.** Pick from 150+ connectors and authorize each one on the vendor's own page, or bring your own API keys. No extra vendor accounts needed.
3. **Set access.** Create roles and decide which tools each role may call. Nothing is allowed until you allow it.
4. **Add your MCP URL to your AI client.** Copy the URL from your Connector dashboard into Claude, ChatGPT, Cursor, VS Code, Gemini, Codex, or any MCP-capable client.
5. **Ask your AI to do real work**, and watch every call appear in the audit log.

### Features

**Unified gateway**
- One MCP URL for all your AI clients
- 150+ connectors across the MSP stack
- Custom MCP servers supported, free
- Authorization happens on each vendor's own page; the AI never sees passwords or tokens
- Bring your own credentials: use the vendor API keys you already have
- Multiple accounts per app; connect once, reuse across AI clients
- Tool search: finds the right action for the task
- One sign-in; vendor credentials are injected behind the scenes and OAuth tokens refresh automatically

**Visibility**
- Audit log for every tool call (who, which vendor, when)
- Usage dashboard: call volume, top vendors, error rate, per-user activity
- Export audit logs to CSV, or stream to Loki, Graylog, or LogScale (SIEM)
- Optional recording of tool arguments and result summaries (off by default, enabled per organization)

**Control**
- Per-role, per-tool allowlist, deny by default
- owner / admin / member roles
- SCIM provisioning with Entra ID, Okta, JumpCloud, and Google Workspace

**Security**
- Credentials encrypted one record at a time (AES-256-GCM, per-record key)
- AI clients authenticate with OAuth 2.1 + PKCE
- Vendor responses are scanned for prompt-injection patterns and returned as structured data. This is one layer of defense, not a guarantee against every attack.
- Static egress IP for customer firewall and API allowlisting

### Pricing

**Free.**

Links: [connect.mspbots.ai](https://connect.mspbots.ai) · [mspbots.ai](https://mspbots.ai)

---

## Overview

This server implements the [Model Context Protocol](https://modelcontextprotocol.io/) (HTTP-SSE transport) and wraps the Microsoft Graph API. It exposes 41 tools (count last updated PRD-20422 — this line had already drifted to 39 before this ticket; not something this ticket introduced) spanning user management (create / read / update / disable, password reset, session revocation, manager assignment, MFA method listing), group membership and ownership (add / remove / list / search, owned-groups orphan check), license inventory and assignment, reading/searching a mailbox and sending mail, SharePoint sites/document libraries (find a site, browse a document library, create/read/write/delete small text files), Intune-managed devices (list / remove a user's enrolled devices), calendar (list events in a time window, check free/busy across mailboxes, create and cancel events), and offboarding composites (transfer a departing user's OneDrive content, start the tenant's directory synchronization). It is designed for **gateway mode**: the caller obtains an Azure access token via OAuth and passes it per-request through a header. The server itself holds no credentials.

## Quick Start

### Docker (recommended)

```bash
docker compose up --build
```

The server starts on `http://localhost:8080`.

### Local (uv)

```bash
uv sync
python -m graph_mcp
```

## Health Check

```bash
curl http://localhost:8080/health
# {"status": "ok", "service": "graph-mcp", "transport": "http"}
```

No token is required for the health endpoint.

## Authentication

Every request to `/mcp` must include a valid Azure access token:

```
X-Ms-Graph-Token: <access_token>
```

The token must be issued for the `https://graph.microsoft.com/.default` scope with the permissions listed per tool below. OAuth acquisition is handled by the caller — this server only forwards the token to Graph API.

Missing or invalid tokens return `401 Unauthorized`.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `MCP_HTTP_PORT` | `8080` | Listening port |
| `MCP_HTTP_HOST` | `0.0.0.0` | Listening host |
| `GRAPH_BASE_URL` | `https://graph.microsoft.com/v1.0` | Override for sovereign clouds (GCC High, DoD, China 21Vianet) |
| `ATTACHMENT_FETCH_ALLOWED_HOSTS` | `agent.mspbots.ai,agentint.mspbots.ai` | Hosts `graph_send_mail` may download `content_url` attachments from (https only, comma separated); empty disables `content_url` |

## MCP Endpoint

```
POST http://localhost:8080/mcp
```

Connect your MCP client with:
- Transport: `http` (Streamable HTTP / SSE)
- Header: `X-Ms-Graph-Token: <access_token>`

## Tools

| Tool | Description | Required Scope |
|---|---|---|
| `graph_check_user_exists` | Check whether an Entra ID user exists by UPN, email, or any proxy address (including aliases; a bare address is matched as `smtp:`); proxy-address lookups use Graph advanced queries (`ConsistencyLevel: eventual` + `$count`) (PRD-20223) | `User.Read.All` |
| `graph_list_users` | List / search Entra ID users by display name (prefix match; `exact=True` switches to exact match); can filter enabled/disabled accounts via `account_enabled`. Returns 50 items by default with a hard cap of 200; paginate via `@odata.nextLink`; the response carries `has_more`. It fills the gap where you only know a name fragment and not the exact UPN - `graph_check_user_exists` requires an exact UPN/email and cannot answer "is there someone named Alice?" | `User.Read.All` |
| `graph_create_user` | Create a new Entra ID user and set usage_location so licenses can be assigned later; can also write office location, phones (`business_phones`/`mobile_phone`), address (`street_address`/`city`/`state`/`postal_code`), `company_name`, `employee_hire_date` (PRD-20219), and `preferred_language` (BCP-47 language tag, e.g. `fr-CA`/`en-CA`, PRD-20480). The user is always forced to change the password at first sign-in, and this cannot be turned off per call; on success the response explicitly includes `passwordChangeRequiredAtNextSignIn: true` as confirmation (PRD-20412) | `User.ReadWrite.All` |
| `graph_get_user` | Read the full user profile, including manager, assigned licenses, and each license's `licenseAssignmentStates` (`assignedByGroup` null = directly assigned / non-null = inherited from group assignment) | `User.Read.All` |
| `graph_update_user` | Update user properties (only the fields passed are changed), including address, company name, hire date, and `preferred_language` (BCP-47 language tag, PRD-20480); `account_enabled=false` disables the account (offboarding scenario) | `User.ReadWrite.All` |
| `graph_reset_password` | Admin reset of a user's password; the user is forced to change it at next sign-in | `User.ReadWrite.All` + Password/User Administrator role |
| `graph_revoke_sessions` | Sign the user out of all sessions, forcing re-authentication | `User.ReadWrite.All` |
| `graph_assign_manager` | Set the user's manager | `User.ReadWrite.All` |
| `graph_list_auth_methods` | List the MFA authentication methods registered by the user | `UserAuthenticationMethod.Read.All` |
| `graph_create_tap` | Generate a Temporary Access Pass (temporary sign-in code) for the user; one-time use by default (`is_usable_once=true`), with optional `start_date_time`/`lifetime_minutes`; calling again invalidates the previous pass, so it is not idempotent (PRD-20480, E6: a one-time sign-in code that is only sent to the manager) | `UserAuthenticationMethod.ReadWrite.All` (until the platform has been granted this scope, calls return 403; the tenant must first complete re-consent, see the scope union notes below) |
| `graph_assign_groups` | Add the user to one or more groups; groups the user is already in are skipped idempotently | `GroupMember.ReadWrite.All` |
| `graph_add_group_owner` | Make the user an owner of a group/Team; skipped idempotently if already an owner. A Team is also an M365 Group underneath and uses the same endpoint (PRD-20480, E5: set as owner only after approval - the approval itself is not this tool's responsibility) | `Group.ReadWrite.All` (until the platform has been granted this scope, calls return 403; the tenant must first complete re-consent, see the scope union notes below) |
| `graph_remove_group_member` | Remove the user from one or more groups; groups the user is not in are skipped idempotently | `GroupMember.ReadWrite.All` |
| `graph_list_user_groups` | List the groups the user directly belongs to | `GroupMember.Read.All` (note: `GroupMember.ReadWrite.All` is not in this endpoint's accepted list; the Read variant must be requested separately) |
| `graph_list_groups` | Search / list Entra ID groups by display name | `Group.Read.All` |
| `graph_get_group` | Read a single group's details by id: whether mail is enabled, type, dynamic rule, and sync source (derived field `sync_source`: `on-premises` / `cloud`, with `onPremisesLastSyncDateTime`). Before assigning groups, use it to verify that the group ids of the template user still exist | `Group.Read.All` |
| `graph_list_group_members` | Page through a group's direct members (users / nested groups / devices / service principals, see `member_type`); default 50 per page, maximum 200; when `has_more` is true it carries a `next_page_token` for the next page, and only the `$skiptoken` is returned rather than the full URL (PRD-20223) | `GroupMember.Read.All` |
| `graph_list_owned_groups` | List the groups a user owns, with each group's total owner count - used in offboarding to decide whether "this user is the sole owner" (`owner_count==1` means the group becomes orphaned as soon as the user is removed). Implementation: first resolve user_id to the real GUID via `/users/{id}`, then page through all of `/groups` and call `/groups/{id}/owners` per group to check whether it includes the user - N+1, but this is currently the only path verified as reliable on a real tenant: the docs state `/users/{id}/ownedObjects` does not support app-only; `/groups?$filter=owners/any(...)` is rejected outright by Graph (`owners` is not a filterable property on Group); `/groups/delta` selecting `owners` looks like the documented correct approach, but real testing found its paging is buggy (the same batch of ~200 groups is returned repeatedly and `@odata.nextLink` never advances, regardless of whether owners is selected), so it was abandoned | `Group.Read.All` (also needs `User.Read.All` to resolve user_id, already covered by the existing `User.ReadWrite.All`) |
| `graph_check_license_stock` | Query license stock and remaining quantity for the SKUs the tenant subscribes to | `Organization.Read.All` |
| `graph_assign_license` | Assign and/or remove licenses for the given SKUs for a user (Graph's assignLicense endpoint supports both adding and removing in one call, so both are merged into this one tool) | `User.ReadWrite.All` |
| `graph_reprocess_license` | Re-run group license assignment for a user (use when `licenseAssignmentStates` reports an error / is stuck, e.g. after usage_location was added or stock was freed up); does not change which licenses the user should have | `User.ReadWrite.All` (least-privilege `LicenseAssignment.ReadWrite.All`) |
| `graph_list_domains` | List the tenant's domains (only verified ones by default, with `isDefault` marking the default domain); used to choose the UPN / email domain during onboarding | `Domain.Read.All` or `Directory.Read.All` (the delegated version has already requested `Directory.Read.All`; app-only needs the customer to grant it separately) |
| `graph_send_mail` | Send mail as the specified user, supporting To / CC / BCC, an HTML body, and up to 5 attachments. Prefer `content_url` for attachments: give an https link (e.g. a public file link from the Agent platform) and the server downloads the raw bytes itself; `filename` is optional (taken from the link); ≤2.5 MB per file; the host must be in `ATTACHMENT_FETCH_ALLOWED_HOSTS`; if the download fails, the mail is not sent (PRD-20707). Small files can also be inlined: for text pass the original text via `content_text`, for binary use `content_base64` (newlines / `data:` prefix / URL-safe / missing padding are all tolerated); ≤500 KB per file and ≤700 KB total inline; total attachments per message ≤2.5 MB | `Mail.Send`; with `sender_id` (sending on behalf of another user / a shared mailbox) `Mail.Send.Shared` is also required |
| `graph_list_messages` | List / search messages in a mailbox, returning only metadata + `bodyPreview` without the body; defaults to the inbox in reverse order of received time, filterable by sender / conversation / time window / unread / has-attachments, and `search` does full-text search. `folder=null` spans all folders, and combined with `conversation_id` you can see the whole thread (including your own replies). Only the first page is fetched; the response carries `has_more` | `Mail.Read` (to read someone else's mailbox: app-only permission, or, under delegation, the mailbox has been shared with the caller, see `Mail.Read.Shared`) |
| `graph_get_message` | Read the full text of a single message (including body). By default asks Exchange to compress the body to plain text (`Prefer: outlook.body-content-type`), which uses several times fewer tokens than HTML; bodies longer than 15,000 characters are truncated and marked `truncated`/`original_length`. `include_attachments=true` runs an extra attachment listing that returns only name / type / size and **never `contentBytes`** | `Mail.Read` (same as above) |
| `graph_search_sites` | Search SharePoint sites by name/keyword; the result automatically includes each site's default document library driveId (filled in for at most the first 5) | `Sites.Read.All` |
| `graph_list_drive_items` | List files/folders at the root of a document library or under a folder (non-recursive) | `Sites.Read.All` |
| `graph_get_file` | Get file metadata (name / size / MIME type) + a temporary pre-authorized direct download link | `Sites.Read.All` |
| `graph_read_file_text` | Read the actual content of small plain-text files (.txt/.md/.csv/.json, etc.); files over 200,000 bytes or not decodable as UTF-8 (i.e. binary Office documents) are rejected with a hint to use downloadUrl instead | `Sites.Read.All` |
| `graph_read_site_file_text` | Read a SharePoint text file by site name + file name in one call; the server performs the site → default document library → file → content resolution, so the caller never needs to obtain or pass a driveId or item id - a single-call replacement for the three-call chain `graph_search_sites` → `graph_list_drive_items` → `graph_read_file_text` (PRD-20413, fixing the occasional "malformed drive id" failures caused by the model mis-transcribing the driveId in intermediate steps). Same limits as `graph_read_file_text`; on failure it returns structured error codes `site_not_found`/`site_ambiguous`/`file_not_found`/`file_empty`/`file_too_large`/`not_text` | `Sites.Read.All` |
| `graph_write_file_text` | Overwrite the entire content of an existing plain-text file (not a patch; the full content must be passed); writing is rejected if the target file's current MIME type does not look like text | `Sites.ReadWrite.All` (sufficient, no need to additionally add `Files.ReadWrite.All` - see the note below) |
| `graph_create_file_text` | Create a new plain-text file at the given path; **if a file already exists at that path it errors out and refuses**, never silently overwriting - to overwrite an existing file use `graph_write_file_text` | `Sites.ReadWrite.All` (sufficient, no need to additionally add `Files.ReadWrite.All` - see the note below) |
| `graph_delete_file` | Permanently delete a file (it goes to the site recycle bin, equivalent to deleting in the SharePoint web UI); idempotent, deleting an item id that no longer exists also returns success | `Sites.ReadWrite.All` (sufficient, no need to additionally add `Files.ReadWrite.All` - see the note below) |
| `graph_list_managed_devices` | List a user's Intune managed devices; if the tenant has no Intune license it does not error and returns an empty list | `DeviceManagementManagedDevices.Read.All` |
| `graph_remove_managed_device` | Permanently delete a device's Intune management record (not a selective corporate-data wipe / retire, but a direct record deletion); idempotent, deleting a device id that no longer exists also returns success | `DeviceManagementManagedDevices.ReadWrite.All` |
| `graph_transfer_onedrive` | For the offboarding flow (following Rewst's approach): in the offboarded user's **own** OneDrive root, create/reuse an archive folder (default `Offboarding_Transfer`), move the rest of the root content into it, then share it with the recipient (write, requires sign-in, sends an invitation email); nothing is copied and ownership is not transferred, and about 30 days after the license is removed OneDrive is deleted along with the share. Repeated calls only process what remains, returning `completed`/`partial`/`no_onedrive`/`no_content`; if the caller cannot open this OneDrive (Graph 403) it returns `access_denied` - under the delegated ms-graph the admin must be a site collection admin of that OneDrive, or use ms-graph-app instead (PRD-20422) | `Files.ReadWrite.All` (for app-only ms-graph-app, once the application permission is granted it applies to all users; under delegation it is still subject to the site-collection-admin restriction above) |
| `graph_start_cloud_sync` | For the offboarding flow: trigger the tenant's Azure AD Connect cloud sync job; only the cloud sync mechanism is supported - classic on-premises AD Connect has no callable Graph interface, in which case it returns `no_sync_job_configured` and points out that `Start-ADSyncSyncCycle` must be run manually on the Connect server, and it does not pretend to succeed or silently do nothing; cloud accounts (`onPremisesSyncEnabled=false`) return `not_applicable` (not an error) (PRD-20422, deferred from PRD-20393 item 8) | `Synchronization.ReadWrite.All` (new, see the note below) |
| `graph_list_calendar_events` | Query a user's calendar events within a time window (`calendarView`, recurring meetings are expanded into individual instances); only the first page is fetched, so if the window is too large shrink the window yourself rather than increasing limit | `Calendars.Read` (to read someone else's calendar: app-only permission, or, under delegation, the calendar has been shared with the caller, see `Calendars.Read.Shared`) |
| `graph_get_user_availability` | Query free/busy for up to 20 mailboxes at once (`getSchedule`), returning each person's availabilityView string (0 free, 1 tentative, 2 busy, 3 out of office, 4 working elsewhere) + working hours; use it first to find open slots when scheduling - cheaper and less invasive of privacy than reading each calendar one by one | `Calendars.Read` |
| `graph_create_calendar_event` | Create a calendar event, optionally with required/optional attendees, location, body, and reminder; `is_online_meeting=true` attaches a Teams meeting and returns the joinUrl; **invitations are sent immediately on call**, cannot be recalled, and can only be cancelled afterwards | `Calendars.ReadWrite` (to create on someone else's calendar: app-only permission, or, under delegation, `Calendars.ReadWrite.Shared`) |
| `graph_cancel_calendar_event` | Cancel/delete an event: if the caller is the organizer and there are attendees it uses `cancel` (sending a cancellation notice to all attendees), otherwise it falls back to a direct `delete`; the `method` in the return value says which path was actually taken; idempotent, cancelling an event id that no longer exists also returns success | `Calendars.ReadWrite` |

> **Permission note**: all SharePoint tools in this file only call Graph's `/sites/*` and `/drives/*` endpoints and never touch `/me/drive` or `/users/{id}/drive`. For these site document-library drive endpoints, `Sites.*` and `Files.*` are alternative permission groups (either one suffices), not cumulative requirements - so only `Sites.Read.All` (read-only tools) + `Sites.ReadWrite.All` (write/create/delete tools) are needed, and there is no need to additionally request `Files.ReadWrite.All`.

> **Scopes actually requested**: the table above lists, per tool, the **minimum** permission each endpoint accepts, so scopes can be trimmed as needed; what the platform side (`auth/oauth/vendors/msgraph.py` in MCP-Management-Service) actually requests from Entra is the union that covers all 41 tools:
>
> ```
> offline_access openid profile
> User.ReadWrite.All UserAuthenticationMethod.Read.All UserAuthenticationMethod.ReadWrite.All
> Group.Read.All Group.ReadWrite.All GroupMember.Read.All GroupMember.ReadWrite.All
> Organization.Read.All Mail.Read Mail.Send Mail.Send.Shared
> Sites.ReadWrite.All Files.Read.All Files.ReadWrite.All
> DeviceManagementManagedDevices.ReadWrite.All
> Calendars.ReadWrite Calendars.ReadWrite.Shared
> Synchronization.ReadWrite.All
> ```
>
> Of these, `User.ReadWrite.All` covers creating/updating users, password reset, license assignment, and session revocation (the minimum permissions of these endpoints are scattered across `User-PasswordProfile.ReadWrite.All`, `User.EnableDisableAccount.All`, `LicenseAssignment.ReadWrite.All`, and `User.RevokeSessions.All`; one covering permission is more honest than requesting five); `Sites.ReadWrite.All` includes `Sites.Read.All` so it is not listed again; `DeviceManagementManagedDevices.ReadWrite.All` likewise includes `.Read.All` and covers the two tools `graph_list_managed_devices` + `graph_remove_managed_device`; `Directory.Read.All` is **not requested** (no tool needs to read the whole directory).
>
> **`UserAuthenticationMethod.ReadWrite.All` (`graph_create_tap`) and `Group.ReadWrite.All` (`graph_add_group_owner`) are two scopes newly added by PRD-20480** - earlier versions explicitly stated "`Group.ReadWrite.All` is not requested (no tool creates/deletes groups or edits group properties)", and that sentence became outdated when these two new tools were added. These two scopes must first be added to the requested scope set on the platform side (`auth/oauth/vendors/msgraph.py` in `MCP-Management-Service`, outside the scope of this repository), and then every tenant that has already connected the Microsoft Graph connector must re-consent once; until a tenant completes re-consent, these two tools keep returning 403 - the same rollout path as `Mail.Read` (PRD-19103) and `Calendars.ReadWrite` (PRD-18631), a known and repeatable process rather than a new category of risk.
>
> `Files.Read.All` / `Files.ReadWrite.All` were requested together as an operational decision: as stated above, for the `/sites/*` and `/drives/*` endpoints used in this file, `Files.*` and `Sites.*` are **alternative** permission groups and the SharePoint tools themselves do not need it - it is added so that tenants where "the admin consented to only one of the groups" can still end up in a usable state. The cost is that `Files.*` also covers every user's OneDrive - `graph_transfer_onedrive` (PRD-20422) is the first tool in this repository that actually uses this part of the permission; before that it was only a defensive request and never actually used.
>
> `Synchronization.ReadWrite.All` is a permission request newly added for `graph_start_cloud_sync` (PRD-20422), covering discovery of `/servicePrincipals/{id}/synchronization/jobs` and the `/start` call; already-connected tenants must re-authorize first to obtain it, and until then calling this tool returns `unauthorized`, which is exactly isomorphic to the rollout path already documented for the two newly added Mail/Calendar permission groups (see below).

> `Mail.Read` covers the two mail-reading tools `graph_list_messages` / `graph_get_message`, and semantically is exactly isomorphic to the calendar group: delegated `Mail.Read` only authorizes the **signed-in user's own** mailbox; reading someone else's mailbox requires `Mail.Read.Shared` **and** that mailbox actually being shared/delegated to the signed-in user on the Exchange side - the scope alone is not enough, these are two independent gates. Under delegated mode there is no tier of "any mailbox in the tenant can be read"; that is the application permission `Mail.Read` of app-only (`ms-graph-app`), which covers **every** mailbox in the tenant in one go, and before granting it we recommend using Exchange application access policy to restrict the accessible mailboxes to a security group instead of opening it bare. In addition, `Mail.ReadWrite` is **not requested** (no tool edits, moves, or marks messages as read) and neither is `Mail.ReadBasic` (it cannot read the body, which would make `graph_get_message` pointless).

> `Calendars.ReadWrite` / `Calendars.ReadWrite.Shared` cover all 4 calendar tools: the former is read/write of one's own mailbox calendar (including `getSchedule` free/busy), and the latter is for "operating on someone else's calendar" - under delegated mode it can only work when the target calendar has been shared/delegated to the signed-in admin, which is the normal entry point in MSP scenarios. `Calendars.Read` / `Calendars.Read.Shared` are strict subsets of them and are not requested again. App-only (`ms-graph-app`) has no such restriction: the application permission `Calendars.ReadWrite` directly covers the calendar of **every** mailbox in the tenant, and customers should be clear about this before granting it.

## Typical Workflows

Onboarding:
```
0. graph_list_domains        → choose the mailbox / UPN domain (optional)
1. graph_check_user_exists   → duplicate check, confirm the account does not exist
2. graph_create_user         → create the account and set usage_location; address/phone/company/hire date can be written in one go
3. graph_assign_groups       → assign groups
4. graph_check_license_stock → check license stock
5. graph_assign_license      → assign licenses
6. graph_send_mail           → send a notification email (optional)
```

Offboarding:
```
1. graph_update_user(account_enabled=false) → immediately block new sign-ins
2. graph_revoke_sessions                    → revoke existing sessions (old tokens may still be briefly valid until they expire; both steps together make a complete offboarding)
3. graph_list_owned_groups                  → check whether the user is the sole owner of any groups, and reassign first before offboarding
4. graph_list_managed_devices               → check whether there are still Intune managed devices
5. graph_remove_managed_device              → delete the management record if so
6. graph_assign_license(remove_sku_ids=...) → reclaim licenses (entries in licenseAssignmentStates with a non-null assignedByGroup are cleared automatically by the next step's group removal)
7. graph_remove_group_member                → remove from each group
```

Scheduling:
```
1. graph_get_user_availability   → query free/busy of all attendees at once and find common open slots
2. graph_create_calendar_event   → create the meeting in an open slot and send invitations (can attach a Teams meeting)
3. graph_list_calendar_events    → verify afterwards / get the event id
4. graph_cancel_calendar_event   → cancel the meeting and notify all attendees
```

## Known Gaps

- `employee_hire_date`: Graph requires a full date-time, so the tool pads `YYYY-MM-DD` to that day's `T00:00:00Z`; per the Graph docs, app-only permission cannot write this field (the ms-graph-app connection fails, the delegated ms-graph works). The parameters and three tools newly added by PRD-20219 have not yet been verified on a real tenant.
- PRD-20223: proxy address lookup depends on Graph advanced queries, and `graph_list_group_members` paging relies on hand-assembling `$skiptoken`; both need to be verified on a real tenant.

- `graph_remove_group_member`, `graph_revoke_sessions`, the `account_enabled` parameter of `graph_update_user`, and the `remove_sku_ids` parameter of `graph_assign_license` are all newly added and have not yet been tested against a real Graph tenant - before going live, run the complete offboarding flow once with a sacrificial test account before trusting it.
- `graph_revoke_sessions` does not take effect instantly: access tokens issued before the call remain valid until they expire (usually ~1 hour), so in offboarding scenarios always call `graph_update_user(account_enabled=false)` as well, not just one of the two.
- **The SharePoint tools (`graph_search_sites`/`graph_list_drive_items`/`graph_get_file`/`graph_read_file_text`/`graph_write_file_text`/`graph_create_file_text`/`graph_delete_file`) only cover plain-text files** (.txt/.md/.csv/.json) and deliberately do not support binary Office documents (.docx/.xlsx/.pdf) - inlining the content of such files into the tool return value would make the caller (the LLM) carry a large base64-encoded blob in its own context window, which directly conflicts with the ~20,000-character return value limit shared across this whole fleet. The `downloadUrl` returned by `graph_get_file` is an escape hatch for binary files - the caller can bypass this MCP and download directly - but **how to connect the download/upload byte stream to the LLM conversation flow** (especially the "write" direction: how a file a user gives in the chat UI ultimately becomes bytes uploaded to SharePoint) is an unverified platform-side integration problem that these tools cannot solve themselves - this repository currently only guarantees the path where "text content can be passed normally via tool_call parameters", and does not guarantee that a file channel exists between the chat frontend and the MCP that bypasses the LLM context.
- **The read-only SharePoint tools (`graph_search_sites`/`graph_list_drive_items`/`graph_get_file`) passed real-world testing on INT with a real tenant + real agent conversation** (2026-09-01, `jexettechnologies537.sharepoint.com`): they really returned 4 sites, a real file list, and real PDF metadata + downloadUrl, and the agent correctly recognized that the PDF is not a text file and did not mistakenly call `graph_read_file_text`. The `Sites.Read.All` permission is confirmed to take effect in production.
- **`graph_read_file_text`/`graph_write_file_text`/`graph_create_file_text`/`graph_delete_file` have not yet been verified with real calls** - during testing, all the ready-made files that could be found in that tenant were pdf/docx/xlsx/JPG with no usable plain-text file; `graph_write_file_text` was also not overwrite-tested because it is real customer data. `graph_create_file_text`/`graph_delete_file` were added afterwards precisely to solve the problem of "no safe test file" (creation uses a separate path and succeeds only if it does not exist; deletion is idempotent), but after adding them the complete create → write → read → delete chain has not yet been run with a real token.
- **`graph_list_owned_groups`/`graph_list_managed_devices`/`graph_remove_managed_device` are newly added** (PRD-17403, 2026-09-01). The `DeviceManagementManagedDevices.*` permissions have been confirmed to take effect on a real INT tenant (testing with a non-existent user_id, the error changed from "unauthorized: missing scope" to a real Intune backend 404, proving the permission gate was passed), but **no real, existing user_id has been found yet**, so `graph_list_managed_devices` has never produced a genuinely positive result with data, and `graph_remove_managed_device` has not been tested at all.
- **The implementation of `graph_list_owned_groups` has been changed three times**: the first version, `/users/{id}/ownedObjects`, was confirmed by the official docs not to support app-only; the second version, `/groups?$filter=owners/any(...)`, was rejected outright by Graph after going live on a real customer tenant (Precicom sandbox, 2qmxyw.onmicrosoft.com) - `owners` is not a filterable property on `/groups` at all, this query syntax simply does not exist, so it was an implementation bug in this repository, not a permission problem; the third version, `/groups/delta?$select=...,owners`, was thought to be the documented correct approach, but local direct-to-Graph testing with a real token found that delta's own paging is buggy - whether or not owners is selected, the same batch of about 200 groups is returned repeatedly and `@odata.nextLink` cannot advance: 5 pages pulled 1000 entries but only 200 distinct ids, while the plain `/groups` list on the same tenant correctly pulls all 279 in one go and matches `$count`. On 2026-09-02 it was changed to the fourth version (the current one): abandon delta and return to paging through the full `/groups` list + calling `/groups/{id}/owners` per group to check - **verified end-to-end with a real delegated token against a real tenant (the internal mspbots.ai tenant)**: all 279 groups were checked with no errors, consistent with the result of a manual full check (the test user actually owns no groups, and `count:0` is the real result, not a false negative masked by a bug). The cost is that the N+1 request volume grows linearly with the tenant's total group count, and a full scan cannot be avoided (`$expand`/`$search`/delta have all been verified as infeasible - see the table above).
- **The mail-reading tools (`graph_list_messages`/`graph_get_message`) have not yet been run successfully on a real consented tenant** (PRD-19103, delivered 2026-09-23); the blocker is exactly the same as for the calendar batch: the platform-side `msgraph.py` has already added `Mail.Read` to the delegated scopes, but the API permissions of the MSPbots shared Entra app still have to be added manually, and **already-authorized tenants must re-authorize** to obtain the new permission (the header comment of `msgraph.py` states this: a tenant keeps running on its old grant until it re-authorizes). Until then, calling these two tools returns `unauthorized` - and to the agent this error looks the same as "the tool does not exist at all", so when troubleshooting first confirm that the tenant's authorization time is later than the scope change time.
- **The calendar tools (`graph_list_calendar_events`/`graph_get_user_availability`/`graph_create_calendar_event`/`graph_cancel_calendar_event`) have not yet been run successfully on a real consented tenant** (PRD-18631, delivered 2026-09-10): the platform-side `msgraph.py` has already added `Calendars.ReadWrite`/`Calendars.ReadWrite.Shared` to the delegated scopes, but the API permissions of the MSPbots shared Entra app still have to be added manually, and already-authorized tenants must re-authorize to obtain the new permission; on the app-only side, the customer must add the application permission `Calendars.ReadWrite` in their app registration and grant admin consent. Until then, calling these tools returns `unauthorized`.
- **The calendar tools deliberately do not support rescheduling (PATCH), querying attendee response status, editing recurrence rules, or booking room/equipment resources**: the scheduling loop does not need them, and adding them would only dilute the agent's tool-selection accuracy. To reschedule, currently the only way is to cancel and recreate.
- **The archive-and-share version of `graph_transfer_onedrive` (PRD-20422 rework, 2026-10-16) has not yet been run on a real tenant**: only unit tests exist. The previous version was observed on INT to get a Graph 403 when an admin under a delegated connection opens another user's OneDrive.
- **`graph_start_cloud_sync` has not yet been run successfully on a real consented tenant** (PRD-20422, deferred from PRD-20393 items 7-8): only unit tests (fake client) exist - `graph_start_cloud_sync` filters service principals server-side by `applicationTemplateId` (the Cloud Sync application template `1a4721b3-e57f-4451-ae87-ef078703ec94`, see Microsoft Learn's cloud sync Graph configuration docs) (since 2026-10-19; before that it filtered locally by `homepage`, which had to page through all pages on large tenants and was too slow); sync jobs are filtered by matching `templateId` and `schedule.state == Active`, a step that comes from the Rewst implementation evidence Mandy Du provided on that ticket; before going live, we recommend first running it on a test tenant confirmed to use Azure AD Connect cloud sync. `graph_start_cloud_sync` also has to wait for `Synchronization.ReadWrite.All` to complete the platform-side authorization flow (the same re-authorize path for already-authorized tenants as Mail/Calendar) before it takes effect on connected tenants; until then, calls return `unauthorized`. This ticket assumes that tenant's groups use cloud sync rather than classic on-premises AD Connect - if not, the whole `graph_start_cloud_sync` tool is infeasible, see Open Questions in `docs/tickets/PRD-20422.md`.

## Sovereign Cloud Support

Set `GRAPH_BASE_URL` to override the default endpoint:

| Cloud | Base URL |
|---|---|
| Public (default) | `https://graph.microsoft.com/v1.0` |
| US Government GCC High | `https://graph.microsoft.us/v1.0` |
| US Government DoD | `https://dod-graph.microsoft.us/v1.0` |
| China (21Vianet) | `https://microsoftgraph.chinacloudapi.cn/v1.0` |

