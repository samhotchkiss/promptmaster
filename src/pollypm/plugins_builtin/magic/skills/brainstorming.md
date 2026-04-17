---
name: brainstorming
description: Generate-then-prune ideation — constraints first, quantity before quality, cluster and rank.
when_to_trigger:
  - brainstorm
  - ideas for
  - ideation
  - generate ideas
kind: magic_skill
attribution: https://github.com/skills-sh/skills
---

# Brainstorming

## When to use

Use when the team is stuck on "what should we even do?" or when a plan needs a list of candidate approaches before committing. Brainstorming is not blue-sky meandering — it is a structured generate-then-prune. This skill gets you from "we need ideas" to "we have three ranked candidates" in 30 minutes.

## Process

1. **Write the constraints down before generating anything.** Budget, time, tech stack, non-goals, must-haves. Ideation without constraints wastes half the ideas on infeasible directions.
2. **Generate cheap, generate plenty.** 15 minutes of uncritical output; aim for 20 ideas. Do not evaluate in the generation phase; evaluation kills divergence. Bad ideas are the price of good ones.
3. **Force variety with prompts.** "What's the boring version? What's the over-engineered version? What would a competitor do? What's the opposite? What if we had 10x the budget? What if we had 10% of the time?" Each prompt breaks stale trajectories.
4. **Walk away for 5 minutes.** Seriously — take a break between generation and pruning. A cold re-read catches weak ideas you bought into during generation.
5. **Cluster by theme.** Adjacent ideas group together. Themes reveal the shape of the solution space. Often three to five themes emerge from twenty ideas.
6. **Score each idea on two axes.** Impact (if it works, how much does it help?) × Effort (how hard to try?). Plot on a 2x2. Top-left quadrant (high impact, low effort) is where you start.
7. **Pick the top three candidates.** Three, not one — an early commit to one idea skips the comparison that makes the choice rigorous. Write one paragraph on each: the idea, the strongest risk, the next step.
8. **Time-box the next step.** "Try candidate A for two days; if no signal, switch to B." Do not debate abstractly; test cheaply.

## Example invocation

```markdown
# Brainstorm: how to surface skills to workers without slowing session startup

## Constraints
- Must work with existing plugin-discovery mechanism (#173).
- Cannot add >50ms to session boot.
- No new dependencies.
- Must degrade gracefully if the memory system is unavailable.

## Raw ideas (18)
1. Load all skills on boot, keep in memory.
2. Load skills lazily on first use.
3. Pre-compile trigger patterns into a single regex.
4. Use embeddings to match current task against skill descriptions.
5. Ship a hardcoded top-5 list; no dynamic matching.
6. Let the user configure which skills to load.
7. Load all skills, cache the parsed list to disk on first run.
8. Surface skills by category, let the agent drill down.
9. No surfacing — agent reads the full catalog on demand.
10. ... etc

## Cluster
- Loading strategy: {1, 2, 6, 7}
- Matching strategy: {3, 4, 5, 8}
- UI strategy: {8, 9}

## Score (impact × effort)
- 1 "eager load, cache": 4×2 = high impact, low effort. Top-left.
- 3 "single regex from triggers": 3×2 = good.
- 4 "embedding match": 5×5 = great impact, but infrastructure cost high.
- 5 "hardcoded top-5": 2×1 = cheap but low ceiling.

## Top 3
1. **Eager load + cache** — simplest, verifiable, gets us shipping. Risk:
   scale past 500 skills may bloat memory.
2. **Regex from triggers** — layered on top of #1; cheap matching.
3. **Embedding match** — deferred to v2; record as a follow-up.

## Next step
Try #1 + #2 in a 2-day spike. Ship if boot time stays under budget.
```

## Outputs

- A constraints list written before any idea generation.
- 15-25 raw ideas, unfiltered.
- Clusters + scores + 2x2 plot.
- Top 3 candidates with a next-step plan for each.
- A time-boxed plan for testing the best candidate cheaply.

## Common failure modes

- Evaluating during generation; kills divergence, stops at 5 safe ideas.
- Generating without constraints; half the ideas are infeasible.
- Picking one "best" idea without a top 3; overcommit to the first plausible direction.
- No next-step plan; the brainstorm becomes a file, not a decision.
