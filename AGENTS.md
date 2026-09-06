# unfertig — local idea board

Read [PROCESS.md](PROCESS.md) before reading/writing board records or processing/implementing ideas. It defines the shared workflow, schema, attribution, and safe update procedure for every agent. See [README.md](README.md) for installation and startup. Keep app code here and state in the configured board directory; use uv and preserve original ideas. Use temporary data for testing.

Locate the active board through the server snapshot context or explicit configuration before reading task records. Data may belong to a parent repository outside this checkout. Never edit the app development board when assigned a host-project task.

For Unfertig development itself, the authoritative backlog is in the
`psiori/um-unfertig` wrapper at `state/unfertig/data/`. Start its managed
`tools/unfertig/` runtime with the wrapper's `start_tools.sh`. Keep feature
previews on disposable boards. The app's former `development/` data is retired;
old branches containing those records do not change the authoritative location.
