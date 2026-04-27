# Signal Routing Spec

Source: implements GitHub issue #883.

This document specifies the actionability-based signal routing
contract every cockpit signal (alert, notification, inbox item,
activity event) must follow. Code home:
`src/pollypm/signal_routing.py`. Test home:
`tests/test_signal_routing.py`.

## Why one policy

The pre-launch audit (`docs/launch-issue-audit-2026-04-27.md` §3)
identifies the recurring shape: events were routed by *caller
preference* — each emitter passed its own opinion of where the
signal should go — instead of by a *shared policy* that knows the
audience, severity, and actionability of each signal. Symptoms:

* `#879` — automatic recovery paused after rapid failures and 98
  `no_session` alerts were visible only on the Activity log;
  Home and Rail did not elevate the degraded state.
* `#820` — Home counted tracked projects, Rail counted registered
  projects, and the two diverged on screen at the same time.
* `#765` — heartbeat classification signals were toasted, training
  the user to dismiss real action-required toasts wholesale.
* synthetic / test event kinds polluted live signal.

The fix is a single envelope every signal carries plus a single
function every router consults. Local exceptions are not
permitted — each surface (Activity, Inbox, Rail, Home, Toast)
gets its delivery list from `route_signal()`, full stop.

## Vocabulary

* **Signal** — any event the cockpit emits or persists for
  delivery: alert, notification, inbox task creation, activity
  feed event, advisor recommendation.
* **Envelope** — `SignalEnvelope`. The structured metadata every
  signal must carry.
* **Surface** — a user-visible delivery channel:
  `ACTIVITY` / `INBOX` / `RAIL` / `HOME` / `TOAST`.
* **Audience** — who the signal is for: `USER` / `OPERATOR` /
  `DEV`.
* **Actionability** — the user's required response:
  `OPERATIONAL` / `INFORMATIONAL` / `ACTION_REQUIRED`.

## Envelope schema

| Field              | Required | Notes                                  |
| ------------------ | -------- | -------------------------------------- |
| `audience`         | yes      | `SignalAudience`                       |
| `severity`         | yes      | `SignalSeverity` — for rendering       |
| `actionability`    | yes      | `SignalActionability` — for routing    |
| `source`           | yes      | Stable subsystem id ("heartbeat", …)   |
| `subject`          | yes      | One-line rendered title                |
| `body`             | yes      | One-paragraph rendered body            |
| `project`          | no       | Project key for scoping                |
| `dedupe_key`       | no       | Stable key for collapsing repeats      |
| `suggested_action` | no       | One-line CLI suggestion                |
| `payload`          | no       | Free-form structured data              |

## Routing policy

`route_signal(envelope)` returns a `RoutingDecision` (a
`frozenset[SignalSurface]` plus a short `reason` string).
Decision tree:

1. `DEV` audience → `frozenset()`. Drop. The dev-channel inbox
   reads the underlying store directly when needed.
2. `OPERATOR` audience or `OPERATIONAL` actionability →
   `{ACTIVITY}`. Forensic visibility, no interrupt.
3. `INFORMATIONAL` actionability → `{ACTIVITY, INBOX}`.
   Discoverable, not interrupting.
4. `ACTION_REQUIRED` + `USER` audience → all surfaces.

Tests in `test_signal_routing.py` lock every decision in.

## Dedupe policy

`compute_dedupe_key(source, kind, target=…, extra=…)` returns a
stable string for the same logical signal. Two emissions with the
same key collapse to a single rail / inbox / toast delivery. The
audit cites `#867` — repeated alerts re-fired every heartbeat
because each emission produced a unique id.

Convention: `<source>:<kind>[:<target>][:<extra>]`. Hashed when
the human-readable form would exceed 120 characters.

## Migration

The migration registry (`ROUTED_EMITTERS`) tracks which emitters
have moved to `SignalEnvelope` + `route_signal`. The launch
hardening release gate (#889) reads
`missing_routed_emitters()` and rejects v1 if any of the
high-traffic required emitters is missing. The current required
set is:

* `work_service`
* `supervisor_alerts`
* `heartbeat`

When an emitter migrates it calls
`register_routed_emitter("...")` at module import time.

## Shared count API

The audit cites `#820` (Home vs. Rail inbox count divergence)
and `#879` (Home / Rail / Activity disagreement on degraded
state). The shared count API is the structural fix: every
counter on every surface calls one of these helpers, never
reaches into `Store.query_messages` directly.

* `shared_inbox_count(config)` — re-exports the consolidated
  inbox counter. Returns 0 on any error so the cockpit never
  fails to render because of a stale store.
* `shared_alert_count(rows, *, include_operational=False)` —
  filters operational alerts by default. Pass
  `include_operational=True` only on debug surfaces.

## What lives elsewhere

* `cockpit_alerts.py` still owns the toast-tier classification
  (`AlertChannel`, `alert_channel`, `alert_should_toast`,
  `is_operational_alert`). `signal_routing` re-exports those so
  callers have one import path. The classification logic stays
  where it is — the pre-#883 module already had the right shape;
  this issue is about the wider envelope around it.
* `cockpit_inbox.py` still owns the inbox database scan. The
  shared count helper delegates to it.

*Last updated: 2026-04-27.*
