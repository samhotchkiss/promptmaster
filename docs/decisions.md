# Decisions

## Summary

Key decisions made during the project, with rationale and context.

## Decisions

- Use SQLite for storage backend
- API key-based authentication as primary auth mechanism
- tmux as the control plane interface
- Role-based access control with Heartbeat and Operator roles
- Heartbeat role: Claude with Bash only (edit/write blocked), read-only analysis only
- Operator role: Claude with extended permissions for code modification and testing
- Use Haiku model for extraction work (cost optimization)
- Reassign idle workers to active work rather than let them sit idle
- Complete system state roadmap consolidation as priority knowledge extraction task
- Run full pytest suite as final validation before marking issues complete
- Set completed worker sessions to `done` status to suppress heartbeat alerts
- CRITICAL: Direct instructions from users are MANDATORY and must never be ignored
- Run pollypm repair to regenerate project documentation scaffolding
- Assign post-repair knowledge extraction tasks to consolidate historical project states
- Process project histories in chunks to efficiently consolidate state understanding
- Documentation claims must be verified against actual code state and event timeline—mismatches indicate stale documentation

*Last updated: 2026-04-13T01:29:31.935791Z*
