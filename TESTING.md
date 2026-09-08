# Complete checks

Run `uv run --no-project --python 3.12 --script workflow_support.py unfertig test
--repository "$PWD" --context "$CONTEXT"` using the explicitly selected host
context. This performs whitespace checking, complete Python discovery and every
`test_*.cjs` file. Installed older host helpers continue to run sequential
discovery until the host adopts the updated helper through its normal updater.
Both paths remain supported; neither skips correctness gates.

The updated helper uses `suite_runner.py --javascript`. Four Python processes
receive every discovered test exactly once, balanced using source-controlled
duration hints. New tests are included automatically with a default weight.
Each test owns its writable temporary boards, repositories and ports. Global
monkeypatches and environment changes stay in separate processes. Shared
class/module setup, if introduced, keeps the entire module in one shard.
`suite_runner.py --manifest` lists IDs; `--workers 1` and `--reverse` support
serial and ordering diagnosis. Failures retain their unittest ID and traceback.

OS-released slot locks limit active test workers to four per operating-system
user across concurrent ticket recipes, including the serial JavaScript stage.
This bounds scenario workers, not subprocesses inside real process/locking tests.
It does not reserve physical CPUs or limit older runners that do not participate.
Slot files contain no verification results, board records or durable receipts.
Never delete occupied slot locks to increase concurrency.

Select optional supervisor conformance explicitly with
`UNFERTIG_TEST_HOST_CONTEXT="$CONTEXT"`; it copies reusable host scripts and
starts disposable services. No test may point at a live board. Production
server polling, lock timing and asynchronous correctness deadlines are unchanged.

Multi-repository Preview reuses its primary verification only within the same
action, using a one-use in-memory token. Clean revision/tree, argv, environment,
interpreter/executable and external helper source fingerprints must still match.
Missing or invalidated evidence runs the check again; dirty or failed checks
block Preview. Other changed repositories still run their configured checks.
Actual preview startup/readiness and exact integration/deployment gates remain
mandatory. Tokens are never saved, recovered after restart, or reused between
actions. This is not cross-stage verification caching.

View-state regression: `node --test test_view_state.cjs` covers the tab cache and
recovery defaults. Against a disposable declared preview, run
`node browser_view_state.cjs` with Playwright installed, optionally selecting it
via `PLAYWRIGHT_MODULE`, Chromium via `CHROMIUM`, and the preview URL via
`VIEW_PREVIEW_URL`. The browser check uses mocked disposable board snapshots,
new browser state, desktop/narrow screenshots in `/tmp`, real reloads and both
aggregate transport presentations. Never point it at a live board.
