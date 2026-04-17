---
name: devil's-advocate
description: Steel-man the opposite position — surface real objections, rank by severity, separate "wrong" from "uncomfortable".
when_to_trigger:
  - devil's advocate
  - challenge this
  - red team
  - poke holes
  - contrary view
kind: magic_skill
attribution: https://github.com/skills-sh/skills
---

# Devil's Advocate

## When to use

Use when a decision is building momentum and nobody is pushing back. Team consensus without dissent is a signal — the real objections are being suppressed, not absent. This skill forces the honest objections into the open so the decision gets tested before it ships, not after.

## Process

1. **State the proposal exactly as its proponent would.** Steel-man, not straw-man. If you cannot state the case for the proposal as strongly as its champion would, you have not understood it well enough to critique.
2. **Generate objections in categories.** Technical (does it work?), scope (is it too big / too small?), sequencing (wrong order?), team (wrong people / wrong time?), market (nobody wants this?), values (does this violate a principle we hold?). Five categories ensures you do not miss an axis.
3. **For each objection, write the strongest version.** Not "someone might say..." but "here is why this is actually wrong." If you are softening the objection to be polite, it will not land; the decision proceeds uncorrected.
4. **Distinguish "wrong" from "uncomfortable."** Some objections are correctness objections (the plan will fail); some are preference objections (I would do it differently). Label each. Only correctness objections should block.
5. **Rank by severity.** Blocker (would fail if ignored), yellow (serious risk, mitigate), minor (note but proceed). Three tiers; no fourth.
6. **Propose a resolution for each blocker.** "Disprove this objection experimentally before committing." "Change the scope to eliminate this risk." "Accept the risk with a named fallback." Critique without resolution is easy — do the work.
7. **Present to the proponent, not around them.** Email or DM, not a passive-aggressive doc. The goal is to improve the proposal, not to win a rhetorical point.
8. **Time-box the pushback.** One day max, usually 30 minutes. Longer means you are not challenging — you are campaigning. If the proponent does not accept any blocker, escalate once and then defer.

## Example invocation

```markdown
# Devil's advocate: "Ship all 71 magic skills in one PR"

## Steel-man
One PR lets reviewers see the full starter-pack shape; piecemeal review
misses cross-cutting inconsistencies; the smoke test is the acceptance
gate and either passes or doesn't.

## Objections

### Technical (blocker)
A 71-file PR diff is ~7k lines of content. Reviewers will rubber-stamp
or skim. The errors will be systemic (voice inconsistency, missing
attributions) and not caught.

### Scope (blocker)
This PR cannot be merged if any single skill fails review. One poor
skill blocks the other 70.

### Sequencing (yellow)
If the magic plugin's loader (#173) has any bugs, we discover them with
71 skills in-flight. Better to land 10 first and confirm the shape works.

### Team (minor)
Only one agent is producing 71 files; no second pair of eyes on the
draft before submission.

### Values (minor)
PollyPM's convention is "ship in reviewable slices." This PR violates
that implicitly.

## Resolution proposed
- Split into 10 commits, one per category (blocker 1, 2).
- Each commit is independently mergeable, so poor content in category 3
  does not block category 4.
- First category lands as a pilot; loader issues surface early (yellow 1).
- Self-review protocol: re-read 3 random skills from each batch before
  pushing.

## Ranking
- Blockers: split into category-per-commit.
- Yellow: sequencing concern addressed by pilot-first.
- Minor: self-review mitigates; accepted.
```

## Outputs

- A steel-manned restatement of the proposal.
- Objections organized by category, ranked by severity.
- Labels on each: correctness vs preference.
- Proposed resolution for every blocker.
- A decision: proceed (with changes), pause, or escalate.

## Common failure modes

- Straw-manning the proposal; the critique misses the actual strength.
- Polite softening; real objections stay underground, decision ships, regret follows.
- Preference framed as correctness; team resents the pushback because it was not the real issue.
- No resolution proposed; critique reads as obstruction, proponent ignores.
