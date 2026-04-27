# Task Workflow Invariants

Source: implements GitHub issue #886.

This document specifies the canonical task-state transition
table plus the doctor-style invariant checker. Code home:
`src/pollypm/task_invariants.py`. Test home:
`tests/test_task_invariants.py`.

## Why one table, one checker

Before #886, every consumer (assignment, recovery, capacity,
cockpit, inbox, advisor, metrics) maintained its own private
allowlist of which `WorkStatus` values it cared about. The
audit (`docs/launch-issue-audit-2026-04-27.md` §5) cites 30+
regressions that all reduce to: *one subsystem ignored a state
another subsystem cared about*. Examples:

* `#770` / `#771` — recovery missed `IN_PROGRESS` per-project.
* `#816` — capacity manager missed `REWORK`.
* `#807` — recovery matched the wrong live windows.
* `#806` — recovery deleted execution history.
* `#395` — critic synthetic tasks leaked into user task lists.

The structural fix is one transition table that every consumer
reads from, plus one checker that runs the cross-state
invariants. The checker is the `pm doctor --launch-state` the
audit asks for. The release gate (#889) consults the checker.

## Per-state metadata

```python
@dataclass(frozen=True, slots=True)
class StateMetadata:
    status: WorkStatus
    owner: StateOwner
    allowed_next: frozenset[WorkStatus]
    consumes_capacity: bool
    visible_in_cockpit_default: bool
    visible_in_inbox: bool
    is_terminal: bool
    counts_as_done: bool
    requires_active_worker_session: bool
    requires_recovery_lane: bool
    user_can_cancel: bool
```

## State table

| Status        | Owner    | Capacity | Recovery | Inbox |
| ------------- | -------- | -------- | -------- | ----- |
| `DRAFT`       | USER     |          |          |       |
| `QUEUED`      | SYSTEM   |          |          |       |
| `IN_PROGRESS` | WORKER   | ✓        | ✓        |       |
| `REWORK`      | WORKER   | ✓        | ✓        |       |
| `REVIEW`      | USER     |          |          | ✓     |
| `BLOCKED`     | USER     |          |          | ✓     |
| `ON_HOLD`     | USER     |          |          | ✓     |
| `DONE`        | NOBODY   | terminal |          |       |
| `CANCELLED`   | NOBODY   | terminal |          |       |

Tests in `test_task_invariants.py` lock each cell.

## Allowed transitions

The transition graph is encoded as `allowed_next` per state:

* `DRAFT → QUEUED, CANCELLED`
* `QUEUED → IN_PROGRESS, BLOCKED, CANCELLED`
* `IN_PROGRESS → REVIEW, BLOCKED, ON_HOLD, DONE, CANCELLED, QUEUED`
* `REWORK → IN_PROGRESS, QUEUED, CANCELLED`
* `REVIEW → DONE, REWORK, IN_PROGRESS, CANCELLED`
* `BLOCKED → IN_PROGRESS, QUEUED, CANCELLED`
* `ON_HOLD → IN_PROGRESS, QUEUED, CANCELLED`
* `DONE`, `CANCELLED` — terminal

`validate_transition()` raises a `ViolationKind.INVALID_TRANSITION`
for anything outside this graph. The audit cites `#806`: recovery
applied a transition outside the table, the work service did not
refuse, execution history was lost. Running the validator at
write time prevents that class of bug.

## Invariants the checker reports

| Kind                              | Catalog reference |
| --------------------------------- | ------------------- |
| `IN_PROGRESS_NO_OWNER`            | #770 / #771         |
| `QUEUED_NO_ROLE_SESSION`          | (audit §5 generic)  |
| `REWORK_OUTSIDE_RECOVERY_LANE`    | #816                |
| `BLOCKED_NO_UNBLOCK_PATH`         | (audit §5 generic)  |
| `DEAD_CLAIM_CONSUMES_CAPACITY`    | #816 / #770         |
| `INVALID_TRANSITION`              | #806                |
| `PLANNER_CRITIC_LEAKED_INTO_TASKS`| #395                |

The checker takes a `TaskCheckContext` snapshot:

* `tasks` — list of `TaskRow` rows.
* `live_worker_session_task_ids` — task IDs with a live tmux
  worker window.
* `reachable_role_sessions` — role keys with at least one live
  session.
* `recovered_task_ids` — visited by the recovery sweep this cycle.
* `capacity_consumed_task_ids` — what the capacity manager
  thinks is consuming a slot right now.
* `synthetic_task_id_prefixes` — defaults to
  `("critic-", "planner-")`.

The checker is pure — no DB, no tmux. The caller assembles the
snapshot from authoritative subsystems and hands it off. Each
violation carries `kind`, `task_id`, and a one-line `detail`.

## Migration

Adopting the table is additive. Each consumer's migration is
small:

* **Capacity manager** — replace inline allowlist with
  `is_capacity_consuming(status)`.
* **Recovery sweep** — replace inline list with
  `requires_recovery_lane(status)`.
* **Work service** — call `validate_transition(...)` before
  every transition write.
* **Cockpit Tasks view** — read `visible_in_cockpit_default`.
* **Cockpit Inbox** — read `visible_in_inbox`.
* **`pm doctor`** — run `check_task_invariants(...)` and
  render each violation.

Each step is an independent PR. The release gate (#889) reports
`all_statuses_have_metadata()` to refuse a tag when a new
`WorkStatus` is added without a metadata entry.

## What the checker does NOT do

* It does not execute fixes. Some violations (REWORK outside
  recovery) require coordination with the running heartbeat;
  others (synthetic-task leak) require a code change.
* It does not differ for project-local vs. workspace data —
  the caller assembles per-project contexts and calls the
  checker once per project.

*Last updated: 2026-04-27.*
