# Overnight Session Ideas & Observations

## What's Working Well
- Task lifecycle flows end-to-end through Polly → worker → Russell
- Per-task workers with isolated worktrees
- Russell does genuine code review (reads diffs, runs CLI, verifies criteria)
- Dashboard shows task state across all projects
- Rail grouping separates active from inactive projects

## Issues to Fix
- **Input bar submission**: Long messages get stuck in Claude Code's input bar.
  send_keys -l + Enter doesn't reliably submit. Need to investigate tmux
  paste buffer approach or break messages into shorter chunks.
- **Per-task worker exits after completion**: Claude -p mode processes the
  prompt and exits. Sessions need to stay alive for rejection feedback.
  Consider using --continue with injected prompt instead of -p.
- **Role binding mismatch**: Tasks created before Russell update have
  reviewer=polly. Russell can't approve as himself. Need migration for
  existing tasks, or teach Russell to detect and handle this.
- **Worker claim automation**: Currently I'm manually claiming tasks.
  The heartbeat should nudge the per-task worker to claim, or the claim
  should happen automatically when the task is queued and a worker exists.

## Improvement Ideas

### Prompt Engineering
- Give Russell more context about the project (CLAUDE.md, recent commits)
  so he can make better review decisions
- Give Polly a summary of all projects and their states in her initial
  prompt so she has awareness without having to search
- Workers should get the full project context (not just the task prompt)
  including coding conventions and architecture docs

### Dashboard Improvements
- Show token usage per project (not just total)
- Show estimated cost per task
- Show worker session health inline (alive/dead/idle)
- Add a "Deploy" section showing which projects have been deployed recently

### Flow Improvements
- Auto-claim: when a task is queued, automatically claim it and spin up
  the per-task worker instead of waiting for heartbeat nudge
- Auto-deploy: after Russell approves, automatically deploy if the project
  has ItsAlive configured
- Parallel review: Russell should be able to review multiple tasks
  simultaneously instead of one at a time

### Testing Improvements
- Create a test framework that validates the full lifecycle programmatically
- Add health checks that verify: worktree exists, worker is alive, task
  state matches session state
- Monitor token burn rate and alert if a task is consuming too many tokens

### New Features
- **Project templates**: Pre-built task sets for common project types
  (web app, CLI tool, library)
- **Task dependencies in UI**: Show dependency graph in the dashboard
- **Cost tracking**: Per-task, per-project, per-day cost estimates
- **Session replay**: View a completed task's full session transcript
  in the cockpit (not just the archive path)
