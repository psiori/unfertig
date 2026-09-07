# Codex session interoperability

Status: **live desktop proof incomplete**. Checked with Codex CLI 0.153.2
on Linux on 2026-09-08. This note records reusable integration findings for
T0030, requested by Sascha and investigated by Codex. It does not enable a new
launcher or change the Implement → Test preview → Merge/restart workflow.

Unfertig currently launches `codex exec --approve-for-me -C <worktree>` in
`Workflow.run` in `workflow.py`, supplies the host context in its prompt,
and collects process output and a final report. It does not retain an app-server
thread/turn identity or offer desktop navigation. Neither a transcript nor an
Unfertig workflow run ID proves a shared live Codex thread.

## Interfaces checked

The installed CLI help exposes `app-server daemon`, `app-server proxy --sock`,
`agents`, and `--remote unix://PATH`. Proxy connects to a running control socket;
it is a candidate integration transport, not proof that the desktop uses that
socket. `exec --help` has no remote endpoint option. Do not assume that adding
`--json` or changing `--thread-source` attaches an exec worker to the app.

The generated 0.153.2 schema contains `thread/start` with `cwd`,
`developerInstructions`, `approvalPolicy`, `approvalsReviewer`, and `sandbox`.
It also contains `thread/resume`, `turn/start`, `turn/steer` (required
`threadId`, `expectedTurnId`, `input`) and `turn/interrupt` (required
`threadId`, `turnId`). Schema presence establishes available request shapes,
not successful execution or desktop compatibility.

The official [App Server documentation](https://learn.chatgpt.com/docs/app-server)
describes initialization, thread creation, streamed events, steering and
interruption. It documents Unix socket and stdio transports and terminal UI
connections with `--remote`. It labels app-server and WebSocket use experimental
and unsupported for production workloads. Generated schemas are specific to the
CLI version; no independently verified desktop/API compatibility range is known.

The official [Commands reference](https://learn.chatgpt.com/docs/reference/commands)
documents `codex://threads/<thread-id>` for opening a local chat, and
`codex://new?path=<encoded-absolute-directory>&prompt=<encoded-text>` for a new
chat with workspace and composer text. The new-chat link does not submit the
prompt. These are documented navigation mechanisms; this investigation could
not exercise their handlers or establish attachment to an externally created
active thread. Never use the new-chat link as evidence of same-session attachment.

## Reproducible preflight and observed results

Run these inspection commands in the intended desktop user's environment:

```sh
codex --version
codex app-server --help
codex app-server proxy --help
codex app-server daemon version
codex exec --help
codex resume --help
codex app-server generate-json-schema --out /tmp/unfertig-session-schema
sha256sum /tmp/unfertig-session-schema/ClientRequest.json
```

Observed CLI version: `codex-cli 0.153.2`. Schema generation succeeded;
`ClientRequest.json` SHA-256:
`25bc001b5dfe3b35785597b8f9ad9e5aaf7e437331fa9921f041c9e0e03fc9f3`.

The daemon version check initially failed with sandbox `Operation not permitted`.
Repeating that read-only command outside the sandbox failed with `No such file
or directory` for the default `app-server-control/app-server-control.sock`.
That establishes no reachable default daemon here; it does not establish that
no daemon exists at another endpoint. No daemon was started or restarted.
Computer-use discovery returned `apps: []` and `browsers: []`; native app control
was unavailable. The desktop build version therefore remains unverified.
No model worker was launched for this investigation.

## Remaining live acceptance procedure

This is an unexecuted procedure, not a successful demonstration. Use a disposable
board, repository and worktree on a machine exposing the desktop app. Record CLI,
running daemon and desktop build versions before testing.

1. Establish the app's supported daemon endpoint and identity. Connect the
   integration client to that same daemon, initialize it, and retain its replies.
   A separately spawned stdio app-server is insufficient evidence of sharing.
2. From a disposable Unfertig UI implementation launch, create exactly one thread
   and one turn. Preserve the host context and selected developer in the prompt;
   set `cwd` to the exact isolated worktree. Check the returned context and loaded
   instructions. Preserve the existing workspace-write and approval-review
   policy; do not silently substitute different permission defaults.
3. Retain the mapping from workflow run to daemon, thread and active turn. Open
   the documented existing-thread link. Verify the app shows the same technical
   thread ID, worktree and current progress, with timestamped evidence.
4. Steer from the app using a unique harmless marker. Verify acceptance on the
   original active turn and observe the changed behavior in Unfertig's stream.
   Interrupt from the app and verify the original turn finishes interrupted.
   Record IDs and notifications, not just similar text in two windows.
5. Show that navigation and steering created no additional thread, turn or exec
   worker. On disconnect, reconnect to the retained identity; an uncertain
   creation response must not trigger another creation or fallback worker.
   Verify the original checkout and real board remain untouched.

If the app rejects externally created threads, record its exact response and
tested versions. Only then describe that combination as unsupported. Missing
desktop access is an environment blocker, not evidence of product incapability.

## Sequential handoff available today

Wait for the Unfertig worker to exit and for its workflow phase to settle before
continuing elsewhere. Preserve its worktree, branch, commit and final report.
Do not click Retry implementation while a manual continuation owns the work.

For a desktop continuation, open a new local chat using the documented new-chat
link with the absolute retained worktree as `path`. Supply the saved briefing,
host context directory, selected developer, completed work and remaining scope;
inspect the workspace before submitting. Encode each query value separately.
This is a **new session with an explicit handoff**, not live attachment or
automatic reconciliation with Unfertig's workflow claim.

The installed CLI also supports `codex resume <session-id> -C <worktree>` and
`--include-non-interactive` for its picker. Use the actual Codex session ID,
never the workflow run ID, and only after the original worker has stopped.
This is a sequential CLI continuation; desktop steering remains unproven.

No stored formats, transport adapters or workflow claims change in this note.
