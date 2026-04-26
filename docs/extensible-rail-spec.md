# Extensible Cockpit Rail Specification

**Status:** partially shipped. Depends on: plugin-api v1, cockpit.

## 0. Surface contract

PollyPM has **two live rail surfaces** and rail-facing issues must say
which one they affect:

- `src/pollypm/cockpit_ui.py` — the Textual cockpit launched by `pm`
  and used for the normal interactive UI.
- `src/pollypm/cockpit_rail.py` plus `src/pollypm/rail_daemon.py` —
  the text/daemon rail path that keeps heartbeat and recovery work
  alive when the cockpit is closed.

If a rail feature changes row rendering, indicators, keyboard
bindings, footer/ticker content, or section collapse behavior, the
default expectation is that **both surfaces** are updated. An issue may
scope itself to only one surface, but it must say so explicitly.

## 1. Purpose

The cockpit's left rail used to be a hardcoded list (Projects, Tasks, Settings, etc.). The current build registers the core rail items through the plugin API, and plugins can add visible surfaces such as the Live Activity Feed, a Magic Skills explorer, or a Downtime Backlog viewer without editing the main cockpit rail list.

Make the rail pluggable: plugins register rail items against named sections with explicit indexes.

## 2. Rail anatomy

The rail is organized into **sections**. Each section is a named, ordered region. Plugins insert **items** into sections at explicit indexes.

Built-in sections (fixed, in this order):
- `top` (home, dashboard-at-a-glance)
- `projects` (per-project rail items)
- `workflows` (work-service surfaces — tasks, inbox, activity)
- `tools` (plugin-contributed tools)
- `system` (settings, plugins, version)

Plugins typically insert into `workflows` or `tools`. Inserting into `top`, `projects`, or `system` requires a manifest flag (`contributes_to_reserved_section=true`) that the plugin-host warns on at load.

## 3. Registration API

During `initialize(api)`:

```python
def initialize(api: PluginAPI) -> None:
    api.rail.register_item(
        section="workflows",
        index=30,
        label="Activity",
        icon="activity",            # optional; falls back to plugin's default icon
        badge_provider=live_count,  # optional; returns count for small badge on rail
        handler=open_activity_panel, # called when user selects the item
        visibility=always,          # "always" | Callable[[Context], bool] for conditional
    )
```

- **index** — integer ordering within section. Lower = higher in the rail. Convention:
  - Built-in core items own indexes 0–99 per section.
  - Plugin items start at 100.
  - Ties resolve by plugin name alphabetically.
- **badge_provider** — optional callable that returns a number or string for a small badge (e.g., unread count).
- **handler** — invoked when the user selects the item. Receives a `RailContext`. Returns a panel-spec (Textual widget + focus hint).
- **visibility** — controls whether the item is shown. Can be `"always"`, `"has_feature"` (only if a capability is registered), or a predicate.

### Current `RailContext`

The shipped `RailContext` exposes:

- `selected_project`: current project key, if any
- `user`: opaque user identity token
- `cockpit_state`: live cockpit state dict; handlers should treat it as read-only
- `config`: loaded PollyPM config
- `router`: cockpit router object
- `supervisor`: loaded supervisor object
- `launches`, `windows`, `alerts`: current cockpit runtime snapshots
- `spinner_index`: render tick index for lightweight animations
- `extras`: untyped compatibility bag for values that have not been promoted into the public contract

Third-party plugins should prefer the typed fields above. `extras` is retained
for cockpit-private compatibility during incremental migrations; new plugin
contracts should not require callers to know magic string keys.

## 4. Rendering contract

Cockpit's rail builder walks sections in order, within each section walks items by index, and renders:
- Icon (16px or ASCII char).
- Label.
- Badge (if badge_provider returns non-null).
- Keyboard shortcut (auto-assigned from section index + position, override-able).

Selection dispatches to the item's handler; the panel pane updates to show the returned widget.

## 5. Back-compat with current rail

The old hardcoded rail is re-expressed as a set of built-in items registered by `core_rail_items`. This means the existing rail works identically on first load; external plugins just slot in alongside.

## 6. Settings

`pollypm.toml`:

```toml
[rail]
hidden_items = ["tools.activity"]      # list of "section.label" to hide
collapsed_sections = ["system"]        # sections that start collapsed
```

CLI:
- `pm rail list` — list registered items with section + index.
- `pm rail hide <section.label>` — append to hidden_items.
- `pm rail show <section.label>` — remove from hidden_items.

## 7. Implementation roadmap (er01-er05)

1. **er01 - shipped** — Rail API on `PluginAPI`: `register_item`, `RailContext`, `PanelSpec`. Section definitions as a frozen registry (`top`, `projects`, `workflows`, `tools`, `system`).
2. **er02 - shipped** — Convert current hardcoded cockpit rail to a built-in plugin `core_rail_items` registering items at indexes 0-99 per section. Rail rendering logic reads from registry instead of hardcoded list.
3. **er03 - partially shipped** — Badge + visibility predicate support in the renderer.
4. **er04 - shipped** — `pm rail list / hide / show` CLI + `[rail]` config.
5. **er05 - shipped** — Promote the common cockpit runtime values out of `RailContext.extras` into typed fields.
