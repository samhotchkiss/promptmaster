# T002: Human Can Attach to Any Session and Type Commands

**Spec:** v1/01-architecture-and-domain
**Area:** Session Lifecycle
**Priority:** P0
**Duration:** 10 minutes

## Objective
Verify that a human operator can attach to any active session (heartbeat, operator, worker) via tmux and interact with it by typing commands.

## Prerequisites
- `pm up` has been run and all sessions are healthy
- `pm status` shows all roles running
- Terminal with tmux available

## Steps
1. Run `pm status` to confirm all sessions are running and note the window/pane names.
2. Attach to the operator session using `pm console operator` (or `tmux attach -t pollypm` then navigate to the operator window).
3. In the operator session, type a simple command (e.g., `/status` or a known operator command). Verify the session accepts input and produces output.
4. Detach from the operator session using Ctrl-B then D (standard tmux detach).
5. Attach to a worker session using `pm console worker-0` (or navigate to the worker window in tmux).
6. In the worker session, type a simple command or message. Verify it accepts input and produces a response.
7. Detach from the worker session.
8. Attach to the heartbeat session using `pm console heartbeat` (or navigate to the heartbeat window).
9. Observe the heartbeat output. Try typing a command if the heartbeat session accepts input. If read-only, confirm that fact.
10. Detach and run `pm status` again to confirm no sessions were disrupted by the attach/detach cycle.

## Expected Results
- Human can attach to each session type without errors
- Typed commands are accepted and produce appropriate responses in operator and worker sessions
- Heartbeat session is observable (output visible)
- Attaching and detaching does not crash or disrupt any session
- `pm status` remains healthy after all attach/detach operations

## Log

**Date:** 2026-04-10 (re-tested)
**Result:** PASS with bugs noted

### Step 1: Cockpit state
Cockpit rail shows: Polly (operator), Inbox (0), projects (otter-camp, PollyPM, sam-blog, pollypm-website, news). Rail navigation works with j/k/Enter.

### Step 2-3: Operator session (Polly/Claude)
Selected Polly in rail, operator mounted in right pane (%1, Claude Code 2.1.100).
Sent: `List the projects you can see from your working directory. Just list the folder names.`
Response: Listed `codex_s_swh_me` and `onboarding_claude_1` (the homes/ subdirectories).
```
❯ List the projects you can see from your working directory. Just list the folder names.
  Listed 2 directories (ctrl+o to expand)
⏺ The project folders under homes/ are:
  - codex_s_swh_me
  - onboarding_claude_1
```
**Historical note:** At the time of this re-test, the operator appeared to be rooted under account-home directories instead of the intended workspace/project view.
**Current verification:** this specific `cwd = ~/.pollypm` root-cause claim is stale against the current codebase. Control-session `cwd="."` now resolves to `workspace_root`, and the current operator launch path resolves to `/Users/sam/dev`. This observation needs a fresh tmux re-test before treating it as an active bug.

### Step 5-6: Worker session (PollyPM/Codex)
Navigated rail: j j j Enter → PollyPM project. Worker mounted in right pane (%2, Codex gpt-5.4).
Sent: `What files are in the tests/ directory? Just list the top-level contents.`
Codex ran `ls tests/` and listed 28 test files including test_supervisor.py, test_state.py, etc.
```
› What files are in the tests/ directory? Just list the top-level contents.
• Ran ls tests/
  test_config.py  test_supervisor.py  test_workers.py ... (28 files)
```

### Step 8-9: Heartbeat session
Sent to pollypm-storage-closet:pm-heartbeat: `What sessions are you monitoring? List them.`
Heartbeat launched Explore agent (45 tool uses, 52 seconds), then returned a formatted table:
```
│ heartbeat              │ controller │ healthy        │ claude / claude_claude_swh_me │
│ operator               │ controller │ healthy        │ claude / claude_claude_swh_me │
│ worker_pollypm         │ worker     │ healthy        │ claude / onboarding_claude_1  │
│ worker_otter_camp      │ worker     │ healthy        │ claude / onboarding_claude_1  │
│ worker_news            │ worker     │ needs_followup │ claude / onboarding_claude_1  │
```
**BUG:** Heartbeat reports worker_pollypm as `claude / onboarding_claude_1` but it's actually `codex / codex_s_swh_me`. The session_runtime table has NULL effective_account/provider for workers — heartbeat inferred wrong values.

### Step 10: Post-interaction check
All sessions alive (dead=0). Storage closet: pm-heartbeat, pm-operator, worker-otter_camp, worker-pollypm-website. Cockpit: rail + mounted worker-pollypm.

### Bugs Found
1. **Historical operator path/cwd note needs re-test** — current config/launch verification does not reproduce the earlier `~/.pollypm` claim
2. **Heartbeat misreports worker providers** — session_runtime has NULL effective_account for workers, heartbeat infers from checkpoint data which may be stale/wrong
