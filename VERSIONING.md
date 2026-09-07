# Persisted formats and compatibility contract

Read this contract when processing ideas and before changing persistence or the
API. Every format change must include a deterministic migration, useful minimal
defaults, compatibility handling, and regression tests in the same change.

`format_version` is a major.minor.build string. Current storage is `1.2.0`.
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
