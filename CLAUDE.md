# unfertig — local idea board

Read [PROCESS.md](PROCESS.md) for the shared agent workflow, schema, and safe update procedure. Read [README.md](README.md) for startup. These instructions also apply when a copied briefing asks you to process ideas or implement a todo. Preserve original ideas and use temporary data for testing.

Locate the active board through the server snapshot context or explicit configuration before reading task records. Data may belong to a parent repository outside this checkout. Never edit the app development board when assigned a host-project task.

For Unfertig development itself, the authoritative backlog is in the
`psiori/um-unfertig` wrapper at `state/unfertig/data/`. Start its managed
`tools/unfertig/` runtime with the wrapper's `start_tools.sh`. Keep feature
previews on disposable boards. The app's former `development/` data is retired;
old branches containing those records do not change the authoritative location.
