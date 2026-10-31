# Authenticated collaboration acceptance

Status: **core read path executed against the real provider on 2026-10-31**. User Hub
PKCE, refresh, mail list/search, calendar list/window query, contacts list/search and
file-root discovery/list passed. RSVP, revoke-after-read, file-content read and real
cross-user/cross-tenant rejection remain follow-ups. See
`OAUTH-INTEGRATION-HANDOFF.md` for sanitized evidence and the public Gateway issue.
Repeat this checklist for later releases through the authenticated Re8ch tenant
self-service plugin; never substitute the Admin organization or local provider credentials.

## Prerequisites and evidence

Record date, tenant reference, active membership result, server version, deployed
source revision/image digest, available tool names and grant class. Do not record
access/device tokens, user codes, mail bodies, search terms or file contents in Git.
The tenant must grant `collaboration.stalwart.v1`; the runtime must enable the tools.
The user needs an existing Stalwart account and must complete provider device consent.
This implementation does not create mailboxes or register a second identity.

The runtime owner must first verify native service scopes and the configured OAuth
client against the actual server. Stalwart 0.16.x is not an accepted scoped OAuth
baseline; a 1.0 migration needs its own reviewed migration and runtime evidence.
Do not remove scope enforcement to accommodate an older provider.

Use existing user data wherever possible. If needed, the user can prepare these
through their normal authenticated application (the tools below cannot create them):

- A saved, unsent draft containing a unique test marker and no recipients.
- A private calendar event within the next seven days, with no attendees.
- A small UTF-8 `text/plain` file containing the marker in their own DAV collection.
  Also use a filename containing `#` or `?` to exercise encoded href round-tripping.

These fixtures send no messages. A no-attendee event verifies calendar reads, not
RSVP. Shared file collections and binary download are not exposed by this first
self-service release; do not report them as passed.

## Calls and required results

Discover the current authenticated catalog and use its exact schema. If any tool is
missing, record that as a blocked publishing/grant check and stop before consent.
The JSON below is the current source contract; replace uppercase placeholders only
with values returned by the authenticated tools. Never construct account IDs or DAV
paths from an email address.

1. Call `collaboration_connection`:
   `{"action":"plan","scopes":["mail.read","calendar.read","files.read"]}`.
   Inspect scopes and account-binding implications. Then call it with
   `{"action":"start","scopes":["mail.read","calendar.read","files.read"],"planHash":"RETURNED_PLAN_HASH","idempotencyKey":"UNIQUE_RUN_KEY"}`.
2. The user opens the returned `verificationUri`, authenticates to their account,
   reviews permissions and enters the returned user code. Respect the returned poll
   interval; call `{"action":"complete","connectionRef":"RETURNED_CONNECTION_REF"}`
   until consent completes or expires. Record `Ready` and expiry only. An empty,
   mismatched or management scope response must fail closed.
3. `mail_query` with `{"connectionRef":"REF","action":"list"}` must return the
   user's mailboxes. Search the marker with
   `{"connectionRef":"REF","action":"search","text":"MARKER","limit":5}`.
   Pass an actual returned ID to `mail_read`:
   `{"connectionRef":"REF","emailId":"RETURNED_EMAIL_ID"}`.
   Verify the expected draft/body locally and record only match/truncation results.
4. `calendar_query` with `{"connectionRef":"REF","action":"list"}` must return
   calendars. Query a window of at most 31 days:
   `{"connectionRef":"REF","action":"search","after":"RFC3339_START","before":"RFC3339_END","timeZone":"Asia/Shanghai","limit":10}`.
   Read an actual returned event with
   `{"connectionRef":"REF","action":"get","eventId":"RETURNED_EVENT_ID","timeZone":"Asia/Shanghai"}`.
   Verify its local time, title and recurrence behavior against the source application.
5. `files_list` with `{"connectionRef":"REF","href":""}` must discover the
   authenticated principal's own root. Pass its returned href to `files_list`, then
   pass the fixture's returned href unchanged to `files_read`. Verify text, MIME,
   size and ETag locally. The encoded-name fixture must round-trip without losing
   its `%23` or `%3F` encoding. Listing is one level, capped at 50 entries; direct
   text is capped at 256 KiB. Respect `partial` and `truncated` flags.
6. Revoke this test connection with
   `{"action":"revoke","connectionRef":"REF"}`. Verify status is `Revoked` and
   repeating a previously successful data read is denied. Reconnect separately if
   the user wants continued use; revocation does not delete their provider data.

## RSVP and isolation follow-up

RSVP remains **not tested** in the no-external-message run. A separately authorized
controlled invitation fixture is required to verify an applied response and its
read-back. `calendar.respond` must be explicitly consented along with calendar read.
A preview (`apply:false`) is non-mutating; applying can notify the organizer.
Never apply to an arbitrary existing invitation merely to complete this checklist.

A second active user in the same tenant and a second authorized tenant are required
for real cross-subject/cross-tenant rejection tests. Use those users' authenticated
sessions, not copied tokens or impersonation. Confirm foreign connection references
are denied. Local isolation tests are useful but are not this runtime evidence.

Record each step as passed, failed, or blocked, with operation/reference IDs and
sanitized errors. Empty lists demonstrate protocol access but do not verify content
reads. A merged PR, healthy pod, mock server or successful build cannot establish
that these user workflows work.
