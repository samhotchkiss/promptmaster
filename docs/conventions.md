# Conventions

## Summary

Coding patterns, naming conventions, testing approaches, and tooling preferences.

## Conventions

- Issue-based tracking (Issue NNNN format)
- tmux pane naming: 'pollypm-storage-closet:worker_XXX'
- Role names: Heartbeat (read-only, Bash only), Operator (extended permissions)
- Issue state machine: 01, 02, 03-needs-review, 04-in-review, completed
- Worker idle detection: 5+ cycles triggers Heartbeat alert
- Heartbeat status classification overrides manual `done` based on tmux pane content
- Documentation regeneration via pollypm repair
- Escalation response protocol: Heartbeat escalations demand immediate acknowledgment and concrete next steps
- Stop-looping directive: cease loop iteration immediately, state remaining task concisely, execute next step, report blocker
- History chunk analysis: sequential processing to build consolidated understanding
- Documentation verification: cross-check claims against actual code state and event timeline

*Last updated: 2026-04-13T01:29:31.935791Z*
