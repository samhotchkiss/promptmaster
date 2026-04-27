# Launch State Machine

Source: implements GitHub issue #884.

This document specifies the idempotent state machine that models
`pm up` and the cockpit's launch / attach / recover / upgrade-
restart flows. Code home:
`src/pollypm/launch_state.py`. Test home:
`tests/test_launch_state.py`.

## Why a state machine

The pre-launch audit (`docs/launch-issue-audit-2026-04-27.md` §4)
identifies the recurring shape: launch, reattach, upgrade restart,
stale-pane recovery, and storage-closet recovery were handled as
overlapping branches in `cli.py`. Symptoms:

* `#841` — relaunch / respawn hit a tmux segfault and dropped the
  cockpit session into raw Claude Code without a Polly return
  affordance.
* `#871` — the cockpit session inventory reported zero live
  sessions while tmux had five live windows.
* `#817` — `pm up` created an idle shell where it should have
  attached an existing rail.
* `#808` — upgrade restart cycle dropped persistent state.

Every one of those failed because branch ordering in the original
`pm up` happened to put the right test before the wrong action,
or vice versa, and a small change to one branch shifted that
ordering invisibly. The state machine is the structural fix: a
pure function from the read-only tmux probe to a named state and
an ordered action plan. Tests cover every state without forking
tmux.

## Vocabulary

* **Probe** — `LaunchProbe`. Read-only snapshot of tmux + state-
  store + filesystem state.
* **Context** — `LaunchContext`. Where the user is running
  `pm up` from (outside tmux, inside unrelated tmux, inside
  Polly).
* **State** — `LaunchState`. The high-level decision label.
* **Action** — `LaunchAction`. A primitive the runtime executes.
* **Plan** — `LaunchPlan`. State + context + ordered tuple of
  actions + reason string.

## States

| State                    | Trigger                                        |
| ------------------------ | ---------------------------------------------- |
| `FIRST_LAUNCH`           | No main, no closet                             |
| `ATTACH_EXISTING`        | Healthy cockpit                                |
| `RESTORE_FROM_CLOSET`    | Closet alive, main session vanished            |
| `RECOVER_DEAD_SHELL`     | Console pane dead                              |
| `RECOVER_DEAD_RAIL`      | Rail pane dead and not running non-shell       |
| `RECOVER_MISSING_CLOSET` | Main alive, closet vanished                    |
| `UPGRADE_RESTART`        | Upgrade marker present and main alive          |
| `UNSUPPORTED`            | Inside unrelated tmux while main is also alive |

`UNSUPPORTED` is the **fail-closed** state. The plan contains a
single `FAIL_CLOSED` action and a `reason` string with the exact
shell command to recover. Refusing to act is the right answer
when nesting tmux clients would damage user state.

## Hard rules

These are tested explicitly in `test_launch_state.py`:

* **Live non-shell rail is never respawned during attach.**
  `#841` was the segfault path: tmux crashed when respawn-pane
  hit an active rail. The state machine refuses by branching
  into `ATTACH_EXISTING` whenever the rail pane is alive *and*
  running a non-shell.
* **Bootstrap is only valid in `FIRST_LAUNCH` or
  `RECOVER_MISSING_CLOSET`.** No other state runs the
  controller-account selection / per-session launches because
  they would relaunch live workers.
* **`UPGRADE_RESTART` does not bootstrap.** The marker says
  workers were intentionally killed and will be reclaimed by the
  heartbeat sweep.
* **`UNSUPPORTED` is terminal.** A fail-closed plan has exactly
  one action (`FAIL_CLOSED`).

## Inventory reconciliation (#871)

`reconcile_session_inventory(persisted, live)` returns a tuple of
`InventoryDisagreement` rows for every session name that is in
one set and not the other:

* `missing_in_tmux` — persisted as live but no tmux window.
* `missing_in_persisted` — live tmux window but no row.

The cockpit renders the count as a Rail badge / Settings panel
warning so the `#871` "zero sessions while tmux has five" shape
is impossible to hide.

## Migration

The state machine is the policy layer; the runtime in `cli.py`
keeps its concrete tmux / supervisor calls. Migrating `cli.py:up`
to consult `plan_launch()` is the next step. The current
acceptance criteria require:

1. Tests cover every state — done in `test_launch_state.py`.
2. Inventory reconciliation reports disagreement — done.
3. Live rail respawn refusal is testable — done.
4. `cli.py:up` consults `plan_launch()` and executes the action
   tuple in order. Migration step.

The release verification gate (#889) inspects whether `cli.py:up`
delegates to `plan_launch()` before tagging v1.

*Last updated: 2026-04-27.*
