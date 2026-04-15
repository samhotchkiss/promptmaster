# To Discuss with Sam

Items that need human input or decision before proceeding.

## Open Questions

1. **Russell's account**: I assigned Russell to `claude_s_swh_me` (your personal Claude account). Should he use a different account, or is sharing with you fine? He'll consume tokens from that subscription.

2. **Deploy step in review**: Should deploy be part of the worker's "done" signal (worker deploys before signaling done), or should Russell trigger deploy after approving? The current flow has no deploy step — the worker just commits.

3. **Human approval UX**: For user-review flow tasks, how should the approval notification look in the cockpit? Options: (a) inbox message with approve/reject buttons, (b) task detail view with approve/reject actions, (c) both.

4. **Project grouping in rail**: You want active projects (task in last 24h) above inactive ones. Should new projects with zero tasks also appear in the "active" group, or only once they have their first task?

## Issues Found During Testing

- `docs/project-overview.md` is 142MB — added to .gitignore. Needs investigation on what's bloating it. Root cause: knowledge extraction pipeline appends JSON blobs indefinitely.
- Operator session prompt is baked into pollypm.toml (not just the profile) — updating builtin.py doesn't fully update the operator prompt without editing the TOML too.
- **Input stuck in bar (recurring)**: Messages sent via `tmux send-keys` sometimes land in the input bar but don't get submitted. The `_verify_input_submitted` retry mechanism doesn't reliably catch multi-line wrapped text. May need to use tmux's `-l` literal mode differently or break long messages into shorter chunks.
- **Worker session uses Codex (gpt-5.4) by default**: The `pm worker-start` command created a Codex worker, not Claude. This is because the config uses `codex_s_swh_me` for workers. Per-task workers from the SessionManager should probably use Claude since the task prompts are designed for Claude Code.

## Overnight Testing Progress

### Weather-CLI project
- [x] Round 1: Project registered, worker created
- [x] Round 2: weather_cli/1 (core fetch module) — full lifecycle completed
  - Polly created task with pm task create + pm task queue
  - Per-task worker session created with worktree (task-weather_cli-1)
  - Worker implemented code, ran CLI to verify (Temperature: 21.1C, Overcast)
  - Russell reviewed: checked git diff, ran CLI, verified all 6 requirements
  - Russell merged branch to main, approved
  - NOTE: Russell had to use --actor polly because task was created with old role binding
- [ ] Round 3: weather_cli/2 (colored output + --units) — in progress
  - Will test rejection flow if Russell finds issues
- [ ] Rounds 4-10: dependency chain, spike, bug, hold/resume, user-review

### Issues to Fix
- Role binding: tasks created before the Russell update use reviewer=polly, preventing Russell from approving as himself
- Per-task worker window disappeared from storage closet during testing (may be duplicate cleanup or heartbeat interference)
- Input bar submission: long messages still sometimes get stuck
