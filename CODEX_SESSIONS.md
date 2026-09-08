# Agent execution

Unfertig launches independent noninteractive `codex exec` jobs. It does not reuse
desktop tasks or share sessions across todos. Configure the executable and sign
in on the host; unavailable executables or models fail with diagnostics.

Each task launch sets its model and reasoning effort explicitly. See
[agent effort](PROCESS_REFERENCE.md#agent-effort-format-121). The coordinator
retains process identity, output reports and launch observations with the run.
Use [PROCESS.md](PROCESS.md) for role instructions and [README.md](README.md) for
configuration. Machine-specific experiments and transcripts belong to their host.
