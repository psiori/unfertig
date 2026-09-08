# unfertig — local idea board

Read [PROCESS.md](PROCESS.md) before reading/writing board records or processing/implementing ideas. It defines the shared workflow, schema, attribution, and safe update procedure for every agent. See [README.md](README.md) for installation and startup. Keep app code here and state in the configured board directory; use uv and preserve original ideas. Use temporary data for testing.

Locate the active board through the server snapshot context or explicit configuration before reading task records. Data may belong to a parent repository outside this checkout. Never edit the app development board when assigned a host-project task.

For Unfertig development itself, the authoritative backlog is in the
`psiori/um-unfertig` wrapper at `state/unfertig/data/`. Start its managed
`tools/unfertig/` runtime with the wrapper's `start_tools.sh`. Keep feature
previews on disposable boards. The app's former `development/` data is retired;
old branches containing those records do not change the authoritative location.

Read [VERSIONING.md](VERSIONING.md) while processing persistence ideas and before
changing stored JSON or API contracts. Every format change requires its version,
sequential migration, safe defaults, compatibility behavior, and recovery tests.

HTTP and filesystem aggregation are one maintained contract. Every relevant
schema, API, storage, migration, routing, or view change must update both adapters
and run `test_transports` plus the full suite. Put record semantics in BoardStore
and common validation/context helpers; never implement a weaker filesystem writer.
Add recovery and transport-switch scenarios to the shared conformance suite.
See TRANSPORTS.md for configuration, coordination, and compatibility boundaries.

Read the located process and authoritative task/originals and verify the ID and repository before work or status changes; stop dependent work on missing files. Follow agent_advice.json for local commits, explicit push authorization and no-change/no-branch handling. Closure reports belong in completion_summary, separate from requirements; managed workers hand results to the coordinator.

For every persisted-format change, implement deterministic, sequential Python migrations and robust defaults in versions.py; preserve originals, explicit values and extensions, and test interrupted recovery, idempotence and supported upgrade paths. Normal server startup applies supported migrations automatically before serving requests. Merge/update/restart authorization includes these routine migrations; no separate migration approval, LLM, Codex or network service is required. Integration tests the exact combined code and migration behavior before main publication. Updaters trust published main and perform installation guards and actual startup backup, compatibility, migration and recovery checks; they do not rerun code tests or disposable rehearsals. Follow DEPLOYMENT.md for host backups, configuration migration and recovery. Verify the actual runtime, owner and writable history before reporting deployment.
