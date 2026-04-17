"""Advisor plugin — alignment coach that runs every 30 minutes.

See ``docs/advisor-plugin-spec.md``. The advisor is a course-correction
plugin for work in flight: when a project has recent activity (commits
or task transitions), a short-lived advisor session reviews the delta
against the project's plan and goals and decides whether to emit an
inbox insight — or stay silent. The persona's credibility is its
rarity; the persona prompt (``profiles/advisor.md``) is the quality
filter, not a system-enforced rate limit.
"""
