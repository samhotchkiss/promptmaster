# Launch Issue Audit: Last 500 GitHub Issues

This audit looks at the last 500 GitHub issues created for
`samhotchkiss/pollypm` as of 2026-04-27. The goal is not to summarize every
issue. The goal is to identify weak app surfaces where the project repeatedly
fixed the same class of problem, reopened fixes, or found the same failure
shape under a different name.

## Source

- Query: `gh issue list --state all --limit 500 --search "sort:created-desc"`.
- Issue window: #880 back through #140.
- Created range: 2026-04-16 19:38 UTC through 2026-04-27 13:33 UTC.
- State split at audit time: 471 closed, 29 open.
- Current open set: 27 cockpit UX issues from #854-#880, plus #704 and #248.
- Top current labels in the sample: enhancement 257, Claude audit 110, bug 78,
  ux 71, stability 57, documentation 34, correctness 20, error-handling 16,
  missing-test 12.

The issue numbers below are intentionally dense. They are the evidence trail
for the patterns.

## Executive Findings

1. **The cockpit is the launch-critical weak surface.** The latest open issues
   are overwhelmingly about rail navigation, focus, layout, help, dashboards,
   inbox, activity, and action affordances. These are not isolated polish bugs.
   They point to a missing cockpit-wide interaction contract.
2. **Operational signal routing is not trustworthy enough yet.** Alerts,
   counters, inbox state, activity rows, toasts, and rail badges repeatedly
   disagree, over-alert, hide actionable commands, or expose implementation
   events to users.
3. **Session, tmux, persona, and role launch remain brittle.** Several issues
   show launch/reattach paths, role prompts, live tmux panes, and persisted
   session state disagreeing or clobbering each other.
4. **Task workflow recovery is still a state-machine problem, not a collection
   of one-off bugs.** Dead workers, stale claims, REWORK tasks, blocked tasks,
   and missing role sessions have each been fixed in slices, but the recurring
   shape is "state changed and one recovery lane did not learn about it."
5. **Verification discipline is itself a weak surface.** Multiple issues were
   closed based on local branch state or closed once and then found still broken
   after restart. Several one-day regressions landed in highly visible surfaces.

## Repeated Fix Patterns

### 1. Cockpit Input, Focus, And Navigation

Recurring issue shape:

- A visible keybinding does nothing, does something in the wrong pane, or
  triggers a global action while the user is trying to type.
- Rail focus and detail-pane focus drift apart.
- Help claims one key contract while the rendered view implements another.
- Escape, `q`, `Tab`, `Enter`, `j/k`, numbered action keys, and search input
  semantics differ by screen.

Evidence:

- Current open issues: #880, #877, #864, #863, #862, #861, #860, #859, #858,
  #857, #856.
- Recently closed issues: #850, #849, #847, #846, #843, #842, #840, #835,
  #824, #823, #822, #821, #797, #795, #789.
- #840 is the strongest warning: a fix protected `a`/`x` only when an input
  had focus, but a smoke test still approved a real task because the Tasks view
  opened with table focus. That is a system-level focus contract failure.

Hardening:

- Define a cockpit interaction contract for every mounted surface: initial
  focus, focus traversal, modal trapping, search mode, global shortcuts, escape
  behavior, destructive-action confirmation, and help text source.
- Generate help from the same keybinding registry the app uses at runtime.
- Add a cockpit smoke suite that drives `j/k`, arrows, `Enter`, `Tab`, `Esc`,
  `q`, `/`, Backspace, Ctrl-U, and numbered action keys on Home, Inbox,
  Activity, Settings, Tasks, Project Dashboard, PM Chat, and Help.
- For destructive actions, require either explicit row selection or a
  confirmation state. Single-letter global actions should not fire from default
  table focus in a view with a visible text input.

### 2. Cockpit Rendering, Truncation, And 13-Inch Layout

Recurring issue shape:

- Important recovery or command text is hidden by rail width, table columns,
  scroll overlays, or decorative button framing.
- Glyphs and sparklines render without legends.
- Layouts work in one terminal shape and break at realistic smaller sizes.

Evidence:

- Current open issues: #878, #876, #874, #872, #865.
- Recently closed issues: #844, #838, #836, #833, #832, #831, #830, #829,
  #828, #827, #826, #825, #819, #793, #792, #790.
- #831 required a second verification pass because the shortened upgrade pill
  still hid the only recovery hint, `ctrl+q`, in the default rail width.
- #829 required a second pass because preserving `Try:` in the data did not
  help when the rendered table column still cut off the command.
- #826 fixed data but missed scroll/page behavior, leaving the "Up" label
  unreachable in the modal.

Hardening:

- Add golden TUI snapshots at 80x30, 100x40, 169x50, 200x50, and 210x65 for
  the primary cockpit surfaces.
- Add text assertions that recovery affordances survive rendering, not just
  data preparation. Examples: `q quit`, `ctrl+q`, `Try: pm ...`, inbox counts,
  Help movement labels.
- Treat glyphs, sparklines, and status icons as invalid unless the same surface
  includes a nearby legend or drill-in detail.
- Reserve decorative borders for places where they do not consume vertical
  space needed for actual state.

### 3. Signal Routing: Alerts, Inbox, Activity, Toasts, Counters

Recurring issue shape:

- Operational events are surfaced as user alerts.
- Action-required events are buried in Activity or Metrics.
- The same concept has different counts in rail, Home, and Inbox.
- Suggested commands are truncated or shown only in a transient toast.
- Synthetic/test events pollute live signal.

Evidence:

- Current open issues: #879, #867, #866, #855, #854.
- Recently closed issues: #844, #834, #830, #829, #828, #820, #799, #794,
  #791, #788, #787, #786, #785, #783, #765, #760, #759, #754, #509, #502.
- #879 is the clearest launch blocker in this family: the cockpit knew about
  automatic recovery pausing after rapid failures and 98 `no_session` alerts,
  but did not elevate that degraded state to the user.
- #820 regressed #799 and exposed two independent inbox counters: Home counted
  tracked projects, while rail counted registered projects.
- #765 describes the right product rule: heartbeat classification signals are
  operational until remediation fails; only action-required states should
  interrupt.

Hardening:

- Make every emitted event carry `audience`, `severity`, `actionability`,
  `dedupe_key`, `source`, and optional `suggested_action`.
- Route by policy, not caller preference:
  `operational -> Activity`, `informational -> Activity/Inbox`, and
  `action_required -> Activity/Inbox/Toast/Rail`.
- Put rail badge counts, Home cards, Inbox list counts, and Activity filters
  behind shared query functions with regression tests for empty, hidden, FYI,
  unread, and action-required states.
- Add silence/dismiss semantics for repeated alerts like #867.
- Exclude synthetic/test event kinds from live user surfaces by default.

### 4. Session, Tmux, Persona, And Role Launch

Recurring issue shape:

- Launch or reattach creates the wrong tmux state, an idle shell, or raw
  Claude Code instead of Polly.
- Live tmux panes, persisted session state, and cockpit counts disagree.
- Role prompt/kickoff paths can be missing, wrong, rejected, or clobbered.
- Persona defense catches one phase but not another.

Evidence:

- Current open issues: #871, #870, #869, #868.
- Recently closed issues: #852, #848, #847, #841, #817, #815, #808, #775,
  #762, #758, #757, #755, #734, #309, #272, #263, #260, #257, #156, #152,
  #149, #148.
- #841 is catastrophic: relaunch/respawn hit a tmux segfault path and dropped
  the cockpit session.
- #757 and #758 show the distinction between launch-time persona mismatch,
  kickoff clobber, silent role-guide no-load, and mid-flight persona drift.
- #871 shows cockpit session inventory cannot be trusted if it reports zero
  sessions while tmux has live worker, architect, reviewer, operator, and
  heartbeat windows.

Hardening:

- Model `pm up` as an idempotent state machine: first launch, attach existing,
  recover dead shell, recover dead rail, and unsupported launch context must be
  separate states with tests.
- Never respawn a running non-shell rail pane as part of normal attach.
- Add a launch smoke matrix: outside tmux, inside unrelated tmux, inside
  existing Polly tmux, after upgrade, after crashy stale panes, and with storage
  closet already running.
- Treat persona identity as a per-turn invariant. Kickoff checks, role banners,
  post-kickoff proof, heartbeat drift detection, and remediation should share
  one canonical role contract.
- Reconcile cockpit session counts directly against `tmux list-windows` plus
  persisted session rows, and alert when the two disagree.

### 5. Task Flow, Recovery, And State Transitions

Recurring issue shape:

- A task state enters a valid-looking status that one subsystem ignores.
- Workers die, restart, get rejected, get blocked, or enter REWORK, and one
  recovery/capacity path misses that state.
- Planner and critic flows create artifacts that task listing, monitoring, or
  approval does not understand.

Evidence:

- #816, #807, #806, #788, #786, #785, #779, #778, #777, #776, #774, #773,
  #772, #771, #770, #769, #768, #395, #393, #389, #386, #383, #382, #296,
  #295, #279, #268, #259, #246, #244.
- #770 and #771 show recovery missing per-project in-progress tasks and
  leaving dead-worker work stuck.
- #816 shows REWORK tasks were invisible to dead-worker recovery and capacity
  accounting.
- #807 and #806 show recovery matching the wrong live windows or deleting node
  execution history.
- #395 shows critic infrastructure leaking synthetic tasks into the real task
  list and breaking downstream assumptions about task IDs.

Hardening:

- Maintain a single task-state transition table with explicit participation by
  assignment, recovery, capacity, cockpit, inbox, advisor, and metrics.
- Add invariant tests for each state: visible location, who owns it, who can
  move it, recovery behavior, capacity accounting, and user-facing copy.
- Add `pm doctor --launch-state` or equivalent to flag:
  in-progress tasks without live owners, queued tasks without reachable role
  sessions, REWORK tasks outside recovery, blocked tasks with no unblock path,
  and dead claims that still consume capacity.
- Ensure planner/critic subflows either use first-class non-task artifacts or
  are consistently hidden from user task queues.

### 6. Storage, Source Of Truth, And Migration Fallout

Recurring issue shape:

- Data exists, but a reader queries the wrong scope, project DB, legacy table,
  namespace, provider home, or ambient environment.
- Migration work closes one set of call sites while another writer/reader
  remains on the old path.
- Counts and derived views disagree because there is no shared service-owned
  read model.

Evidence:

- #820, #799, #759, #377, #271, #259, #380, #372, #349, #342, #341, #340,
  #339, #338, #337, #411, #704, #810, #809, #814, #812, #813, #150, #146.
- #820's tracked-vs-registered project split is the user-visible symptom.
- #259 and #377 show workspace DB vs per-project DB confusion killing pickup
  and task-status discovery.
- #271 shows notifications written to a namespace the cockpit did not surface.
- #809, #812, #813, and #814 show transcript and token accounting depending on
  the wrong provider root, wrong tree, or incomplete token schema.

Hardening:

- Document the source of truth for each launch-critical concept: project,
  session, inbox item, activity event, alert, task, execution, transcript,
  token usage, provider account.
- For each concept, expose one read API used by cockpit, CLI, heartbeat, and
  plugins. Avoid parallel counters.
- Add migration completion tests that fail if old table writers still emit
  user-visible data.
- Persist provider/account transcript roots at launch and use provider-specific
  transcript locators at teardown.

### 7. Prompt, CLI, Docs, And Runtime Contract Drift

Recurring issue shape:

- Prompts, docs, worker guides, and UI copy reference commands, files, paths,
  roles, or schemas that do not exist in the current app.
- Normal validation failures surface as tracebacks.
- Agents receive instructions that are too long, duplicated, or invisible to
  verification.

Evidence:

- #851, #837, #760, #490, #489, #488, #487, #486, #485, #484, #483, #482,
  #481, #480, #479, #478, #477, #476, #475, #474, #473, #472, #471, #446,
  #390, #387, #263, #258, #238.
- #471 references `pm mail`, which did not exist.
- #476 references `pm inbox show <id>` without verifying the subcommand.
- #387 tells users `--actor user` while the node required `--actor reviewer`.
- #851 shows a user-facing traceback for a normal missing-role validation
  error.

Hardening:

- Generate prompt and documentation command references from Typer command
  metadata or run command probes in CI.
- Add a prompt-contract test suite that verifies every referenced path,
  command, actor, enum, and output schema.
- Route all expected work-service validation errors through user-facing CLI
  formatters.
- Keep role guides in project-local or installed data paths that are valid from
  the session cwd; avoid `src/` paths in runtime instructions.

### 8. Verification And Review Process

Recurring issue shape:

- Issues were closed as already fixed based on local branch state, then reopened
  after checking `origin/main`.
- Fixes passed narrow unit tests but failed the real cockpit after restart.
- Regression issues appeared the next day against issues that had just closed.

Evidence:

- Wrong-branch close/reopen pattern: #395, #501, #505, #511, #513, #515.
- "Fix still broken after verification" pattern: #840, #831, #829, #826, #820.
- One-day cockpit regressions: #821 regressed #514, #820 regressed #799, #819
  regressed #792.
- #709 records main being red with 12 failures and 10 errors, blocking the
  desired CI gate pattern.

Hardening:

- Do not close an issue as fixed unless verification ran against current
  `origin/main` or the merged commit hash is explicitly named.
- For cockpit issues, verification must include a fresh process or cockpit
  restart when the bug depends on runtime state.
- Every regression issue should add a failing test that reproduces the original
  symptom at the rendered/user-observable layer, not just the helper branch.
- Add a release-candidate close checklist: commit hash, branch, command run,
  fresh-start status, and whether the issue changed a shared contract.

### 9. Architecture And Plugin Boundaries

Recurring issue shape:

- Plugins import other plugins' private helpers.
- CLI and jobs code mutate internal locks/connections or reach into private DB
  helpers.
- Protocols promise methods that built-ins implement as stubs or omit caller
  parameters.
- Large files and implicit routing maps make cross-surface behavior hard to
  reason about.

Evidence:

- #805, #804, #803, #802, #801, #800, #798, #796, #426, #425, #424, #423,
  #422, #421, #420, #419, #418, #370, #369, #368, #367, #366, #365, #364,
  #363, #362, #361, #360, #359, #358, #177, #185.
- #802 promoted several task-assignment private helpers to a public API, which
  is good, but the recurrence suggests boundary tests should enforce that
  shape.

Hardening:

- Add import-boundary tests for plugin packages, CLI modules, and service
  internals.
- Prefer typed public capability APIs over cross-plugin private imports.
- Make protocol conformance part of CI for built-in providers and services.
- Split high-churn cockpit/session/work-service modules along the surfaces that
  repeatedly fail: routing, rendering, data loading, keybindings, recovery.

### 10. Performance And Responsiveness

Recurring issue shape:

- Render paths scan too much, import too much, or recompute on every tick.
- Session and activity polling can turn into user-visible lag.
- Settings and inbox surfaces regress after caching/refactors.

Evidence:

- #839, #606, #560, #752, #470, #469, #468, #467, #466, #465, #464, #463,
  #462, #461, #460, #459, #458, #457, #688, #687, #686, #685, #684, #683,
  #682, #681, #680, #679.
- #839 is directly user-visible: every `j/k` rail keypress took 3-5 seconds.

Hardening:

- Add cockpit render-time budgets and keypress latency tests.
- Keep dashboard snapshots, git history, transcript ingest, and activity scans
  off the hot render path.
- Add performance regression tests for rail navigation, inbox refresh, activity
  filter/search, and settings load.

### 11. Security And Trust

Recurring issue shape:

- The sample includes a concentrated security audit batch that appears mostly
  closed, but the findings touch launch-sensitive trust boundaries.

Evidence:

- #496, #495, #494, #493, #492, #491, plus related prompt-injection/runtime
  trust issues #755, #356, #261.

Hardening:

- Keep plugin trust assumptions visible in the install/enable flow.
- Maintain path validation for worktrees, plugin content paths, branch names,
  backup tar extraction, and runtime probe execution.
- Treat heartbeat and background roles as least-privilege actors; write APIs
  should be explicit and audited.

## Launch Hardening Priorities

### P0 Before Launch

1. **Cockpit contract and smoke harness.** Build an automated tmux/Textual smoke
   pass that opens the cockpit fresh and drives Home, Rail, Inbox, Activity,
   Settings, Tasks, Project Dashboard, PM Chat, and Help at multiple terminal
   sizes. Assert focus, keybindings, no tracebacks, no raw agent bootstrap, no
   hidden recovery commands, and no contradictory counters.
2. **Signal policy and shared counts.** Put alerts, inbox, activity, rail
   badges, and Home counters behind one event/read model with routing by
   actionability. Fix #879-class degraded states so recovery failure is
   impossible to hide in Metrics only.
3. **Idempotent session launch.** Harden `pm up` and session attach/reattach
   as a state machine, with explicit tests for existing tmux sessions, stale
   panes, live rail panes, storage closet already running, and upgrade restart.
4. **Workflow invariant checker.** Add a launch doctor that reports stuck
   task/session states across recovery, capacity, REWORK, blocked, queued,
   user-review, and dead-claim cases.
5. **Issue close discipline.** Require current-main or merged-commit
   verification before closing issues, with fresh cockpit restart for cockpit
   bugs and rendered-layer assertions for regressions.

### P1 Before Broad Beta

1. Replace prompt/docs CLI references with generated or CI-probed command
   references.
2. Add plugin/import-boundary tests and protocol conformance tests.
3. Add render-time and keypress-latency budgets for the cockpit.
4. Finish storage/source-of-truth documentation and migration completion tests.
5. Keep the security audit batch on the release checklist, especially plugin
   trust, path validation, backup extraction, and background role privileges.

## Bottom Line

The highest-risk surfaces before launch are not hidden backend edge cases. They
are the control plane surfaces a user sees constantly: cockpit navigation,
alerts, inbox/activity state, and session launch/recovery. The repeated fixes
show that local patches work temporarily, but the app needs shared contracts and
rendered-end-to-end checks around those surfaces before launch.
