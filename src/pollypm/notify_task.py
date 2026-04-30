"""Identify ``pm notify`` inbox-only tasks (#1003).

Contract:
- Inputs: a work-service task row (or anything exposing ``labels`` /
  ``flow_template_id``).
- Outputs: a boolean classification — does this task represent a pure
  inbox notification (architect/operator notify, plan_review handoff,
  etc.) rather than a real work item that should appear in the cockpit
  Tasks pane and ``pm task list``?
- Side effects: none.
- Invariants:
    - ``pm notify --priority immediate`` materialises a ``chat``-flow
      task carrying the ``notify`` label and a ``notify_message:<id>``
      sidecar so the cockpit inbox can render it as a structured row
      with the architect's user_prompt payload (see
      ``cli_features/session_runtime.py::notify``).
    - Those rows must NOT show up in the Tasks view — they're pure
      announcements with no node-level transition affordance, so the
      user sees a ``draft`` row that ``A``/``X``/``Q`` won't act on.
    - Filtering is keyed on the ``notify`` label, not the ``chat``
      flow template alone — real chat-flow conversations (e.g. PM ↔
      user threads) MUST stay visible in Tasks for projects that
      genuinely use them.
"""

from __future__ import annotations


NOTIFY_LABEL = "notify"


def is_notify_inbox_task(task) -> bool:
    """True when ``task`` is an inbox-only ``pm notify`` row.

    The architect's stage-7 plan-review handoff (and any other
    ``pm notify --priority immediate``) lands as a ``chat``-flow task
    so the cockpit inbox can surface a structured row. Those tasks
    have no transition affordance the way ordinary work tasks do, so
    they should be excluded from the Tasks view and the canonical
    ``pm task list`` output. The inbox UI keeps surfacing them via
    its own pane — that's where ``v open explainer · d discuss · A
    approve`` lives.
    """
    labels = list(getattr(task, "labels", []) or [])
    return NOTIFY_LABEL in labels


__all__ = ["NOTIFY_LABEL", "is_notify_inbox_task"]
