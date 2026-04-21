import asyncio

from textual.app import App, ComposeResult
from textual.widgets import ListView

from pollypm.cockpit_rail import CockpitItem
from pollypm.cockpit_ui import RailItem, _rail_alert_subtitle_width, _wrap_alert_reason


def test_wrap_alert_reason_respects_rail_budget() -> None:
    width = _rail_alert_subtitle_width()
    lines = _wrap_alert_reason(
        "Window pm-operator has produced effectively the same snapshot for 3 heartbeats",
        width=width,
        max_lines=4,
    )

    assert width == 26
    assert lines
    assert all(len(line) <= width for line in lines)


def test_rail_item_alert_subtitle_does_not_exceed_default_rail_width() -> None:
    item = CockpitItem(
        key="polly",
        label="Polly",
        state="! Window pm-operator has produced effectively the same snapshot for 3 heartbeats",
    )

    class _RailTestApp(App[None]):
        def compose(self) -> ComposeResult:
            with ListView(id="nav"):
                yield RailItem(item, active_view=False, first_project=False)

    async def body() -> None:
        app = _RailTestApp()
        async with app.run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            row = app.query_one(RailItem)
            rendered = row.body.render()
            plain = getattr(rendered, "plain", str(rendered))
            lines = plain.splitlines()
            assert len(lines) > 1
            assert all(len(line) <= 30 for line in lines)
            assert any(
                "snapshot for 3 heartbeats" in line or "snapshot for 3" in line
                for line in lines
            )

    asyncio.run(body())
