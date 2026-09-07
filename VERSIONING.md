# Persisted formats and compatibility contract

Read this contract when processing ideas and before changing persistence or the
API. Every format change must include a deterministic migration, useful minimal
defaults, compatibility handling, and regression tests in the same change.

`format_version` is a major.minor.build string. Current storage is `1.6.0`.
Major changes may break reading; minor changes remain readable but may introduce
semantics an older writer cannot preserve; builds must remain safe to read and
write. A newer major refuses startup before recovery or writes. A newer minor
makes the entire instance read-only, with an update notice; pending transactions
or history require updating before startup. A newer build is writable, with a
notice, preserving its version and unknown fields. Never downgrade a version.
Invalid or unregistered older versions fail without guessing a migration.

The independent API contract is `protocol_version: "2.0.0"`; the existing
`api_version: 2` and integer layout `schema_version` remain layout identifiers.
Clients must stop on an incompatible protocol major, and disable writes for a
newer minor. Mutation requests may declare protocol_version; omitted versions
remain accepted for existing API-2 clients. The server rejects incompatible
versions before processing a mutation.

## Inventory

Version active ideas headers, each todo file, instance configuration, transaction
journals, pending-history files, and idempotency receipts. Version newly generated
relocation manifests, snapshots, tombstones, and retirement markers too. Ideas
retain their exact original text and attribution inside their versioned header.
Unknown extension fields remain intact. Wrapper node/launch metadata belongs to
the host contract, not this app. Non-JSON locks and Git internals are not formats.

Historical backups, completed relocation manifests, and their original snapshots
are immutable evidence: do not rewrite them just to add a version. Old evidence
is recognized as legacy when used by a supported recovery procedure. Active
receipts migrate without changing request IDs, fingerprints, or assigned IDs.

## Migration and recovery

Unversioned JSON is the legacy `0.0.0` input. The explicit sequential registry in
versions.py introduces `1.0.0`, then migrates to `1.1.0` with missing optional todo
fields and a default config port. It never invents authors, IDs, dates, names,
descriptions, or source links, nor replaces explicit invalid values. Existing
integer schema-1 monoliths still migrate to per-todo storage with an exact backup.

Startup and the offline `--snapshot` command migrate under the board writer lock.
`--check` validates without migrating. Preflight inspects every active file and
journal payload before recovery, Git commits, or migration writes. Full record
validation precedes the atomic journal; interrupted writes resume from that
journal. Versioned history retries preserve pending commits on failure. Meaningful
migrations commit locally through the owning repository, never push automatically.
Configuration and board must share that owner for automatic migration commits.

For a managed installation whose normal updater requires unchanged storage:
stop the instance; preserve an exact local copy of config, data, receipts and the
installed commit; rehearse the candidate on a disposable copy; compare normalized
records and original ideas; check repeated startup changes no bytes; then install
the reviewed, published commit and explicitly run its offline migration against
the real config before restarting. Keep the backup. Do not weaken the normal
updater or run pre-contract software against migrated state. On failure, keep the
service stopped, inspect the journal/history, and use supported recovery; never
blindly restore files over subsequent edits or reset unrelated repository work.

Tests must cover every migration step, mixed versions, absent optional fields,
unknown fields, newer major/minor/build handling, malformed data, interrupted
recovery, retry identity, and idempotence. Register a successor for every future
supported format change instead of silently rewriting files with a new shape.

Format **1.2.0** adds optional record project_id, inbox selected_project/routing,
destination source_refs and initials-prefixed IDs. The registered 1.1.0 → 1.2.0
step preserves all original content and extensions without adding invented
project assignments to legacy records. Earlier 1.1 writers see the newer minor
and become read-only. The API envelope stays protocol 2.0.0; these are optional
record extensions plus new /api/aggregate and /api/routes endpoints. The source
preflight explicitly requires 1.2-capable writers for foreign provenance guards.
Routing requests live in the versioned ideas header; ordinary transaction and
receipt recovery applies to them. No new unversioned durable cache is introduced.

Format **1.3.0** registers the 1.2.0 → 1.3.0 transport step. It versions active
files without changing originals or receipts, and gives aggregation configs an
explicit HTTP-only transport default when absent. New transport settings preserve
unknown fields and explicit booleans. Older writers become read-only and their
exclusive lifetime locks also exclude upgraded cooperative clients. Filesystem
aggregation requires an already migrated source; discovery cannot perform this
step. Stop services/clients for explicit migration and preserve all recovery
files. See TRANSPORTS.md for the shared adapter and contributor contract.

Format **1.4.0** adds processing configuration with manual-only migration defaults
and immutable, server-assigned `captured_system` on newly created ideas. The
1.3 → 1.4 step preserves existing records exactly except their format metadata;
it does not invent origins for old ideas. Earlier writers become read-only.
Both HTTP and filesystem creations use BoardStore's same system attribution and
immutability checks. Processing status/presence are ephemeral service controls,
not a second record writer or persistent job format. API protocol remains 2.0.0.

Format **1.5.0** adds optional backend-managed todo workflow claims and config
workflow with automatic:false. The sequential 1.4 → 1.5 migration invents no
claims and changes no originals. Active storage uses existing migration,
transaction and receipt recovery. Ordinary HTTP and filesystem mutations preserve
but cannot forge/change claims; owner-local actions use BoardStore transactions.
Claims retain system, run ID, scope digest, repository, worktree, branch, base,
phase and message; completed stages add commit, tested_commit and preview URL.
Foreign claims cannot be taken over automatically. Interrupted stages require
explicit retry. Versioned deployment receipts beside retained worktrees are local
runtime evidence keyed by run and exact commit; newer-minor receipts cannot
complete an older writer's job. Preview data and logs remain local there.


Format **1.6.0** adds the workflow.enabled feature gate. The sequential 1.5 → 1.6
migration inserts enabled:false when absent, preserving explicit values, automatic
preferences, existing claims and unknown fields. Automatic behavior also requires
the feature gate. Older writers become read-only, preventing them from ignoring
the disabled feature. Migration and receipt recovery retain the shared storage
contract; no task records are altered except version metadata.

Direct merge after completed implementation does not change the stored format:
`tested_commit` remains optional evidence of a successful separate test/preview
step, and is never populated by merge. Existing phases, claims, receipts and
recovery retain their meanings. Older writers preserve these claims but may
require preview before accepting a merge retry. No migration is required.
