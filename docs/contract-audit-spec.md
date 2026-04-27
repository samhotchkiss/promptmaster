# Contract Audit Helpers

Source: implements GitHub issue #888.

This document specifies the prompt / docs / CLI contract
verification helpers that close the audit gaps the existing
`test_prompt_command_references.py` and
`test_docs_command_references.py` tests do not cover. Code
home: `src/pollypm/contract_audit.py`. Test home:
`tests/test_contract_audit.py`.

## Why an additional layer

Existing tests verify every ``pm <command>`` reference resolves
in the Typer command tree. The pre-launch audit
(`docs/launch-issue-audit-2026-04-27.md` §7) cites *other*
contract dimensions where drift bit launch-critical surfaces:

* `#387` — `--actor user` referenced where the node required
  `--actor reviewer`.
* `#258` / `#390` — runtime instructions pointed at `src/`
  paths invalid from session cwd.
* `#851` — a missing-role validation surfaced as a Rich/Python
  traceback.
* `#487` / `#488` / `#489` / `#490` — duplicated worker-guide
  injection because two emitters each prepended the canonical
  copy without coordinating.

Each helper here is a pure function. Tests call them on real
docs / prompt sources; the release gate (#889) consults the same
helpers at tag time.

## Helpers

### Actor-name verification

`extract_actor_references(text, path)` — returns every
`--actor <name>` reference with file path and line number.

`known_actor_names()` — the canonical set of valid actor
names. Derives from `pollypm.role_contract.ROLE_REGISTRY` plus
CLI sentinels (`user`, `polly`, `system`) plus persona aliases
(`polly`, `russell`, `archie`) plus hyphen-form variants.

Test: `test_real_docs_actor_references_are_known` cross-checks
every reference in `docs/worker-guide.md` against the canonical
set.

### Role guide path verification

`known_role_guide_paths()` — every absolute Path from the role
contract.

`role_guide_paths_exist()` — returns one human-readable line per
guide path that does not resolve on disk. The release gate
(#889) blocks v1 if this returns non-empty.

This audit *already caught a live drift bug*: the legacy
heartbeat persona table (`heartbeats/local._ROLE_GUIDE_PATHS`)
named `architect.md` but no such file existed. The role
contract was updated to set `guide_path=None` for architect
(its persona is built inline in `profiles.py`, not from a
standalone markdown).

### Rich-traceback detection

`looks_like_rich_traceback(text)` — heuristically detects
Python tracebacks. Used by the smoke harness (#882) and by any
CLI handler test that wants to assert "no traceback in this
error path" (#851 fix).

### Generated-snippet marking

`find_unmarked_generated_snippets(paths)` — flags doc files
whose name suggests CLI-generated content
(`cli-reference.md` etc.) but which lack the canonical marker
`<!-- generated-from-cli -->`. The marker requirement keeps
generated docs from drifting silently when their source
schema changes.

### Worker-guide duplication

`detect_worker_guide_duplication()` — counts copies of the
canonical worker-guide section header in the live worker-guide
doc set. Two copies in one document means a duplicate inject
(#487 / #488 / #489 / #490 family). The default search set is
`docs/worker-guide.md`, `docs/worker-onboarding.md`, and
`src/pollypm/memory_prompts.py`.

## What the audit does NOT cover

* It does not re-implement the existing `pm <command>`
  reference checks — those stay in `test_prompt_command_
  references.py` and `test_docs_command_references.py`.
* It does not verify enum values exactly — the work-service
  enum-value check is folded into the work-service spec
  (`docs/work-service-spec.md`).
* It does not exhaustively scan every doc — the audit's
  explicit set (worker-guide, worker-onboarding, memory_prompts)
  is the launch-critical subset; broader scans are the next
  follow-up.

*Last updated: 2026-04-27.*
