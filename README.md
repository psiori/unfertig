# unfertig

A local, repository-backed idea and todo board for people and agents. It runs with Python 3.12 via uv, uses no third-party Python packages, and has no frontend build step.

Renamed from Little by little on 2026-09-06. The `little-board.` browser preference keys are retained so existing identity and view settings continue to work. Original records retain historical wording.

## Start

Install once with `install.command` on macOS, `install.bat` on Windows, or `sh install.sh` on Linux. Then use the corresponding `start` launcher. Keep its terminal open; stop with Ctrl+C. Launchers work from any directory.

In a standalone checkout, the default board is `board/data.json` plus `board/todos/`. A missing default board initializes one onboarding todo, “Add your first idea”. Existing data is never replaced or reseeded. Git must be installed and the checkout must belong to a Git repository with a configured identity. Each meaningful save is committed locally; nothing is pushed.

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
