# Automatic programmatic startup migrations

A merge, published code, installed runtime and healthy migrated board are separate
outcomes. Supported storage upgrades are an ordinary part of an authorized update
and restart. They do not pause merging for a second migration approval.

## Runtime contract

Every release that changes persisted formats ships deterministic sequential Python
migration functions in versions.py, registered from each supported predecessor.
BoardStore.initialize applies them before the service accepts requests. This works
for standalone and embedded instances, pinned releases and offline restarts.
Neither migration nor recovery calls an LLM, Codex, GitHub or another network service.
The application may use an agent to develop migration code or resolve a source
merge conflict; executing the resulting migration is exclusively programmatic.

Defaults apply only to missing optional fields. Preserve originals, attribution,
explicit values, extensions, request identities and historical claims. Validate
before writes; refuse unsupported/corrupt inputs with useful diagnostics. Newer
minor data remains read-only to older runtimes; unsupported majors are refused.
Transactions roll forward after interruption. Repeat startup is a byte no-op.
Do not require an operator to interpret records or supply routine migration steps.

## Host startup and updates

Install the matching wrapper startup contract before relying on automatic managed
updates; old wrappers deliberately rejected storage changes. Update reusable host
templates as well when distributing this contract to other wrappers.

The stopped-session updater validates the candidate code and rehearses migrations
on disposable copies of host data and both shared/effective configurations. It
checks original preservation, idempotence and older-writer protection, then promotes
only the exact validated runtime. Validation failure retains the prior runtime.

Before launching Unfertig, the wrapper runs scripts/startup_migration.py, including
on pinned or offline starts. It holds the exclusive board lease, backs up exact
board/configuration bytes into ignored local recovery storage, migrates shared and
machine-local configuration sequentially, preserves local overrides, and commits
only tracked shared storage/configuration. It does not require a deliverable
checkout, move development branches, or push history.

A durable local checkpoint identifies the runtime and configuration being migrated.
After interruption, the next Start retains that runtime and resumes forward. A
failed migration/history commit prevents service launch; it never starts an older
writer against partially migrated data or clears evidence. Actual corruption,
unsupported data, unresolved local edits or failed Git configuration require fixing
the reported cause, then Start again. Normal migrations need no intervention.

## Integration and deployment evidence

The coordinator checks GitHub first and tests the combined candidate. Programmatic
preflight occurs before publication/shutdown; supported storage changes continue
through the already-authorized Merge & restart. The restart recipe uses the host
supervisor and verifies the exact installed commit. Never infer deployment from a
healthy process on an older revision. Failed delivery keeps the integration queue
paused; recovery retries programmatic restart without asking an agent to migrate.

Historical migration_required claims can use Merge & restart for fresh preflight.
The legacy scripts/managed_deployment.py review/deploy/recover CLI remains available
for explicit maintenance and recovery of old reservations. Do not delete those
reservations or fabricate historical receipts. New normal updates use startup
migration rather than creating a manual review gate.

## Required verification

Test upgrades from all supported earlier formats, missing-field defaults, explicit
invalid values, original/extension preservation, both HTTP/filesystem transports,
interrupted transactions, configuration recovery, failed history commits and byte
idempotence. Exercise a real supervisor restart with a storage-changing candidate
and no Codex executable. Failed migration must leave recoverable evidence and no
writable service. Source relocation remains a separate explicit operation.

## Queue failures and conflict recovery

An outstanding failed delivery pauses subsequent integration
entries. Their saved order, action requests and approval scope remain intact
across service restarts. The pipeline and ticket details show the blocker and
waiting cause. Retry the blocked integration or Recover deployment through the existing public action. **Skip & continue queue**
is a separate explicit decision: it retains the failed ticket and evidence, does
not mark it deployed, and grants no new publication permission.

Ordinary conflicts automatically enter **Agent resolving merge conflict**, then
**Testing resolved candidate**. The integration agent handles code, documentation,
tests and migration successors in the retained isolated candidate. It preserves
the original branch and runs combined checks before the coordinator rechecks main,
PR state and scope and continues existing authorization. A restarted coordinator
resumes resolution only after the process receipt excludes a live or uncertain
worker. Genuine blockers show attempts, affected files and the minimal required
input/access; unchanged failing candidates stop for lack of progress. Exact Git
command, revisions, bounded stdout and stderr and resolution reports are retained.

Incomplete, mismatched or newer-writer deployment receipts remain pending and keep
the queue blocked. A newer receipt explicitly identifies an outdated coordinator.
Recover through the host contract; never treat a healthy retained older runtime
as deployment of the published candidate. Closed historical runs do not create
new queue barriers; external completion reconciliation remains a separate concern.

The optional real-supervisor conformance tests copy only reusable host scripts
into disposable repositories. Run them with an explicitly selected host context:

```sh
UNFERTIG_TEST_HOST_CONTEXT=/absolute/wrapper uv run --no-project --python 3.12 python -m unittest test_supervised_queue -v
```

GitHub and the agent are deterministic fixtures; Git merges, combined checks,
receipts, service/supervisor restarts, HTTP recovery and queue transactions are real.
