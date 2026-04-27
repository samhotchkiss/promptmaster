# Role / Persona Contract

Source: implements GitHub issue #885.

This document specifies the canonical role-identity invariant.
Code home: `src/pollypm/role_contract.py`. Test home:
`tests/test_role_contract.py`.

## Why one contract

Before #885, role identity was defended in eight separate
slices: launch-time checks, kickoff banners, system-update
notices, heartbeat drift detection, remediation messages, work-
service actor validation, project-guide gating, and architect
lifecycle hand-off. Each slice owned a tiny role list, persona
mapping, or marker dict that drifted independently. The audit
(`docs/launch-issue-audit-2026-04-27.md` §4) cites:

* `#757` — operator session identified as Russell after
  kickoff-time persona defense had already passed.
* `#758` — wrong-role kickoff clobbered the operator session.
* `#762` / `#755` — update / guide paths can be wrong or
  rejected as prompt injection.

The contract is the structural fix. One `RoleContract` per
role; one `ROLE_REGISTRY`; one set of accessors and validators.
Every consumer reads from the registry. Adding / renaming /
changing a role is one edit, not seven.

## Vocabulary

* **Role** — canonical snake_case key (e.g., `operator_pm`).
  The same key the work service stores in `actor` columns.
* **Persona** — human-facing display name (e.g., `Polly`).
* **Identity marker** — substring that, when present in a pane
  snapshot, proves the session is operating as the expected
  role.
* **Conflicting persona** — name that, if claimed, indicates
  drift.
* **Drift** — the session has identified as someone else.

## Contract shape

```python
@dataclass(frozen=True, slots=True)
class RoleContract:
    key: str                        # canonical role
    persona_name: str               # display name
    guide_path: str | None          # repo-relative path
    identity_markers: tuple[str, ...]
    conflicting_personas: tuple[str, ...]
    can_write_session_state: bool
    has_project_guide: bool
```

`__post_init__` rejects any contract whose
`identity_markers` and `conflicting_personas` overlap (case-
insensitive) — that combination is incoherent.

## Registered roles

| Key                    | Persona  | Guide                                                  |
| ---------------------- | -------- | ------------------------------------------------------ |
| `operator_pm`          | Polly    | `polly-operator-guide.md`                              |
| `architect`            | Archie   | `architect.md`                                         |
| `reviewer`             | Russell  | `russell.md`                                           |
| `worker`               | Worker   | (none — per-task identity)                             |
| `heartbeat_supervisor` | Heartbeat| (none)                                                 |

## Aliases

`canonical_role()` accepts the canonical form, the display /
tmux form (`operator-pm`), and the persona name (`Polly`,
`Archie`). Unknown roles raise `ValueError` — silent fallthrough
on a typo'd role string is exactly the bug shape this module
prevents.

## validate_identity

```python
validate_identity(role: str, pane_text: str) -> str | None
```

Returns the drifted-to persona name or `None`. Pure,
total. Heartbeat drift detection calls this on every snapshot.
Patterns recognised:

* `"I'm <Name>"`
* `"I am <Name>"`
* `"This is <Name>"`
* `"This session is <Name>"`
* `"identify as <Name>"`

A claim that names one of *this role's* identity markers passes
silently. A claim that names a conflicting persona — or *any
other role's* canonical persona — returns drift. The cross-
registry check is the safety net for `#757` (operator drifting
to Russell) and similar cross-role swaps.

## build_remediation_message

The audit cites `#755` / `#757` for the canonical wording
requirements:

* avoid `<system-update>` (prompt-injection defense rejects it)
* re-anchor by naming the operating guide path
* request an explicit `"OK <persona>"` acknowledgement so the
  operator can verify the drift cleared

`build_remediation_message(role, drifted_to)` returns a single
string the heartbeat sends through the
`persona-drift-remediation` owner channel. Centralising the
wording means the next persona-drift fix lands in one place
instead of seven.

## Legacy reconciliation

`legacy_persona_table_disagreements(legacy_dict)` compares any
legacy `role -> persona` dict against the canonical registry
and returns one human-readable line per disagreement. The
release gate (#889) consults this at tag time to refuse a
release whose legacy tables do not agree with the canonical
contract. The test
`test_real_heartbeat_persona_table_matches_registry` enforces
the live `pollypm.heartbeats.local._ROLE_PERSONA_NAMES` dict
matches today.

## Migration policy

The legacy modules continue to work — the contract registry is
*additive*. New code must consult `ROLE_REGISTRY`; the launch
hardening release gate flags legacy-table drift but does not
require rewriting every reader. Migration steps:

1. Heartbeat persona table → consult registry directly. (Test
   guards equivalence.)
2. `role_routing._ROLE_KEYS` → swap for
   `set(ROLE_REGISTRY.keys())`.
3. `project_guides._SUPPORTED_PROJECT_GUIDE_ROLES` → derive
   from `has_project_guide`.
4. `heartbeats/local._MUTATING_SESSION_ROLES` → derive from
   `can_write_session_state`.

Each step is small enough to ship independently.

*Last updated: 2026-04-27.*
