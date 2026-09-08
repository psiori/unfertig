# unfertig

A local, repository-backed idea and todo board for people and agents. It runs with Python 3.12 via uv, uses no third-party Python packages, and has no frontend build step.

Renamed from Little by little on 2026-09-06. The `little-board.` browser preference keys are retained so existing identity and view settings continue to work. Original records retain historical wording.

## Start

Install once with `install.command` on macOS, `install.bat` on Windows, or `sh install.sh` on Linux. Then use the corresponding `start` launcher. Keep its terminal open; stop with Ctrl+C. Launchers work from any directory.

In a standalone checkout, the default board is `board/data.json` plus `board/todos/`. A missing default board initializes one onboarding todo, “Add your first idea”. Existing data is never replaced or reseeded. Git must be installed and the checkout must belong to a Git repository with a configured identity. Each meaningful save is committed locally; remote pushes require the explicit Push action.

For hosted installations, keep runtime code independent of development branches
and store configuration/backlog in the owning host repository. Follow that host's
launch instructions. Test feature branches with a separate temporary board and
port using `--data` and `--no-git`.

## Embed as a submodule

Add the app at any submodule path in your project. Put `config.json` in **`state/unfertig/config/` under the project repository root**, following `config.parent.example.json`:

```json
{
  "mode": "embedded",
  "data": "../data/data.json",
  "repository": "../../.."
}
```

Run the app's start launcher. A registered submodule discovers this parent config automatically. Missing parent configuration is an error, rather than a fallback to the app's own board. Explicit `--config /absolute/path/config.json` also works.

Paths in the config are relative to the config file. The optional `--data` overrides its data setting, using the same relative base; without a config the base is the app directory. Configuration precedence: `--config`, `--state-dir`, `UNFERTIG_CONFIG`, `UNFERTIG_STATE_DIR`, discovered superproject config, app-root `unfertig.json`, standalone default. A state directory contains `config/config.json` and `data/`; its config uses `../data/data.json`. Explicit selectors override environment selectors. Standalone or hosted installations can use an external state directory; normal saves still require a Git repository owning the data. Existing standalone defaults remain supported for compatibility. Embedded data must be outside the app checkout and inside the configured owning repository. Explicit missing boards require `--init`; this avoids creating a new board due to a typo. No global search for arbitrary configs is performed.

The ideas JSON, individual todos, locks, journal, receipts, and pending-history state all follow the configured board directory. Git commits are made in the repository that owns **the data**, not the repository containing the server. A host edit never commits the submodule or another project's data. The API and UI expose the resolved locations, and copied briefings point to those same locations.

## Everyday use

Enter your identity in **Working as**. Capture original thoughts with **Add idea** (or Cmd/Ctrl+Enter). **Create manually** refines one idea; **New todo** creates a standalone task. IDs are allocated by the server. Original wording and attribution are immutable. An idea is processed when a todo links to it.

Expand a todo to edit its name, description, priority, group, tags, status, or implementation references. Save explicitly. Grouping, filters, sorting and folding organize the board. AI and human briefing buttons copy the saved task and workflow, and do not start work. **Copy all-pending briefing** delegates planning only. It references the authoritative board and PROCESS.md, without copying idea bodies or IDs. The agent reads all ideas still pending at execution time, including newly saved ideas, regardless of UI filters; if none remain it reports "Nothing to process" without writing. Re-read before saves and follow PROCESS.md for overlap, attribution, record revisions, processing-race conflicts and identical-request retries.

One worker per task is coordinated through status/progress notes. Independent task edits do not conflict. Stale same-task edits are rejected with a copyable draft; copy your draft, reload, and reconcile only intended changes. Keep unsaved text before a browser restart. The app polls for updates and preserves drafts.

Each meaningful saved edit creates a data-only local Git commit; identical/timestamp-only saves do not. Unrelated staged work is excluded. A failed commit leaves data saved and displays **Git commit pending**. Fix the reported Git issue and click **Retry Git commit**. Further meaningful saves wait until that commit succeeds. This is local history, not remote backup.

## Agent and offline workflow

Read [PROCESS.md](PROCESS.md). First locate the authoritative board through `/api/state` context; do not assume it is in this checkout. The API retains per-record revisions, conflict handling and idempotent request IDs. Whole-board writes are rejected.

With the server stopped, use the same safe writer:

```sh
uv run --no-project --python 3.12 --script server.py --config /absolute/config.json --snapshot
uv run --no-project --python 3.12 --script server.py --config /absolute/config.json --apply /absolute/request.json
uv run --no-project --python 3.12 --script server.py --config /absolute/config.json --check
```

An OS lock prevents a second server/offline writer against the same board. Do not directly edit active JSON. See PROCESS.md for the request format, closure/commit rules and recovery.

## Verification and recovery

```sh
uv run --no-project --python 3.12 -m unittest discover -s . -p 'test_*.py'
```

Tests use temporary boards/repositories. Use `--data /absolute/temp/data.json --no-git` for disposable previews; never disable real-board history to evade an error. `--port` and `--no-browser` are available.

A missing Git repository, invalid config, missing board, or wrong embedded owner is reported before normal service begins. Resolve paths instead of copying data into the app as a workaround. The HTTP service binds to localhost, validates its Host/Origin/session token, and only serves allowlisted UI assets and APIs.

An interrupted transaction is rolled forward on restart. Preserve journals and receipts. Before manually recovering, copy the whole board directory. Split migration recovery, receipts and source snapshots belong to the host repository; never publish mixed-board backups in the app repo. See `split_board.py` and the host migration guide for the one-time extraction. Repository publication and pushes require explicit authorization.

## Instance ports

Setup asks for a port and saves it in the selected configuration. The default is **8765**; the suggested unfertig range is **8765–8799** (an application convention, not a reserved range). The prompt lists ports currently occupied on localhost, rejects occupied or invalid choices, and preserves the existing port when Enter is pressed. Ports outside the suggested range are accepted from 1–65535. Availability can change before startup.

To change it later, stop the board server and run `sh start.sh --configure-port` (with `--config` or `--state-dir` when needed). This exits after saving; commit the config in its owning repository. Normal startup never prompts. `--port` overrides the saved port for that run only. Without a saved port, startup uses 8765. Each instance needs a distinct board directory and port.

## Project title

Set `"project_name": "My project"` in the instance configuration to show
`unfertig · My project` in the masthead and browser tab (which also retains
`Ideas & todos`). The value must be a string; surrounding whitespace is trimmed.
An explicit empty or whitespace-only string suppresses the project suffix.
Explicit values skip title discovery entirely.

Otherwise startup inspects ancestors of the resolved installation directory,
stopping at the nearest enclosing Git repository or Git superproject. The
nearest `node.json` with `kind: "project-wrapper"` supplies its top-level `name`
(the wrapper name, not `project.path` or a repository identifier). A qualifying
wrapper without a usable name supplies no suffix. Unreadable/malformed metadata
is ignored. If no wrapper qualifies, a registered Git superproject supplies its
directory basename. An ordinary enclosing repository alone does not qualify.
No host means no project suffix; the app's own repository name is never used.
Nested repository boundaries prevent discovery of unrelated outer wrappers.

The name is resolved once at startup, exposed as `context.project_name`, and
rendered as text, including characters such as `<` and `&`. Restart after changing
configuration or metadata. Discovery uses the installation location, independent
of the launch working directory, external board/config paths and board ownership.
Use an explicit name when an external board represents another project. Title
resolution never changes data paths, Git ownership, bootstrap or operating mode.

## Publishing saved work

Each idea/todo's publication label compares its saved content with
its version in the configured upstream branch. **Local only** means that saved
record differs from the last fetched upstream, including an edit to an existing
record. **On upstream** means the saved content matches; it is not a claim about
unsaved browser drafts or a live remote check. **Not committed** identifies saved
content missing from local HEAD. **Remote unknown** means comparison is unavailable.
Ideas are compared individually even though they share a file.

Ordinary polling and saves do not contact the network. The subtle **Refresh**
arrow immediately before **New todo** fetches only the configured upstream branch.
Its tooltip shows the check time; routine publication details have no separate panel. An offline or
authentication failure makes remote status unknown until a successful check.
Configure a single upstream in the board-owning repository yourself. Detached
HEAD, missing upstream, differing push/fetch destinations or multiple push URLs
require manual configuration. Unfertig preserves that repository's SSH selection.

**Push all commits** appears when there are outgoing commits. Review its branch
and remote confirmation: it publishes every outgoing branch commit, including
committed code/configuration changes outside this board. There is no per-record
push, cherry-pick, force-push, implicit branch selection or automatic push on save.
Pending local Git history, uncommitted/untracked files and ongoing Git operations
block publication; finish them manually. Save or reset browser drafts first.

The server holds the board lock, fetches, and attempts a standard merge in a
detached temporary worktree. It validates records, immutable attribution, source
links and three-way record contents before pushing. Same-record changes on both
branches, ID collisions and duplicate concurrent idea processing need manual
resolution even if Git could merge the JSON text. Text conflicts also leave the
live branch, index and working tree untouched. Independent changes to separate
records can merge automatically. No candidate app code is executed for validation.
Git commands have 30-second timeouts; board reads/saves wait during publication.

The candidate is pushed before the live checkout advances. A rejected push leaves
local content untouched; remote races are reported without forced retries. A lost
push response is checked against the remote. If accepted, the live checkout is
fast-forwarded and records are reread; existing browser drafts retain their record
revisions and cannot silently overwrite incoming work. If local synchronization
fails after acceptance, the message explicitly says that the remote was updated.
Do not try to roll back the remote. The candidate remains reachable through a
`refs/unfertig/publication/...` recovery ref for manual reconciliation. The same
ref preserves a candidate after an uncertain push or interrupted process. Inspect
these refs and remote history before retrying; they are not pushed by this action.
Remove a recovery ref manually only after its outcome and local recovery are known.

This coordinates one managed board server. Avoid external Git commands during
publication; detected changes abort publication or require manual synchronization.
Git and filesystem operations across a remote and local checkout are not one
atomic transaction. A process/machine failure during Git's final fast-forward can
require normal Git recovery using the retained candidate. Never reset/clean away
unrelated work, delete live locks, or restore only one file of a split board.

Publication tests use disposable repositories and local bare remotes:

```sh
uv run --no-project --python 3.12 -m unittest test_publication -v
node --test test_title.cjs test_publication.cjs
```

## Aggregation and initials

Choose `--configure-mode` during stopped setup, or configure an empty inbox:

```json
{
  "format_version": "1.3.0",
  "mode": "aggregation",
  "project_id": "workspace-inbox",
  "project_name": "Workspace",
  "data": "../data/data.json",
  "port": 8765,
  "sources": [
    {"data": "../../../alpha/state/unfertig/data/data.json", "url": "http://127.0.0.1:8766", "project_id": "alpha"},
    {"data": "../../../beta/state/unfertig/data/data.json", "url": "http://127.0.0.1:8767", "project_id": "beta"}
  ]
}
```

Set the corresponding `project_id` in each source config and restart it first;
verify source IDs and exact data paths with `/api/state`. Configuration paths are
relative to the config file. Up to 20 explicit JSON locations are supported.


To discover existing boards at startup and during refresh, add `search_paths`
alongside any exact `sources` (format 1.11):

```json
{
  "search_paths": [
    "../../../um-*/state/unfertig/config/config.json",
    "../../../companies/*/projects/um-*/state/unfertig/config/config.json",
    "../../../companies/*/clients/*/state/unfertig/config/config.json"
  ]
}
```

Each matched **config**, not data file, must declare its existing board's `data`
and explicit stable `project_id`, plus service metadata such as:

```json
{
  "aggregation_source": {
    "app_root": "../../../tools/unfertig",
    "url": "http://127.0.0.1:8766"
  }
}
```

The app path is relative to that matched config and must contain PROCESS.md.
The loopback URL must match the actual service; discovery never infers it from
`port`, starts services or creates boards. Filesystem-only mode can omit `url`.
Keep machine-specific metadata in your host's supported local config and target
that effective config when necessary; examples are not installation settings.

Patterns support `*` and `?` within components, but no `**` or brackets. They
follow directory symlinks only along those finite components, including outside
the pattern's prefix. Canonical aliases and exact entries deduplicate when their
metadata agrees. The limit remains 20 unique current boards. Unmatched/invalid
patterns appear in source status; removed boards retain cached rows and project
filters, with writes blocked. Returning matches recover without restart. Saved
routing claims keep their destination and retry request even across restart.
See [TRANSPORTS.md](TRANSPORTS.md#config-search-paths-format-111) for all bounds,
refresh scheduling, identity conflicts, symlink behavior and recovery. Configure
and migrate explicitly under [VERSIONING.md](VERSIONING.md); discovery itself
never migrates sources or grants write authorization.

An explicit `project_id` always takes precedence. If omitted, the ID is the first
32 hexadecimal characters of SHA-256 of the resolved board data path relative to
the enclosing Kermit workspace, using POSIX separators and UTF-8. The workspace
is identified by its `node.json` application ID `salange/kermit` and runtime root
`state/node.json`, regardless of its directory name or location. Without that
workspace, the nearest enclosing Git repository is the root; without Git, the
configuration directory (or application directory without a config) is the root.
This matches Kermit's source launcher. Moving the whole workspace preserves IDs;
moving a board within it changes a fallback ID. For existing boards, explicitly
save the old ID before upgrading to preserve aggregation and provenance links.
Use explicit distinct IDs for independent copies with identical relative layouts.
No stored format or record migration is introduced by this resolver change.

Canonical duplicate paths are deduplicated; conflicting aliases/identities and
self references are rejected. No arbitrary recursion or nested aggregation is
supported. HTTP-only sources need a separately started service. Filesystem sources
need no service; Unfertig never launches one. Existing boards with todos cannot become aggregators implicitly.

The aggregator presents read-only child todos with a project column, project
filter, and project → group nesting. Group/tag filter choices include the project
and work equally in flat views; identical labels do not merge across projects.
The source's configured/discovered project_name is used; an empty name or an
uncached offline source falls back to its owning repository's node.json
repository name, then its checkout directory name (or project ID if unavailable). Source ideas
appear as read-only reference sections; their processed filter uses source todos.
Open actions target the configured origin and record fragment; unavailable
services show a label instead of launching anything.

Visible pages poll the cached view every four seconds. Independent background
checks refresh healthy sources at most every four seconds and retry failed
sources after 20 seconds, measured from completion. Only one check per source
runs at a time; slow/offline sources do not block the view or other sources.
Each source snapshot supplies record revisions; a successful refresh
replaces its read view. On failure the server retains its last successful
in-memory snapshot, labelled stale. After restart there is no disk cache: a failed
source says unavailable/no cached records, never "empty". Discovery never migrates child files; accepted interrupted journals recover under
the shared writer lock. Change config and restart to alter sources.

Ideas entered here stay in this inbox. Select a project immediately before **Add
idea**, or later on the idea. Use **Copy all-pending briefing** for explicit/inferred
routing of every pending inbox idea at execution time; unclear ideas remain red and pending. See PROCESS.md for exact routing,
provenance, retry, preflight and local-commit semantics. Routing requires source
format 1.2, and never pushes or resolves conflicts automatically.

**Initials** is the only top-level identity input. Browser saves require 1–12
ASCII letters/digits starting with a letter; whitespace is trimmed and letters
are uppercased. These initials identify new human records/actions and prefix new
IDs. They are not unique or authentication, and give less descriptive attribution
than a full name. Changing initials affects future actions only.

Saved initials are reused. A legacy saved Author preference is retained but
ignored, never guessed into initials: enter initials once if none were saved.
Empty or invalid initials block browser saves with a prompt, without losing drafts.
Existing authors, IDs and provenance remain unchanged. Todos made from existing
ideas retain the original requester; human creation/closure uses the acting
initials. Agents still supply their actual identity in actor/created_by/closed_by
and preserve original requester attribution, including routing. The API continues
to accept separate actor identity and optional initials (empty means legacy IDs).
No stored-format migration is needed.

Format 1.3 is a storage migration. For managed existing instances follow
VERSIONING.md's automatic startup migration, backup, disposable rehearsal
and idempotence checks. No LLM or separate routine migration approval is required.


### Filesystem access and HTTP-first fallback

Set global `"transports": {"http": true, "filesystem": true}` for HTTP-first
fallback, or `{"http": false, "filesystem": true}` for filesystem-only. Both
false is an error. Omission retains HTTP-only; there are no source overrides.
For filesystem-enabled sources add explicit config and app checkout locations:

```json
{
  "data": "../../../alpha/state/unfertig/data/data.json",
  "project_id": "alpha",
  "config": "../../../alpha/state/unfertig/config/config.json",
  "app_root": "../../../alpha/tools/unfertig",
  "url": "http://127.0.0.1:8766"
}
```

Omit `url` when HTTP is disabled. Paths resolve relative to the aggregator config;
they do not grant write authorization. Configure/migrate the child first using
its own installation. Filesystem discovery reports a block for older formats,
missing paths or incompatible writers. Upgraded POSIX services permit coordinated
filesystem reads and routing while running, even without HTTP access. Windows
filesystem access is explicitly blocked; its HTTP mode retains exclusive locking.

The source status shows transport and fallback reason. HTTP rejection never
triggers fallback. A lost HTTP write response recovers the same request/receipt.
Filesystem records open as details with their location, without launching a
service. Read [TRANSPORTS.md](TRANSPORTS.md) for exact error classification,
failback, locks, stopped migrations, recovery and the required parity suite.
Add `.operation.lock` and `.service.lock` to each host's runtime ignore rules.

Run the shared conformance and regression suites:

```sh
uv run --no-project --python 3.12 python -m unittest discover -v
node --test test_*.cjs
```

### Process ideas with Codex

**Create todos with Codex** starts a planning-only local Codex CLI job in the
resolved project directory. It reads repository instructions, current design,
saved context and the selected developer's rules, then re-reads the authoritative
board. It creates/refines open todos through the board API, whose normal history
commits them locally. It does not implement or publish them. Aggregator inboxes
use the existing verified routing workflow and configured source projects.
Conversation history from an existing Codex task is not inherited.

Install and sign in to Codex CLI first (`codex login`). The launcher uses
`codex exec --approve-for-me -C <directory>`; approval
requests go through Codex's automatic review using its workspace-write sandbox. CLI user settings and authentication
remain in effect. No shell command interpolation or credentials in board config.
Processing details show the resolved directory, completion summary, questions or
failure. A failed/ambiguous idea is not retried automatically during that server
session; use the manual button after addressing it. Stop shuts down its worker.

Configure optional `processing` in the instance config (relative paths resolve
from that file), and restart:

```json
"processing": {
  "enabled": true,
  "automatic": true,
  "working_directory": "../../..",
  "developer": "sl",
  "executable": "codex",
  "idle_seconds": 600,
  "closed_seconds": 90
}
```

Migration defaults to manual processing (`automatic: false`). Omit
`working_directory` to inspect the known board-owning repository and at most two
parents for `node.json` or `AGENTS.md`, otherwise use the owner. Explicit directories
always win and missing explicit directories fail clearly. No recursive project
search. At startup, a usable `executable` wins; otherwise Unfertig discovers
`codex` on PATH, common user/system CLI installations (including mise), and
macOS/Linux desktop bundle locations. Both processing and implementation retry
discovery before launching, so installing Codex does not require rewriting the
config. Discovery stays in memory; no host-specific path is saved in Git.
Use an absolute CLI path for a custom installation. If no executable is found,
the board remains usable and Codex actions report the setup requirement. Codex
must already be installed and signed in. `enabled: false` disables all launches.

Automatic processing requires a browser visit during the current server session,
then either ten minutes without activity or a 90-second grace period after the
last browser tab disappears. Input/click/key activity is aggregated in 20-second
heartbeats; each batch resets inactivity. Multiple tabs share the backend timer.
Live unsaved drafts postpone automatic processing. Closure is best effort:
pagehide sends a final heartbeat; crashed/throttled tabs expire after 75 seconds.
Only saved ideas are ever processed; drafts are not uploaded by heartbeats.

Each newly saved idea receives an immutable, opaque `captured_system` fingerprint
from its writing system. Automatic jobs select only ideas captured on the current
system, excluding older ideas without provenance and ideas synchronized from
another machine. Manual processing can select all pending ideas. Unsupported
system identity disables automatic selection rather than guessing. A running
job excludes another launch on the same board. Other manual agents must still
follow the existing source-link/revision conflict and routing-receipt rules.

The scratchpad groups capture first, saved ideas and their filter second, and
processing last. The processing area keeps the Codex action, bulk briefing and
run status together. Expand its automatic/manual summary for timing, system scope,
working directory and the latest result. Individual idea rows offer manual todo creation; the all-pending briefing lives
in the processing area. Copied briefings use live pending state, while launched
Codex jobs retain their explicit launch-time ID allowlist and automatic system scope. Unsaved drafts and an empty queue disable the Codex
action immediately; activity remains batched into the existing heartbeats.

## Implementation workflow

Use **Implement with Codex → optional Test branch → Merge & push**.
After implementation, collapsed rows show **Merge & push** as the next workflow
action, including after an optional Preview failure. Unfold the row for
**Preview (optional)** after the merge control. Running and recovery actions retain
their existing meanings; direct merge never invents Preview verification.
Implementation runs in a `codex/…` branch in a locally excluded
`.worktrees/unfertig/` checkout. It receives the host's instructions, design,
developer rules, saved context and original ideas. It uses the configured Codex
executable with `--approve-for-me`. Progress & branch details shows live output.
A finished agent report, new commit, clean worktree and configured checks are
required before preview or merge. The task closes when all changed repositories have been tested, merged and pushed. Instance maintenance is shown separately.

The leading todo icon shows a small rotating ring while fresh owner-local workflow
status confirms an active implementation worker, including its required checks.
It remains visible on collapsed rows and recovers after refresh. The accessible
label says “Implementation running”; reduced-motion preferences keep the working
ring static. Started status alone, foreign or interrupted claims, and waiting for
preview or merge do not activate it. Activity is polled every two seconds; failed
polls clear the indication and evidence expires after six seconds without a fresh
response. Existing progress details remain available during connection loss.

The optional Test branch step reruns checks and launches an isolated preview,
opening web previews in a new tab or launching a native window. Data and logs live beside the worktree,
never in the live board. Stopping the board stops previews. Merge confirms the
exact selected commit. A successful Test branch / Preview is shown only when
it matches that commit; no notice or message space is shown for an untested commit. It
fast-forwards a clean checkout on the configured base, pushes without force,
then closes the task. A serialized integration candidate combines current main
and the ticket, with mandatory checks of that exact combined commit. Main or PR
changes invalidate the attempt. Optional post-publication commands run separately;
their failures and pending updates never undo publication or block the task queue.
Failed pushes and interrupted integration remain visible for explicit retries.
Worktrees and publication evidence are retained.

Configure `workflow` in the instance config and restart. `enabled` defaults to
false: all implementation/preview/merge controls are hidden and backend workflow
actions are rejected (HTTP 403). Set `enabled: true` to expose manual controls.
`automatic` is a separate switch and also defaults false. Disabling the feature
also prevents automatic kickoff and background workflow reconciliation, while
retaining saved run records for later inspection. Aggregators always disable it.

Configure the commands below for your project. Commands are argv arrays,
without a shell. Named placeholders are `{worktree}`, `{repository}`, `{context}`,
`{data}` and `{port}`. Commands come only from administrator configuration.

```json
"workflow": {
  "enabled": false,
  "automatic": false,
  "max_workers": 4,
  "automatic_merge": false,
  "automatic_publish": false,
  "automatic_since": "2026-09-07T00:00:00+00:00",
  "repository": "../../../unfertig",
  "base_branch": "main",
  "test": ["uv", "run", "--no-project", "--python", "3.12", "--script", "{worktree}/workflow_support.py", "unfertig", "test", "--repository", "{worktree}", "--context", "{context}"],
  "preview": ["uv", "run", "--no-project", "--python", "3.12", "--script", "{worktree}/workflow_support.py", "unfertig", "preview", "--repository", "{worktree}", "--context", "{context}", "--data", "{data}", "--port", "{port}"],
  "preview_url": "http://127.0.0.1:{port}",
  "after_publish": [],
  "timeout_seconds": 3600
}
```

Paths resolve from the config file. Omit repository to use the processing
context's node.json project.path, otherwise that context itself. Missing declared
paths block; no recursive project discovery occurs. Test and preview commands
are required for single-repository workflows. UM context-only work uses metadata
checks. `after_publish` defaults to an empty list. Its commands are optional trusted
configuration, filtered by repository and target branch; see [DEPLOYMENT.md](DEPLOYMENT.md)
for the generic hook and cooperative restart contracts. Old `restart` recipes and
`automatic_deploy` values remain preserved for historical evidence and are ignored
by new integration. They are never automatically converted into executable hooks.

Automatic implementation defaults **off**, separately from idea processing.
When enabled, it selects open, unclaimed todos entered on/after automatic_since
(or the current startup when omitted), after 60 seconds without a todo edit and
after this board's planner finishes. Every linked local or foreign original
must have this system's capture provenance. Legacy, standalone, foreign-system
and existing backlog todos remain manual. Aggregators never implement; open the
source's owner link. Claims prevent automatic retries across restarts or sync.
Explicit Retry implementation uses the retained branch. Task-scope changes
require reviewing and reconciling that branch. Preview remains manual. Unattended
Merge & push requires both explicit publication grants described below.

These are independent CLI runs with output in Unfertig. See
[agent execution](CODEX_SESSIONS.md) for the supported launch contract.

Parallel execution defaults to four workers (`max_workers`, range 1–8), with a
durable queue for additional tickets. Dependencies can be entered as ticket IDs
in the expanded editor; dependent work waits until prerequisites are published
(or closed unmanaged work whose commit is on origin/main). Cycles and unknown dependencies are rejected.

GitHub CLI `gh` must be installed and authenticated for the code repository.
Before an agent starts, Unfertig pushes its branch and creates a draft
`[WIP] [unfertig]` PR. An empty kickoff commit establishes the PR but cannot count
as implementation. The PR appears in the ticket and the integration pipeline
below the todo list. The coordinator publishes coherent worker commits early; Unfertig pushes
observed HEAD changes every two seconds and at worker exit. A publication/PR
failure blocks launch; retries recover the existing PR rather than creating two.

Integration always checks GitHub first and again before publication. If the PR
was already merged, including squash or rebase merging, it verifies the GitHub
merge commit belongs to origin/main and reconciles that result without reapplying
the original ticket branch. Closed unmerged PRs and changed heads need attention.
The original branch is never rebased or force-pushed. Failed candidates remain
available for explicit repair and retry; branch protection is never bypassed.

The pipeline shows Working → Ready → Integration queue → Integrating → Done,
plus Needs attention. Integration is serialized by repository. Published tasks
leave the integration queue even when a hook fails or an instance update is waiting.
Instance maintenance shows hook outcomes, restart blockers and the running revision.
Queued work survives a cooperative restart; active work finishes before exit.

Automatic kickoff does not authorize main publication. `automatic_merge` and
`automatic_publish` default false and must both be true for unattended Merge & push.
`automatic_deploy` is a retained legacy setting with no effect on this boundary.
A configured hook has its own execution authorization; a task cannot supply commands.

The canonical implementation advice is [agent_advice.json](agent_advice.json),
loaded by both the browser briefings and managed worker prompts. See
[task execution](PROCESS_REFERENCE.md#implementing-a-todo) for authorization, closure and recovery.

Work categories describe the kind of deliverable independently of Group and
Status. Choose one in the new-todo or expanded editor; Unclassified is the safe
default. Guidance explains the expected result and completion criteria, and is
included in human and AI handoffs. Search includes the selected category. See
[work categories](PROCESS_REFERENCE.md#work-categories-format-17) and the canonical
[categories.json](categories.json). Format 1.7 requires an explicit supported
migration before rollout; see [VERSIONING.md](VERSIONING.md).

Bugfix corrects a known defect and demonstrates the correction with proportional
regression verification within the task's authorization. Debugging retains its
focus on investigation and causal explanation. Both use the shipped definitions
in `categories.json` for selectors, validation, human/AI briefings and worker
guidance. Storage 1.19 protects the expanded vocabulary from older writers and
never automatically recategorizes existing todos.

After implementation checks pass, Unfertig updates the PR description with the
result and verification, removes [WIP] from its title, and marks the draft ready
for review. It remains unmerged until the integration action is authorized.

Completion summaries are edited separately from task requirements in the expanded todo and included in AI/human briefings and aggregate details. Closing requires an outcome, verification and limitations/follow-up; reopening clears the summary, with its previous text retained in Git history. Legacy closed tickets stay editable without fabricated summaries. Format 1.10 migrates automatically on the owning instance’s next restart (VERSIONING.md). Before work, locate/read the actual process, task and originals and verify the repository. Commit verified implementation changes locally; never push without explicit authorization. No-change findings require neither an empty implementation commit nor a new branch. Existing managed branches/PRs stay with the coordinator for review.

## Agent effort (format 1.21)

Expand a todo and use **Agent effort**, then **Save**. Automatic is the default;
it displays the profile selected for the saved task. Manual choices are Terra
medium, Sol medium, Astra medium and Astra high. Returning to Automatic restores
scope-based selection. The owner board edits this field; aggregate views and
fresh copied briefings display the same selection.

| Automatic task scope, in precedence order | Profile |
| --- | --- |
| Exceptional complexity (`xhigh`); recovery, authorization, security or concurrency hazards | Astra high |
| Architecture, persistence, migration, storage, protocol or compatibility; integration repair | Astra medium |
| High complexity; research, concept, design or exploratory debugging | Astra medium |
| Low complexity or an explicitly bounded local/UI/copy fix | Terra medium |
| Ordinary multi-file work or unspecified scope | Sol medium |

These deterministic rules are in `efforts.json`, shared by Python and the browser.
Scope matching uses the saved heading and description; the old `effort` field
remains a complexity hint (`low`, `medium`, `high`, `xhigh`), independent of priority.
It never directly requests low or xhigh model reasoning. Text matching is a
conservative heuristic, not a correctness guarantee or authorization check. A
manual selection overrides automatic routing, including for integration repair.

`execution_profile` accepts `auto`, `terra-medium`, `sol-medium`, `astra-medium`
or `astra-high`. Missing means `auto`; explicit null/empty/unknown values fail
validation. Existing explicit hints and extensions survive migration. Edits
omitting the new field retain its saved value. Both HTTP and filesystem writers
use the same validation and recovery contract.

Every implementation/retry reads the latest saved task after worktree preparation
and passes both `-m <model>` and `-c 'model_reasoning_effort="<effort>"'` to Codex.
A queued job uses the latest saved selection; a running job keeps its launch
profile. Integration repair follows the same contract. An unavailable model fails
visibly; there is no silent substitution. Ordinary idea processing launches Terra
medium; aggregator routing launches Astra medium. Processing suggests complexity
hints and leaves profile selection Automatic unless the user requested an override.

Protected `workflow.agent_runs` records each implementation/repair launch's model,
reasoning effort, requested/effective profile, selection reason, start, duration,
exit status and prompt byte count/hash. These are launch observations, not proof
of task acceptance or provider-reported token usage. Verification and publication
retain their separate evidence. Processing exposes its profile in runtime status.
No persistent session, authority cache or cross-task transcript is introduced.

### Completed externally

Use **Completed externally** in owner ticket details when a failed/interrupted
attempt was completed through another branch or PR. Supply a reason, your identity,
and any actual replacement PR, implementation, integration and deployment commits.
Unknown references may be left empty. Original failure, scope, branch and attribution
remain inspectable; the action appends reconciliation history and marks the attempt
superseded, without closing/reopening the ticket or running implementation/deployment.

Token-protected `PUT /api/workflow/action` accepts:

```json
{
  "id": "T0040",
  "action": "complete_external",
  "revision": "current saved todo revision",
  "request_id": "stable-unique-request-id",
  "actor": "SL",
  "reason": "Completed through a replacement PR",
  "pr_url": "https://github.com/owner/repository/pull/123",
  "implementation_commit": "",
  "integration_commit": "",
  "deployment_commit": ""
}
```

Use full actual commit hashes or empty strings. Retry uncertain delivery with the
identical body; revision conflicts require reloading and reviewing. Live/uncertain
workers and queued/deploying stages must be resolved first. Reconciliation needs
the enabled owning service, writable records and completed history, but does not
require configured launch recipes. Aggregators can inspect the protected evidence;
perform the action on the owner. Closed historical failures have no retry button.
Reopening an unreconciled failure restores recovery; superseded attempts remain
history even when reopened, with new work assigned a follow-up todo.

GitHub's current PR identity, target, state and merge revision are checked against
the configured repository and fetched origin branch. Squash/rebase merges use that
merge revision; the old failed branch need not contain it or even have a completed
commit. Integration and deployment are displayed separately as verified/unverified.
For managed Unfertig, deployment verification checks the committed runtime pin,
installed runtime, startup-captured running revision, owner and writable history.
Other artifact recipes retain supplied deployment references explicitly unverified.
No code merge, restart, deployment recovery or storage migration is invoked.


### Recovering a committed implementation

The coordinator publishes managed checkpoints; workers commit locally and read
the confirmed publication receipt. Implementation, publication, approval and
verification outcomes appear separately in the protected run details. Publication
authorization does not authorize merging or deployment.

Use **Verify existing result and resume** to review the retained report and exact
commit, authorize coordinator publication to the assigned PR and run missing
checks without a new implementation agent, branch or PR. Legacy needs-attention
reports require explicit review that publication was the sole blocker. Changed
scope, dirty work, contradictory evidence and active/uncertain workers block
recovery. Worker tool approvals remain with the trusted execution approval service;
a board edit cannot grant permission. See PROCESS.md and VERSIONING.md.

### UM task context and multiple repositories

New runs on a UM board always receive an isolated worktree for the board-owning
UM repository. Its declared `project` or schema-2 `projects` supply code targets;
company/client directory containers do not implicitly grant access to descendants.
The worker is given explicit worktree mappings, including reverse metadata
contexts. Unavailable checkouts stay unavailable; they are never cloned implicitly.

Task-related design, concepts, decisions, sources and notes can be committed to
the UM worktree. Live `state/`, `tools/` and child pins remain coordinator-owned.
Each repository uses its own Git identity. Only repositories with meaningful
changes get a PR; a concept-only task does not need a code commit or code PR.
The worker reports every available repository's ID and actual commit. The
coordinator verifies clean worktrees, publishes checkpoints and tests the result.

`workflow.repository` remains the primary artifact for existing test/preview/
test and preview recipes. Additional children use `workflow.repositories` keyed by their
stable artifact IDs (`project` for a legacy singular child, `context` for the UM
repository), with argv-array `test`, optional `preview` (legacy `restart` is retained but ignored) and `base_branch`.
Changed code without a test recipe is blocked. Context validation defaults to
whitespace and changed-JSON checks; configure a context test for stronger checks.
Only the configured primary preview is launched by Test branch.

The UI lists each repository's PR and progress. Integration acquires ordered Git
locks, tests exact candidates, reconciles already-merged PRs and publishes children
before updating wrapper pins. Partial publication is retained and recoverable;
GitHub does not offer an atomic transaction across independent repositories.
All work closes after publication; optional hooks and runtime maintenance retain
separate evidence. Existing single-repository runs are not silently expanded.

### Header worker capacity

After Local & yours and before Initials, Workers shows owner-local occupied slots
and the editable `workflow.max_workers` limit (integer 1–8, default four). Green
means configured and ready with no slots occupied; orange means occupied with
capacity remaining; red means at or above the limit; solid grey means disabled
or stopping. A hollow dot and Unknown/Unavailable label covers stale requests,
uncertain retained processes, missing launcher/recipes, history blocks and draining.
Queued tickets are never counted as occupied slots. Status expires after six seconds.

Edit the number and press Enter or Save. Escape discards the draft and adopts the
latest saved revision. Errors retain the draft; after a conflict, review the saved
value with Escape before applying another edit. The existing scheduler uses saved
limits on its next dispatch (normally within its next tick); increasing allows
queued work to start, while lowering never cancels running workers or drops queues.
No restart or new implementation/publication permission is granted by this edit.

The token-protected owner-only `PUT /api/workflow/settings` accepts exactly
`{max_workers: integer, revision: string}`. `GET /api/workflow` exposes only the
capacity, configuration revision/editability and local/instance persistence layer.
For UM's documented `state/unfertig/config/{config,machine.local}.json` layout,
edits atomically update the ignored, untracked durable
`state/local/unfertig/config/config.json` override, preserving other fields. The
generated effective config and baseline remain untouched; normal supervisor
restart merges the durable override. A crash after saving but before live apply
is recovered on restart. Unsupported generated layouts or divergent baselines
block editing. Other explicit instance configs use the BoardStore transaction
and local history recovery. No-config launches display capacity but cannot save.
This narrow allowlist supplies the capacity setting; there is no separate
concurrency preference or generic config editor.

## Application build in the header

The header's `build <12 hex digits>` identifies the running application's source
build, not a semantic release number. `application_version.application_build` is
the authoritative source: SHA-256 over sorted top-level runtime `.py`, `.js`,
`.css`, `.html` and `.svg` files plus `agent_advice.json`, `categories.json`
and `efforts.json`, excluding `test_*` files.
Names and lengths delimit each input. Git metadata, local board subdirectories,
configuration, documentation and tests do not affect it; source archives work without Git.

The server calculates this value once at startup and renders it into the header
for both normal and aggregation boards. Update the application files together
and restart the server, then reload the page; the new source build automatically
gets a new identifier without a manual version bump. Do not edit a running
installation in place: its imported Python code remains the startup build.
Keep the input suffix list current if runtime assets in new languages or
subdirectories are introduced. This identifier is independent of persisted
`format_version`, API `protocol_version`, and board-content revisions. No board
records or configuration are changed to display it.

## Instance settings

The gear beside Initials opens the keyboard-accessible settings dialog. Escape
or Close retains unsaved edits in this tab; tab reload restores them for review.
**Compare latest** shows saved values alongside the retained draft. **Keep draft
against this version** rebases only your edited fields; **Use saved values**
explicitly discards the draft. Drafts use session storage scoped to the board
identity; they are not retained after closing the tab. Storage failures leave an
in-memory draft and a message. Another tab or the compact worker control can
change configuration; a stale save returns a conflict without overwriting it.

| API field | Purpose and validation | Applies |
| --- | --- | --- |
| `project_name` | Board title, 0–120 characters, no control characters; empty hides it | Immediately in service context, next refresh in other open pages |
| `max_workers` | Existing `workflow.max_workers`, integer 1–8 | Next dispatch; active work finishes and queued jobs remain |
| `idle_seconds` | Existing `processing.idle_seconds`, integer 60–86400 | Next automatic-processing check |
| `closed_seconds` | Existing `processing.closed_seconds`, integer 30–86400 | Next automatic-processing check after browsers close |

Successful saves need no restart. The timing fields only affect automation that
the operator has already enabled. They do not enable processing or workflow.
Workflow permission switches (including automatic publication/merge/deployment),
commands, executable paths, credentials, connections, ports, repository and data
locations are excluded. Settings saves neither create workflow grants nor alter
existing ones. Category definitions remain in the existing category system;
category customization can extend this configuration boundary separately without
duplicating the generic editor. No category fields are accepted by this release.

On supported UM hosts, **all four fields are machine-local preferences** in
ignored, untracked `state/local/unfertig/config/config.json`. They override shared
defaults. The panel does not edit shared `state/unfertig/config/config.json`, the
generated `machine.local.json`, or `machine-baseline.local.json`. Normal supervisor
startup merges the durable override again; it cannot silently overwrite these
preferences. Shared defaults remain operator-maintained. This uses the existing
worker-capacity host contract and needs no supervisor change. Unsupported generated
locations, symlinks, tracked local overrides, modified effective files and incompatible
formats are read-only. Run normal host startup through its supervisor.

For standalone/other explicit instance configs, edits use the existing BoardStore
configuration transaction and local Git history in the board owner. Such settings
are shared if the configuration is shared. No-config and no-Git instances remain
readable but are not editable. Configure an explicit supported owner before editing.
Only changed allowlisted keys are replaced; other fields and extensions survive.

`GET /api/settings` returns `fields` (labels, types, bounds and help), `values`
(currently effective), `saved_values` (durable configuration), `editable`, `layer`
(`local` or `instance`), and an opaque `revision`. Read-only responses contain a
bounded explanatory `error` and effective values, without source documents or paths.
The owner-only endpoint is additive to protocol 2. Save with the current board
session `X-Board-Token`, same-origin checks and exactly this body:

```json
{"revision":"revision-from-GET","changes":{"project_name":"Our ideas","max_workers":3}}
```

`PUT /api/settings` returns the updated view. Unknown fields and invalid values
return 400, an invalid session or origin returns 403, and concurrent configuration
or pending-history conflicts return 409. Storage errors return 500. The revision
covers the destination and active/upstream config, and is shared with the compact
`/api/workflow/settings` control. Requests contain at least one changed field;
partial edits preserve omitted fields. A save serializes with dispatch, processing
and board writes. Local writes replace one file atomically; standalone writes use
the existing journal/history recovery. Neither mechanism pushes configuration.

After a timeout or lost response, keep the draft and compare current saved values
before retrying; a stale repeat conflicts harmlessly rather than overwriting a
subsequent edit. Reopen the dialog after service restart to renew the session token.
If local history is pending, retry history through the board before another edit.
An interruption after persistence but before live application can leave effective
and saved values different; normal restart applies the durable values. Recovery
does not require an LLM and never restores a stale whole configuration.

## Merge all PRs & push

The owner-local control beside **Push all commits** reviews every saved, finished
implementation on this board, independent of list filters. Review shows each
todo, repository, branch, PR and exact commit, including unchanged repositories
in a UM run. Only ready/tested/test-failed implementations with unchanged scope,
clean worktrees and open, ready PRs at their verified implementation heads qualify.
Foreign, closed, superseded, queued, active, stale and incomplete attempts are
excluded with reasons. Already merged PRs and delivery failures require the
individual reconciliation/recovery controls. Unsaved todo/priority drafts are
excluded in the browser. An empty review queues nothing.

Confirming the concrete batch authorizes the same integration actions as each
individual **Merge & push**: changed children before wrapper pins, exact
combined-candidate tests and publication. Configured instance maintenance follows
publication independently. Each queue entry has its own integration outcome.
Existing migration preflight, conflict resolution, queue pause, skip and recovery
contracts remain in force. A blocked queue can accept later entries, which remain
visibly waiting for its blocker.

GET `/api/workflow/merge-review` returns `repository`, `board`, `entries` (each
with `id`, `name`, reviewed `repositories` and an ordinary merge `action`),
`excluded` and `queue_blocked_by`. This read-only owner operation checks current
PRs; it never authorizes integration. Token-protected PUT
`/api/workflow/merge-batch` takes the exact `repository`, `board`, and `entries`
array of those actions. It rechecks each entry, then calls the supported workflow
action implementation. Results contain per-entry `accepted`, `rejected`, or
`unknown` outcomes and current `workflow` status. Acceptance confirms queue
submission only; inspect the pipeline for actual publication evidence and separate instance maintenance outcomes.
No action accepts a `tested_commit` claim as proof of Preview.

Accepted entries and their request fingerprints are saved before dispatch.
Repeated clicks exclude queued entries. After a lost response, the tab retains
the exact batch in sessionStorage across reloads: **Retry reviewed merge batch**
checks the same request IDs and bodies, including already accepted entries.
Never substitute refreshed revisions into that decision. A definitive rejection
requires a new review; partial acceptance remains in the durable queue across
service restarts. If browser session storage is lost, inspect the pipeline before
reviewing again; existing queued/active stages cannot be enqueued a second time.
Aggregators cannot enqueue source work through either transport; use its owning
instance. No board format change is introduced.

### Tab view state

A tab with no saved view for the board starts with **Open + started**, empty
search/group/tag/project filters, priority sorting, and grouping/show-processed
off. These initial defaults were already the board's defaults. Reloading the
same tab restores its search, all filters, sorting, grouping and processed-idea
visibility, including explicit **Closed** or **All statuses** selections and
**Clear filters**. A saved view also takes precedence over a task/idea hash on
reload; a fresh hash link retains its initial reveal behavior.

Views use optional `sessionStorage`, qualified by project identity and canonical
board data location (within the browser origin). Another board gets its own view;
a fresh tab/session without saved state gets the defaults. Browser duplication
or session recovery may copy an existing tab's session and thus continue its view.
Old global localStorage view preferences are ignored because they cannot identify
a board or tab; saved initials remain unchanged. Missing/invalid fields use their
initial defaults; obsolete group/tag/project selections clear after choices load.
Aggregate selections wait for source choices and work with either transport.

This version-1 browser cache is disposable, never a record/configuration format or
an input to board migrations. Invalid or unsupported cache envelopes are reset;
unknown cache fields are ignored. If browser storage is blocked/full, controls
still work, but reload persistence is unavailable. No task status or stored record
changes when selecting or restoring a view.
