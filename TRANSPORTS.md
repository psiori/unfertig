# Aggregation transport contract

## Source todo controls

Normal and aggregate todos share their collapsed-row renderer, priority selector,
and AI/Human briefing builders. Aggregate identities include the source project;
expanded source details are read-only except for the shared priority control.
Briefings require a fresh compatible source snapshot, complete local history,
linked originals and verified child context. Cached/offline data is never copied
as a current handoff. Both local source ideas and foreign source_refs are included.

Token-protected PUT `/api/source-record` accepts `project_id` and `todo_id` and
returns the saved todo, original ideas, record revision, child context, preflight,
compatibility and history. PUT `/api/source-priority` additionally accepts the
exact saved `original`, `revision`, desired `priority`, `actor`, `request_id` and
that `preflight`. It changes only priority through the standard child mutation.
Both operations use the configured HTTP/filesystem adapters and their guards.
HTTP rejections retain their status; uncertain outcomes retain the exact request.
No stored-format change is introduced (format 1.3, protocol 2.0).

Priority drafts remain in sessionStorage for this board/tab across reloads.
Retry after an uncertain response uses the original mutation and receipt ID;
known conflicts require reviewing the latest record before making a new request.
Pending Git history remains visible and must be completed at its owner. A dirty
expanded editor for the same record blocks quick priority until saved or reset;
other expanded drafts survive the save and reordering. Native keyboard selection
and briefing buttons do not expand rows or change status.

HTTP and filesystem are adapters to the same authoritative BoardStore. All
relevant schema, API, storage, migration, routing and view changes must update
both in one change. `test_transports.py` runs common conformance scenarios for
HTTP-only, filesystem-only and dual mode through ordinary unittest discovery,
including the managed runtime's existing validation command. Contributors must
extend that matrix with relevant recovery/switching cases and run the complete
Python and JavaScript suites. Do not introduce a second writer implementation.

## Configuration and source identity

Global `transports` contains exactly two booleans, `http` and `filesystem`; at
least one must be true. There are no per-source overrides. Omission preserves
HTTP-only behavior; the 1.2 → 1.3 configuration migration records that default.
Explicit false values are never overwritten. Sources still require `data` and
`project_id`. HTTP requires an explicit loopback `url`. Filesystem requires an
explicit child `config` JSON file and `app_root` directory containing PROCESS.md;
URL is optional when HTTP is disabled. These paths may be absolute or relative
to the aggregator configuration, and are canonicalized. Directories, state roots,
globs and URL-derived config discovery are not accepted in place of these files.
T0025 remains separate. No service is discovered or started.

Child configuration resolves the data, project name, project ID and Git owner
using the same resolver as its service. Data and identity must match the source
entry. Child config/data must belong to the same Git repository; embedded owner
checks apply. Canonical duplicate data paths collapse only when all normalized
metadata agrees; distinct boards sharing an ID, self sources, and nested
aggregators fail. A transport switch never changes board-qualified record IDs.

Source paths select records for inspection, not write authorization. Before
routing, the caller must read the destination PROCESS.md and repository rules,
verify authorization, and supply the exact data/repository/process/project ID
and PROCESS.md SHA-256 in preflight. Both transports validate those fields. The
filesystem adapter repeats validation while holding the operation lock before
delivery. A changed preflight blocks a claimed request for manual review.

## Coordination and recovery

On POSIX, upgraded services and filesystem clients acquire a shared lifetime
lease on `.server.lock`. Older servers and stopped migration/configuration/
offline commands use its exclusive lease, so they cannot overlap. A new service
also exclusively owns `.service.lock`, preserving one service per board. Every
read snapshot and record transaction holds an exclusive `.operation.lock` plus
a reentrant thread lock. ID allocation, revision checks, journal recovery,
receipts and local Git commit remain inside that operation lock. Filesystem
clients can therefore operate with a running, stopped or unreachable upgraded
service. An operation waits up to three seconds for another process, then blocks
with a retryable busy message; it never steals locks or reads a partial batch.
Startup initialization uses the same lock. Reads re-read disk, not service memory.

Windows retains exclusive lifetime coordination; filesystem aggregation reports
a specific unsupported-coordination block there. HTTP remains available. POSIX
filesystems must provide coherent flock and atomic rename behavior; network
shares with unreliable locking and arbitrary direct editors are unsupported.
Never replace or unlink live lock files. Hosts must ignore `.server.lock`,
`.service.lock`, `.operation.lock`, `.transaction.json`, `.history-pending.json`,
`.receipts/` and `.board-*.tmp`; locks are runtime state, not versioned data.

Filesystem discovery never initializes a missing board or migrates formats.
Every active JSON file and journal payload must already be format 1.3 or a
compatible successor. Older data requires an explicit stopped migration using
the child installation and VERSIONING.md procedure. Compatible interrupted
journals roll forward under the operation lock before returning a snapshot;
reads do not retry pending Git history. Newer major formats block; newer minors
are read-only and pending recovery requires updated code. Unknown fields,
originals, request IDs and recovery metadata follow BoardStore's version guards.

HTTP and filesystem changes both call BoardStore.mutate, with the same stable
request ID, validation, ID allocator, revisions, immutable source_refs, receipts,
journals, and per-owner local Git history. Routing durably saves the exact
destination mutation in the inbox before sending it. Only confirmed destination
save and successful local history permit marking routed. Pending history, busy
locks, compatibility and preflight failures retain the original idea and claim.
Retry `/api/routes` for the same idea; do not mint a replacement creation. Inbox
and destination commits are independent: no cross-repository atomicity, rollback,
task relocation, Git conflict resolution or publication is implied.

## Selection, failures and display

With both enabled, every inspection and delivery probes HTTP first, with a
three-second socket timeout. Connection refusal/reset, timeout, unreachable
host/network, remote disconnect and incomplete response allow filesystem
fallback. All HTTP status errors (including 400, 403, 409 and 500), redirects,
invalid JSON, oversized responses, incompatible protocols, validation/preflight
rejection and other transport errors block without fallback. A lost write
response falls back using the identical stored request; durable receipts recover
an already accepted creation. If HTTP returns after a filesystem inspection but
the client lacks its session token, that write blocks on 403; retrying gets a
fresh token without changing the durable request.

Each next transport operation probes HTTP again: no sticky failover or durable
cache. Visible pages poll the cached view every four seconds. Source checks run
independently in the background with one check per source in flight. Healthy
sources become eligible after four seconds; failed sources after 20 seconds.
The view never waits for HTTP or filesystem I/O. Routing remains an explicit
operation and is not delayed by the view's retry schedule.
Successful refresh replaces the source view; a failure retains last successful
records marked stale, or unavailable with no cached records. Transport and
fallback reason are visible; inaccessible never means an empty authoritative
board. Configuration changes require restart; each filesystem access re-resolves
the child config. Project-aware filters/grouping use the same records in all modes.
Filesystem records expand in the aggregator with their data location and ID;
opening them never launches a service or invents an HTTP link. HTTP sources keep
their existing record links. Generated routing briefings include current source
context and transport and retain the same preflight, claim and recovery workflow.
