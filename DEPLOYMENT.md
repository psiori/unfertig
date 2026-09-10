# Publication and instance maintenance

**Merge & push finishes the task.** The coordinator tests the exact combined
candidate, merges locally and confirms the main push for every changed repository.
UM children publish before their wrapper pins. This transaction never updates the
installed runtime or restarts the instance. A development pin and an installed
runtime pin can legitimately name different commits.

## Configurable commands after publication

`workflow.after_publish` is an optional list, empty by default. Each entry has a
unique stable `id`, `repository`, `branch`, `command` argument array, optional `cwd`
and `timeout_seconds` (1–3600, default 60). Repository/cwd paths resolve relative
to the instance config; omitted paths and branch use the primary repository/base.
Commands run without an implicit shell and do not expand workflow placeholders.
Only trusted instance configuration can supply commands. They must be foreground,
bounded and idempotent for the event ID; do not launch untracked background writers.

For an UM host with the cooperative updater installed:

```json
"after_publish": [{
  "id": "update-unfertig",
  "repository": "../../../unfertig",
  "branch": "main",
  "cwd": "../../..",
  "command": ["sh", "scripts/run_uv.sh", "scripts/request_tool_update.py", "--tool", "unfertig"],
  "timeout_seconds": 60
}]
```

The closing BoardStore transaction also records the matching events. A detached
executor receives `UNFERTIG_EVENT_ID`, `UNFERTIG_PUBLISHED_COMMIT`,
`UNFERTIG_PUBLISHED_REPOSITORY` and `UNFERTIG_PUBLISHED_BRANCH`. Only a matching
published repository/branch triggers the hook; board saves and runtime-pin commits
do not. A hook cannot roll back publication or reopen/block a completed task.

Inspect **Instance maintenance** or `GET /api/maintenance`. Hook output/receipts
remain in `BOARD/.local/post-publish/`; ignore `.local/` in the owning repository.
A failed command is explicitly retryable using the maintenance button, or token-
protected `PUT /api/maintenance` with `action:retry_hook`, `todo` and `event`.
Retries keep the event ID and increment the attempt. An interrupted executor with
unknown side effects fails for inspection and explicit idempotent retry; it is not
silently reexecuted. Receipt locks prevent duplicate concurrent commands.

## Cooperative restart protocol

The host updater requests a restart; it never kills the active instance or changes
its files. The supervisor supplies `UM_RESTART_REQUEST`, `UM_RESTART_STATUS` and
`UM_RESTART_SESSION`. The request is host schema 1 with `protocol_version:1.0.0`
and that exact session. The application polls the file in its service loop.
Its acknowledgement includes the session, phase, blockers and startup-captured
runtime commit. Stale session acknowledgements never authorize installation.

On a matching request, Unfertig pauses new implementation, processing and
integration launches. Queues are retained. Existing workers, processing, hook
commands and API writes finish; pending local Git history and uncertain retained
workers prevent exit with visible diagnostics. Previews close after drain.
Finally the service rejects new mutations, acknowledges `ready`, releases its
server/board handles and exits 75. The host requires both the matching ready
acknowledgement and its own child's graceful exit before installing anything.

The host coalesces requests while draining, freezes the current batch at the safe
point, fetches latest `origin/main` once and installs that exact commit. Requests
arriving after batch selection remain pending for the next cycle. Requests for an
already installed revision are no-ops. Startup and update results are independent
of hook acceptance: a completed request hook does not mean an update is installed.
`UM_UPDATE_STATUS` lets the application display the host's separate status.
Unsupported hosts leave queued requests pending and report the missing contract;
standalone instances expose hooks but do not claim a supervised restart ability.

## Trusted main and real startup checks

Integration owns code tests, combined-candidate checks and migration regression
coverage before publication. The normal updater trusts main and runs no repeated
code test suite or disposable host-data rehearsal. It retains installation guards:
correct Git origin/branch, clean mounted checkout, preservation of separately staged pins,
fast-forward history and no concurrent wrapper Git operation. Failed installation
keeps the prior runtime where recovery is safe and records the failure separately.
A clean explicitly selected checkout may differ from the recorded runtime pin;
that bookkeeping does not block its startup migrations. The updater works from the
actual installed revision and reconciles the pin when confirming/installing upstream.

Every release changing persistence ships deterministic sequential migrations in
versions.py, useful defaults and recovery tests. BoardStore initializes supported
formats before serving. Actual startup validates compatibility and data, preserves
originals/attribution/extensions, and recovers transactions without an LLM, Codex
or network service. Unknown/newer formats follow VERSIONING.md; never downgrade.

For UM, scripts/startup_migration.py holds the exclusive board lease, backs up
exact stopped board/shared/local configuration bytes, migrates in sequence and
commits only tracked shared state. This also runs on pinned/offline startup.
Shared settings and explicit local overrides are authoritative. Generated machine
configuration is disposable: migrate shared state, then rebuild and normalize the
effective configuration from those sources. Stale defaults and partially written
caches must not block startup. Store preferences in local overrides, never in the
generated cache. Old transactions targeting that cache recover before it is rebuilt.
Idempotence is covered by migration regression tests, not another startup rehearsal.
Interrupted startup retains the exact runtime and journal for forward recovery.
Migration or history failure stops that tool before it serves; it does not undo
published code. Fix the cause and retry maintenance. Do not clear backup/journal
files or start an older writer against partially migrated data.

## Legacy runs and recovery

Existing restart recipes, automatic_deploy values and deployment receipts remain
historical evidence. New integration ignores those recipes and closes at publication;
migration never converts an old command into a new hook or replays old publications.
Automatic integration now requires automatic_merge and automatic_publish. Hook
execution is separately authorized by explicit configuration.

**Finish published task** rechecks retained publication and exact combined test
identity before closing a legacy failed delivery, without invoking deployment.
If evidence is missing, use Merge & push for fresh verification. Multi-repository
recovery verifies already-published entries and completes missing publications,
including context-pin publication after an earlier interrupted push. Bounded,
read-only PR polling handles GitHub lag; successful branch pushes are not repeated.
Actual head changes, wrong identity and non-fast-forward updates remain blockers.

Old detached receipts can still reconcile in-progress historical delivery. The
host's legacy managed_deployment recovery CLI is reserved for an existing host
reservation; never delete that reservation to bypass its ownership. The obsolete
workflow_support.py unfertig restart recipe now refuses in-transaction restart.

Test application behavior with `uv run --no-project --python 3.12 python
suite_runner.py --javascript`. Host tests copy only reusable scripts into temporary
repositories and exercise real supervisor drain/install/start, request coalescing,
stale acknowledgements and crash recovery. Every format-changing release also
maintains HTTP/filesystem conformance and supported migration/recovery tests.

## Relaxed integration and deferred checkout synchronization

Hosts may opt into `workflow.integration` as documented in README.md. Verified
main publication is independent of shared-checkout synchronization: staged,
unstaged and nested board history stay with their owner. A deferred sync is a
maintenance observation, not a failed publication or a request to reset/stash.
Detached candidate dependency worktrees never replace installed runtime files.
Use the retained tested/final/published revisions and exact push intent for
recovery. Only explicit metadata-equivalence configuration permits a tested and
final revision to differ. Install/update/startup continues to trust published
main and retains its existing backup, compatibility and migration guards.
