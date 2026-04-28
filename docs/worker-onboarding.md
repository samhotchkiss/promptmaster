# Worker Onboarding

**Audience:** contributors and maintainers who need to understand the worker
onboarding flow that ships today.

This page documents current behavior only. It is not a roadmap. For planned
follow-up ideas, see
[`docs/future/worker-onboarding-future.md`](future/worker-onboarding-future.md).

## Canonical worker-facing docs

The worker-facing guide is
[`docs/worker-guide.md`](worker-guide.md). That is the document a worker should
read for lifecycle, commands, and troubleshooting.

The same guide is also available through:

- `pm help worker`
- the injected `## Worker Protocol` section for worker sessions

## What ships today

Worker onboarding currently happens through three surfaces:

1. **Role-scoped guide content**
   The worker guide is the long-form, copy-paste-ready document for task
   workers. It explains the task lifecycle, expected commands, output payload
   shape, and common failure modes.

2. **Automatic prompt injection**
   Worker sessions include the guide under a `## Worker Protocol` heading during
   prompt assembly. This means a fresh worker session starts with the same
   operating instructions the CLI exposes via `pm help worker`.

3. **Per-task prompt inside the worktree**
   When `pm task claim <project>/<number>` provisions a worker, the session
   manager creates a real git worktree at
   `.pollypm/worktrees/<project>-<number>` on branch
   `task/<project>-<number>`, writes `.pollypm-task-prompt.md` into that
   worktree, and launches the worker with a kickoff message telling it to read
   that file.

## Current worker flow

At a high level, the shipped worker experience is **per-task and
auto-claimed**. There is no long-running generic worker shell, no
self-service `pm task next` polling loop, and no manual supervisor
`tmux send-keys` poke.

1. Polly (or another operator) creates the task and queues it
   (`pm task queue <project>/<number>`). For plan-gated flows the queue
   transition only succeeds once the plan gate is satisfied.
2. **Polly auto-claims her own queued worker-role tasks.** Per the
   operator delegation contract, immediately after queueing she runs
   `pm task claim <project>/<number> --actor worker`. This is the
   contract — non-worker roles (review, plan) are the only ones that
   stop at `queued`.
3. **`pm task claim` provisions everything in one step.** The work
   service creates the worktree at
   `.pollypm/worktrees/<project>-<number>`, checks out branch
   `task/<project>-<number>`, writes `.pollypm-task-prompt.md` into the
   worktree root, opens a per-task tmux window named
   `task-<project>-<number>` whose CWD is the worktree, and launches the
   provider CLI inside it.
4. **The heartbeat sweep recognizes the per-task session and
   force-pushes the kickoff.** If the pane was still racing the provider
   bootstrap at `claim` time, the next heartbeat tick retries the
   kickoff send and only stamps `kickoff_sent_at` once the pane is ready.
   No human or supervisor needs to send the kickoff by hand.
5. The worker reads `.pollypm-task-prompt.md` plus the worker guide,
   implements the work, commits to the task branch, and submits
   `pm task done ... --output '{...}'`.

If a worker session stalls (no kickoff banner, no activity), the
recovery path is the heartbeat sweep — not a manual tmux poke. The
heartbeat will re-deliver the kickoff on its next cycle, raise the
appropriate alert if the session is wedged, and surface a
`no_session`/`silent_worker` signal for Polly to act on.

The two documents serve different purposes:

- `docs/worker-guide.md` is the human-readable operating manual.
- `.pollypm-task-prompt.md` is the task-specific brief generated for one worker
  on one task.

## Current error guidance

The current build already gives workers guidance in a few places:

- `docs/worker-guide.md` has a dedicated troubleshooting section for common
  worker-path failures.
- `pm help worker` gives workers a stable way to re-read that guidance from the
  CLI.
- worker provisioning writes a task-specific prompt with an explicit
  `pm task done ... --output` example instead of leaving the completion format
  implicit.

This page intentionally does **not** promise that every CLI error in PollyPM is
fully standardized. It only documents the onboarding and guidance surfaces that
exist now.

## Code paths

The current onboarding flow is implemented in these areas:

- `src/pollypm/cli.py`
  role-scoped `pm help worker`
- `src/pollypm/memory_prompts.py`
  worker-guide prompt injection
- `src/pollypm/work/session_manager.py`
  task worktree creation, `.pollypm-task-prompt.md`, and worker launch kickoff

## Non-goals for this page

This page is not:

- a worker manual
- a future-spec document
- a backlog for error-message improvements

Those concerns now live separately:

- worker instructions: [`docs/worker-guide.md`](worker-guide.md)
- future ideas: [`docs/future/worker-onboarding-future.md`](future/worker-onboarding-future.md)
