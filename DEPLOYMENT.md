# Migration-aware managed deployment

A code merge, a confirmed push, an installed runtime and a healthy migrated board
are separate outcomes. Only a verified deployment receipt closes a managed todo.
This contract applies to the configured `workflow_support.py unfertig restart`
recipe. Other artifact recipes retain their own configured deployment semantics.

## Before publication or shutdown

Integration still consults GitHub first, including its merge commit for an already
merged PR, and tests a retained candidate combined with current main. The managed
preflight runs that exact clean candidate against disposable copies of the live
board, receipts, portable configuration and effective local configuration. It
checks preservation, writable startup, byte idempotence and old-writer protection.
It refuses pending transactions/history, changing input and nonportable paths.
Snapshots use `--no-git`; private effective configuration never enters history.

If storage changes, the workflow enters `migration_required` before advancing
main or pushing. The pipeline shows **Migration review**, with current/target
formats and an exact candidate. Ordinary startup keeps its unchanged-storage
update gate. Even automatic delivery cannot approve a migration.

The owning wrapper must implement `scripts/managed_deployment.py` protocol 1 and
its supervisor startup reservation. Without it, preflight still detects the
migration; install the host contract and request a fresh assessment. Do not disable
the updater gate, substitute private supervisor locks or run a feature branch on
the production board. Install the host contract before bootstrapping the first
application version that uses it; review that initial published candidate through
the CLI below, since the older UI has no migration action.

## Reviewed action

**Migrate & deploy** submits the exact `review_id` displayed by preflight. Approval
is separate from Merge & restart. The coordinator drains active ticket workers,
verifies the retained tested candidate, main and GitHub again, and rechecks the
review. It then publishes that exact commit without force and starts the detached
host deployment. It never silently substitutes a rebuilt or newer candidate.

The host reserves ordinary startup durably, stops managed writers through the
supervisor, and obtains the board's exclusive lease (also excluding filesystem
clients). Before changing code or storage it retains exact local backups of data,
receipts, configuration/local overrides and the installed revision. It installs
only the reviewed published `origin/main`, runs transactional offline migrations,
checks preservation/idempotence, regenerates equivalent effective configuration,
and commits tracked board/configuration changes and both wrapper gitlinks. It
preserves unrelated staged work. It then starts the exact installed runtime under
the reservation, without invoking the ordinary updater, and verifies owner,
format, writable history, runtime revision and both committed pins. Successful
verification releases the reservation. The host never pushes the wrapper for you.

Approval fingerprints and recovery backups stay in ignored `.local/migrations/`.
Only opaque review IDs and nonprivate format/commit summaries enter board records.
Coordinator progress alone does not invalidate a review; task content, board
content, configuration and candidate/installed revisions do. A stale review
requires a new assessment. Exact backups always retain all receipts and originals.

## Public host CLI and recovery

Run these from the owning wrapper, using its own uv environment:

```sh
sh scripts/run_uv.sh scripts/managed_deployment.py capabilities
sh scripts/run_uv.sh scripts/managed_deployment.py review --candidate /absolute/clean/candidate
sh scripts/run_uv.sh scripts/managed_deployment.py check --review REVIEW_ID
# Only after explicit migration approval and publication of the exact candidate:
sh scripts/run_uv.sh scripts/managed_deployment.py deploy --review REVIEW_ID
sh scripts/run_uv.sh scripts/managed_deployment.py status
sh scripts/run_uv.sh scripts/managed_deployment.py recover --review REVIEW_ID
```

The CLI review is a storage assessment; run the candidate's required code tests
before approving publication. For a manual rollout, align the deliverable checkout
cleanly to the exact published candidate before `deploy`. Never reset unrelated
work to make this check pass.

A failed or interrupted migration retains its reservation and stage journal;
`recover` resumes forward under the same exclusive maintenance ownership. It
remembers which configuration an interrupted transaction belongs to. It retries
history without restaging private files. After a failed restart, an already
healthy exact deployment is verified without restarting again. An externally
completed migration can also be verified through this public operation. Never
rewrite internal workflow claims or fabricate deployment receipts.

The board's **Recover deployment** action uses retained publication evidence and
GitHub state, then asks the host to resume or verify that deployment. It does not
merge the ticket again. If the service is down, use the host CLI first; the board
can reconcile its receipt after restart. An advanced remote/main requires fresh
review. A stale supervisor session remains subject to the host's existing process
inspection rule; recovery never signals a PID read from disk.

`cancel --review REVIEW_ID` can release a reservation only before any installation
or migration was attempted. It keeps all evidence and does not start tools. Once
installation may have begun, resume forward; automatic rollback across a changed
storage format is deliberately unavailable. Keep backups until recovery is no
longer needed. Do not label a published or migration-pending task as deployed.
