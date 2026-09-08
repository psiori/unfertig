# unfertig

A local, repository-backed idea and todo board for people and agents. It runs with Python 3.12 via uv, uses no third-party Python packages, and has no frontend build step.

Renamed from Little by little on 2026-09-06. The `little-board.` browser preference keys are retained so existing identity and view settings continue to work. Original records retain historical wording.

## Start

Install once with `install.command` on macOS, `install.bat` on Windows, or `sh install.sh` on Linux. Then use the corresponding `start` launcher. Keep its terminal open; stop with Ctrl+C. Launchers work from any directory.

In a standalone checkout, the default board is `board/data.json` plus `board/todos/`. A missing default board initializes one onboarding todo, “Add your first idea”. Existing data is never replaced or reseeded. Git must be installed and the checkout must belong to a Git repository with a configured identity. Each meaningful save is committed locally; remote pushes require the explicit Push action.

Unfertig's own development backlog belongs to **psiori/um-unfertig**, in
`state/unfertig/data/`. In that wrapper, start the independently pinned tool:

```sh
cd /absolute/path/to/um-unfertig
sh start_tools.sh
```

The wrapper runs `tools/unfertig/`; its separate `unfertig/` development checkout
can switch branches without changing the board server. Configuration lives in
`state/unfertig/config/config.json`. The former `development/` board was relocated
with its IDs, attribution, and records preserved. Its remaining file is a retired
marker, and historical branches are not the authoritative development backlog.
The obsolete `unfertig-development.json` selector has been removed.

Test feature branches against a separate temporary board and port using `--data`
and `--no-git`. Keep the real development board on the wrapper's managed runtime.
The default standalone starter board remains available for ordinary app users.

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

Enter your identity in **Working as**. Capture original thoughts with **Add idea** (or Cmd/Ctrl+Enter). **Make todo** refines an idea; **New todo** creates a standalone task. IDs are allocated by the server. Original wording and attribution are immutable. An idea is processed when a todo links to it.

Expand a todo to edit its name, description, priority, group, tags, status, or implementation references. Save explicitly. Grouping, filters, sorting and folding organize the board. AI and human briefing buttons copy the saved task and workflow, and do not start work. Copy processing briefing delegates planning only.

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
idea**, or later on the idea. Copy its processing briefing for explicit/inferred
routing; unclear ideas remain red and pending. See PROCESS.md for exact routing,
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
VERSIONING.md's stopped-service backup, disposable rehearsal, explicit migration
and idempotence checks. The ordinary unchanged-storage updater remains unchanged.


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
working directory and the latest result. Individual idea rows offer a briefing
or manual todo creation. Unsaved drafts and an empty queue disable the Codex
action immediately; activity remains batched into the existing heartbeats.

## Implementation workflow

Use **Implement with Codex → optional Test branch → Merge & restart**.
After implementation, collapsed rows show only **Preview** as the next workflow
action. Successful Preview advances that action to **Merge & restart**. Unfold
the row to choose direct merge without Preview; expanded controls retain both choices.
Implementation runs in a `codex/…` branch in a locally excluded
`.worktrees/unfertig/` checkout. It receives the host's instructions, design,
developer rules, saved context and original ideas. It uses the configured Codex
executable with `--approve-for-me`. Progress & branch details shows live output.
A finished agent report, new commit, clean worktree and configured checks are
required before preview or merge. The task stays started until deployment succeeds.

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
then runs the configured artifact restart. A serialized integration candidate
combines current main and the ticket, with mandatory checks of the combined commit.
Main or PR changes during validation invalidate that attempt and require retry. A detached supervisor retains
restart results even when Unfertig itself restarts. Failed pushes/restarts and
interrupted stages remain visible for explicit retries. Worktrees are retained.

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
  "max_workers": 2,
  "automatic_merge": false,
  "automatic_publish": false,
  "automatic_deploy": false,
  "automatic_since": "2026-09-07T00:00:00+00:00",
  "repository": "../../../unfertig",
  "base_branch": "main",
  "test": ["uv", "run", "--no-project", "--python", "3.12", "--script", "{worktree}/workflow_support.py", "unfertig", "test", "--repository", "{worktree}", "--context", "{context}"],
  "preview": ["uv", "run", "--no-project", "--python", "3.12", "--script", "{worktree}/workflow_support.py", "unfertig", "preview", "--repository", "{worktree}", "--context", "{context}", "--data", "{data}", "--port", "{port}"],
  "preview_url": "http://127.0.0.1:{port}",
  "restart": ["uv", "run", "--no-project", "--python", "3.12", "--script", "{repository}/workflow_support.py", "unfertig", "restart", "--repository", "{repository}", "--context", "{context}"],
  "timeout_seconds": 3600
}
```

Paths resolve from the config file. Omit repository to use the processing
context's node.json project.path, otherwise that context itself. Missing declared
paths block; no recursive project discovery occurs. All three commands are
required. The optional workflow_support.py recipes cover Unfertig, Kermit,
Unendlich's native app and document-only contexts. Other projects supply their
own commands. Managed Unfertig retains its normal update gate: a future storage
migration requires an explicit stopped migration. Unmanaged artifact processes
are not killed; configure their owning supervisor when necessary.

The Unfertig restart recipe polls supervisor status for up to 900 seconds, including
candidate tests and storage validation. Set `--startup-timeout SECONDS` (1–3600)
on the recipe for a different budget, keeping the outer workflow timeout larger
than the startup budget plus shutdown time. A short supervisor client timeout does not end the deployment: the helper keeps
polling until that same session reports the Unfertig service running, then
verifies the installed commit. A real failure or changed session is rejected.
If the overall budget expires, the message explicitly reports pending startup;
inspect supervisor status before retrying.

Automatic implementation defaults **off**, separately from idea processing.
When enabled, it selects open, unclaimed todos entered on/after automatic_since
(or the current startup when omitted), after 60 seconds without a todo edit and
after this board's planner finishes. Every linked local or foreign original
must have this system's capture provenance. Legacy, standalone, foreign-system
and existing backlog todos remain manual. Aggregators never implement; open the
source's owner link. Claims prevent automatic retries across restarts or sync.
Explicit Retry implementation uses the retained branch. Task-scope changes
require reviewing and reconciling that branch. Preview remains manual. Unattended
Merge & restart requires all three explicit grants described below.

These are CLI runs with output in Unfertig. A shared live Codex desktop session
has not been demonstrated. See [Codex session interoperability](CODEX_SESSIONS.md)
for the checked interfaces, reproduction procedure, and sequential handoff.

Parallel execution defaults to two workers (`max_workers`, range 1–8), with a
durable queue for additional tickets. Dependencies can be entered as ticket IDs
in the expanded editor; dependent work waits until prerequisites are published
(or closed unmanaged work whose commit is on origin/main). Cycles and unknown dependencies are rejected.

GitHub CLI `gh` must be installed and authenticated for the code repository.
Before an agent starts, Unfertig pushes its branch and creates a draft
`[WIP] [unfertig]` PR. An empty kickoff commit establishes the PR but cannot count
as implementation. The PR appears in the ticket and the integration pipeline
below the todo list. Workers push coherent commits early; Unfertig also pushes
observed HEAD changes every two seconds and at worker exit. A publication/PR
failure blocks launch; retries recover the existing PR rather than creating two.

Integration always checks GitHub first and again before publication. If the PR
was already merged, including squash or rebase merging, it verifies the GitHub
merge commit belongs to origin/main and reconciles that result without reapplying
the original ticket branch. Closed unmerged PRs and changed heads need attention.
The original branch is never rebased or force-pushed. Failed candidates remain
available for explicit repair and retry; branch protection is never bypassed.

The pipeline row shows Working → Ready → Integration queue → Integrating →
Deploying → Done, plus Needs attention, with individual PR links. Integration
serializes by repository and drains running workers before deployment can restart
the service. Queued work survives restarts; running interrupted jobs require retry.
When code and board share a repository, candidate checks include board history
present at candidate creation; further main changes invalidate the attempt.

Automatic kickoff does not authorize main publication. `automatic_merge`,
`automatic_publish` and `automatic_deploy` each default false; all three must
be explicitly true to enable unattended Merge & restart. Partial grants leave
that combined action manual. Migration never turns any of these permissions on.

The canonical implementation advice is [agent_advice.json](agent_advice.json),
loaded by both the browser briefings and managed worker prompts. See
[PROCESS.md](PROCESS.md#implementing-a-todo) for authorization, closure and recovery.

Work categories describe the kind of deliverable independently of Group and
Status. Choose one in the new-todo or expanded editor; Unclassified is the safe
default. Guidance explains the expected result and completion criteria, and is
included in human and AI handoffs. Search includes the selected category. See
[PROCESS.md](PROCESS.md#work-categories-format-17) and the canonical
[categories.json](categories.json). Format 1.7 requires an explicit supported
migration before rollout; see [VERSIONING.md](VERSIONING.md).

After implementation checks pass, Unfertig updates the PR description with the
result and verification, removes [WIP] from its title, and marks the draft ready
for review. It remains unmerged until the integration action is authorized.
