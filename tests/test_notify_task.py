"""Unit tests for :mod:`pollypm.notify_task` (#1003)."""

from __future__ import annotations

from datetime import UTC, datetime

from pollypm.notify_task import NOTIFY_LABEL, is_notify_inbox_task
from pollypm.work.models import Priority, Task, TaskType, WorkStatus


def _make_task(*, labels: list[str] | None = None, flow_template_id: str = "chat") -> Task:
    return Task(
        project="demo",
        task_number=7,
        title="Plan ready for review: demo",
        type=TaskType.TASK,
        labels=list(labels or []),
        work_status=WorkStatus.DRAFT,
        flow_template_id=flow_template_id,
        flow_template_version=1,
        current_node_id=None,
        priority=Priority.HIGH,
        roles={"requester": "user", "operator": "architect"},
        created_at=datetime(2026, 4, 30, 10, 0, tzinfo=UTC),
        created_by="architect",
        updated_at=datetime(2026, 4, 30, 10, 0, tzinfo=UTC),
    )


class TestIsNotifyInboxTask:
    def test_pm_notify_immediate_task_is_inbox_only(self) -> None:
        """``pm notify --priority immediate`` always tags the task with
        ``notify`` and a sidecar ``notify_message:<id>`` (see
        ``cli_features/session_runtime.py``). That label set is the
        sole signature for "this is an inbox row, not a work item".
        """
        task = _make_task(
            labels=[NOTIFY_LABEL, "notify_message:42"],
        )
        assert is_notify_inbox_task(task) is True

    def test_plan_review_task_is_inbox_only(self) -> None:
        """The architect's stage-7 plan_review handoff carries a richer
        label set — plan_review, project:<key>, plan_task:<id>,
        explainer:<path> — alongside the bare ``notify`` marker.
        Filtering still keys off the ``notify`` label so we don't have
        to teach the filter every plan_review-shaped variant.
        """
        task = _make_task(
            labels=[
                "plan_review",
                "project:demo",
                "plan_task:demo/1",
                "explainer:/tmp/demo-plan-review.html",
                NOTIFY_LABEL,
                "notify_message:42",
            ],
        )
        assert is_notify_inbox_task(task) is True

    def test_real_work_task_is_not_filtered(self) -> None:
        """A normal plan_project / standard task without the ``notify``
        label MUST stay visible. The filter intentionally over-favours
        keeping work tasks visible — false positives in the Tasks view
        are far worse than false negatives.
        """
        task = _make_task(
            labels=["project:demo"],
            flow_template_id="plan_project",
        )
        assert is_notify_inbox_task(task) is False

    def test_chat_flow_without_notify_label_is_not_filtered(self) -> None:
        """Chat-flow alone is not enough — real PM/user chat threads
        also use the ``chat`` template and need to remain visible.
        """
        task = _make_task(
            labels=["chat:user", "project:demo"],
            flow_template_id="chat",
        )
        assert is_notify_inbox_task(task) is False

    def test_missing_labels_attr_returns_false(self) -> None:
        class _BareTask:
            pass

        assert is_notify_inbox_task(_BareTask()) is False
