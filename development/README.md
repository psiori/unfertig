# Development board moved

The complete development backlog moved on 2026-09-07 to the private
`psiori/um-unfertig` wrapper, under `state/unfertig/data/`. All eight ideas and
eight todos retain their IDs, source links, attribution, statuses, and contents.

Use that wrapper's `start_tools.sh` and its
`state/unfertig/config/config.json`. The wrapper retains the migration manifest
and a local exact recovery copy; Git retains the source's earlier history.

`data.json` here is a retired marker, retained to make old explicit data paths
fail without initializing another board. Historical branches may contain the
old records; they are not the authoritative backlog. Keep feature-branch
previews on disposable boards with a separate port.
