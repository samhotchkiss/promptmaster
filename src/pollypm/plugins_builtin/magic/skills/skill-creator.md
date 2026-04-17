---
name: skill-creator
description: Interactive skill authoring — produce a new magic skill markdown file with correct frontmatter, voice, and structure.
when_to_trigger:
  - create a skill
  - add a skill
  - new magic skill
  - author skill
kind: magic_skill
attribution: https://github.com/skills-sh/skills
---

# Skill Creator

## When to use

Use when the user or team wants to author a new magic skill — to capture a pattern they repeat, a process they want baked in, or a third-party source they want to distill. This is the skill for making more skills. It enforces the shape, so every new skill is surfacable and useful on day one.

## Process

1. **Confirm the skill does not already exist.** `ls src/pollypm/plugins_builtin/magic/skills/` plus a grep on trigger patterns. Duplication dilutes signal; extend the existing skill instead when in doubt.
2. **Name the slug.** Short, kebab-case, lowercase. Match the filename. Aim for 2-4 words. No trailing verbs ("do-the-thing"); use nouns or scoped concepts ("systematic-debugging", "canvas-design").
3. **Write the one-sentence description.** This is the elevator pitch — it is what surfacing logic shows the agent. It should make the use obvious. "Turn any concept into an AI-generated visual" beats "Skills for visual work."
4. **Pick 3-5 `when_to_trigger` patterns.** Phrases the user or agent would actually type. Avoid redundancy — "debug" covers "debugging," "bug fix," "troubleshooting" via fuzzy matching; list distinct ones.
5. **Draft the body with the canonical structure**: `When to use` (2-3 sentences), `Process` (numbered 4-8 steps, imperative), `Example invocation` (copy-paste ready), `Outputs` (bulleted), `Common failure modes` (2-4 bullets, each a sentence). 50-200 lines total.
6. **Voice is opinionated.** Never "you could" — always "do X. When you see Y, do Z." The skill bakes in the right answer for the team; do not hedge.
7. **Attribution matters.** If you adapted from an external source, cite it in `attribution:` — repo URL or author link. For purely original skills, cite the repo or team.
8. **Test via the smoke test.** `uv run pytest tests/test_magic_skills_starter_pack.py`. Increment the expected count if this is a permanent addition.

## Example invocation

```markdown
# User: "I want to add a skill for 'code triage' — how to categorize and
# respond to a flood of new issues."

# Output:

---
name: code-triage
description: Categorize and respond to a burst of new issues — severity, ownership, duplicates, close-or-keep.
when_to_trigger:
  - triage issues
  - inbox zero for issues
  - categorize bugs
  - new bug report
kind: magic_skill
attribution: internal
---

# Code Triage

## When to use

Use at the start of a triage window (daily or weekly) when new issues have
accumulated. The goal is one pass: every issue gets a label, an owner, a
priority, and either a comment or a close. No issue stays unexamined.

## Process

1. Sort by creation date, oldest first. The oldest untouched issues have the
   highest reputational cost.
2. For each issue: read the body once. Not twice. Decide within 90 seconds.
3. Label: type (bug/feature/question), area (which module), severity (S1-S4).
4. Check for duplicates with GitHub's "Similar issues" sidebar. Close as
   duplicate with a link, do not merge.
5. Assign owner from the CODEOWNERS file for the affected area.
6. Leave one comment: acknowledgement + next step expectation ("Triaged —
   routed to @alice. Expect update by Thursday.")
7. Close questions that have an obvious answer. Do not hoard them hoping the
   reporter will find the doc.

## Example invocation
...
```

## Outputs

- A new `.md` file at `src/pollypm/plugins_builtin/magic/skills/<slug>.md`.
- Valid frontmatter: name, description, when_to_trigger, kind, attribution.
- Body 50-200 lines following the canonical structure.
- Smoke test updated (EXPECTED_COUNT + 1) and passing.

## Common failure modes

- Duplicating an existing skill; dilutes surfacing.
- Vague description; agent never reaches for it.
- Generic trigger patterns ("improve"); matches everything, surfaces nothing.
- Hedging voice ("you might consider..."); agent does not get a strong default.
