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
to the aggregator configuration, and are canonicalized. Exact entries still require files, not directories, state roots or globs.
Optional `search_paths` discovers config files as described below. No service is started.

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
compatible successor. Older data is migrated by restarting the updated owning instance using
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
board. Changing the aggregator configuration requires restart; configured search paths
are rescanned during refresh. Each filesystem access re-resolves the child config. Project-aware filters/grouping use the same records in all modes.
Filesystem records expand in the aggregator with their data location and ID;
opening them never launches a service or invents an HTTP link. HTTP sources keep
their existing record links. Generated routing briefings include current source
context and transport and retain the same preflight, claim and recovery workflow.

Format 1.4 adds identical capture-system attribution and preservation to HTTP and
filesystem idea writes. The processing launcher is a local service operation;
GET `/api/processing` returns status and token-protected PUT
`/api/processing/start` and `/api/processing/presence` launch/report browser
presence. Filesystem aggregation does not start a source service or agent.
Codex uses the existing HTTP/routing API and source transport configuration for
all saves; there is no alternate processing writer. Shared conformance tests
cover source attribution, retry identity and preservation across all transports.

Workflow execution remains owner-local. Both adapters preserve and protect claims
for direct merges without a separate preview exactly like tested merges; an
absent `tested_commit` must remain absent through edits, receipt retries and
transport switching. Aggregate reads do not infer successful testing from merge
or completion phases.

Format 1.7 categories are optional record fields validated by the common
BoardStore validator for both adapters. Snapshots retain selections and aggregate
views/briefings use the same definitions as owner views. Omission preserves a
saved selection, and explicit empty text clears it. The shared conformance
matrix covers all seven values, clearing, invalid values, stale revisions and
receipt retries after switching transports. Filesystem access never migrates a
source during discovery; upgrade sources explicitly before use.


Format 1.8 adds protected queued workflow claims, PR/integration evidence and
optional owner-local depends_on IDs. Both transports validate dependencies and
preserve the exact backend-owned workflow through edits and uncertain retries.
Only the owning HTTP service launches jobs; filesystem clients cannot forge or
advance claims. Filesystem discovery never migrates a 1.7 source implicitly.

Format 1.9 adds protected migration review and recovery action evidence. Both
transports retain the same workflow record through edits and idempotent retries;
only the owning service may approve/advance stages. Migration itself takes the
exclusive board lease, excluding filesystem writers too. An HTTP service also
reports its startup-captured `context.runtime_commit` for deployment health. This
is process evidence, not persisted record semantics; offline/filesystem snapshots
do not claim a running service revision.

Format 1.10 completion summaries use shared validation and BoardStore reopening normalization in both adapters. Legacy absence remains valid; new closures require a summary. Revision checks, receipt retry after transport switching, recovery and local history retain the same contract.


## Config search paths (format 1.11)

Optional `search_paths` is a list of config JSON path patterns, relative to the
aggregator config directory. Each matched config must declare `data`, an explicit
stable `project_id`, and `aggregation_source: {"app_root":"relative/app",
"url":"http://127.0.0.1:8766"}`. Metadata paths resolve relative to the canonical
matched config, using the same resolver and Git ownership checks as its service.
`app_root` must contain PROCESS.md. The URL is an explicit loopback origin with a
port, required for HTTP and optional for filesystem-only mode; if present it is
always validated. It must describe the actual service, including any launch
port override. A `port` or matched data file alone is insufficient metadata.

`*` matches zero or more characters and `?` exactly one within a path component.
`**`, bracket patterns and recursive traversal are unsupported. Hidden names only
match components beginning with a dot. Absolute paths and `..` are supported.
Traversal follows only the requested finite components, including symlinks to
outside the starting directory; there is no implicit workspace/root sandbox.
Config/data/app paths are canonicalized and aliases deduplicated. Matching an
exact HTTP source may fill absent config/app metadata; common metadata must agree.
Distinct data paths cannot share an ID, and self/nested aggregators are blocked.

Bounds: 20 patterns, 64 components per absolute pattern, 10,000 directory entries
examined and 200 matches per scan (including aliases), 1 MB per config, and 20
unique current sources including exact entries. A scan/traversal/overall-source
limit failure blocks membership rather than applying a truncated list. Other
invalid configs are reported individually. Up to 200 source identities are
retained per service session; additional identities are reported and blocked.
Saved inbox destinations remain visible even if that retention bound is reached.

Startup scans existing configs without initializing or migrating them. Aggregate
view polling schedules one background rescan at most every four seconds; the
cached view never waits for traversal or transport I/O. Newly matching configs
join without restart. Removed/invalid sources retain their last successful rows
and project filters with `removed` status, and cannot receive new operations.
`discovery` in GET /api/aggregate reports unmatched patterns and invalid matches;
source status reports transport unavailability separately. Restoring the exact
source permits normal refresh/retry. Changing a retained ID/data/URL/app mapping
blocks until reviewed; it never silently replaces a destination. The row cache
is memory-only; saved selections and routing claims survive restart in the inbox.

Membership changes serialize with routing and priority writes. A route already
in progress finishes against its frozen source; removal blocks subsequent
attempts while preserving its exact request/preflight. Late source-check results
cannot resurrect removed rows. Restore the original config to retry a pending
claim; receipt identity and transport-switch recovery remain unchanged. Discovery
never grants write authorization, creates boards, registers repositories, starts
services, relocates records or publishes changes. Both adapters retain their
existing preflight/version/history checks; upgrade child storage explicitly.

Format 1.12 adds todo effort using BoardStore for both adapters: missing creation
values default to medium, omitted edit fields preserve saved values, and explicit
unsupported values fail. Routed creation uses the same defaults and original
provenance/claim preservation. The conformance matrix covers effort edits, conflict
recovery, reload and receipt retry after transport switching. Aggregate details and
fresh owner-qualified AI/Human briefings display the owner's effort. The expanded
owner editor saves effort through /api/changes; aggregate effort remains read-only.

Integration recovery (storage 1.13) remains owner-local. Both adapters expose the
same protected claim phases, queue decisions, ordering and diagnostics; ordinary
mutations cannot forge them. Transport switching retains action identity and
workflow evidence through BoardStore. Older writers are read-only. Queue controls
use /api/workflow/action, never a filesystem shortcut around owner authorization.
