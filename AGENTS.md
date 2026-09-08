# Developing Unfertig

These instructions apply to this application's code and documentation. For work
on another project using the board, follow that project's instructions and the
short [PROCESS.md](PROCESS.md) operating entry point instead.

Use uv for Python. Preserve unrelated work and original ideas; test with disposable
data and a separate port. Locate the active board through snapshot context or
explicit configuration before record work. Use supported API/CLI writers.
Keep reusable code here and instance configuration, backlog and evidence with
their host. Write well-structured, concise documentation, reports and other text.

Read [VERSIONING.md](VERSIONING.md) when designing or changing Unfertig's stored
formats or API contracts. Every stored-format change needs a version and
deterministic sequential migration in versions.py, useful defaults, preservation
of originals/explicit values/extensions, compatibility guards and interrupted
recovery, idempotence and supported-upgrade tests. Normal startup applies supported
migrations automatically; no LLM, network service or separate migration approval.

Read [TRANSPORTS.md](TRANSPORTS.md) for changes to Unfertig's schema, API, storage,
migration, aggregation routing or transport behavior, including relevant shared
views. Keep HTTP/filesystem semantics in BoardStore/common validation, update both
adapters and run `test_transports` plus the full suite for such changes. Add
recovery and transport-switch cases when behavior changes. Unrelated CSS, copy,
documentation or ordinary worker-launch changes do not require this document.

Read [DEPLOYMENT.md](DEPLOYMENT.md) when changing installation, update or recovery
behavior. Integration validates the combined candidate before publication.
Updaters trust published main and run installation/startup compatibility, backup,
migration and recovery guards. Deployment is separate from merge completion.
Verify the actual runtime and writable owner history before claiming deployment.

Follow the assigned managed/manual role briefing for commits and completion.
Maintain `agent_advice.json` as the shared source for concise role instructions;
do not introduce duplicate blanket document reads in individual launch paths.
