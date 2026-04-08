# 0003 Token Usage Ledger

## Goal

Keep a real log of token usage per account, per model, and per project, aggregated per hour.

## Current Approach

Use live session pane snapshots at heartbeat time:

- parse cumulative token counts from Claude and Codex session footers
- parse the visible model label from the pane
- compute per-session deltas from the previous sample
- store hourly rollups keyed by account, provider, model, and project

## Deliverables

- Persistent token usage rows in the state store
- Hourly aggregation queries
- At least one control-room or CLI surface that shows recent token totals

## Acceptance Criteria

- Heartbeat records token usage when the pane exposes a cumulative token count
- Queries can answer usage by hour, account, model, and project
- The implementation is covered by automated tests
