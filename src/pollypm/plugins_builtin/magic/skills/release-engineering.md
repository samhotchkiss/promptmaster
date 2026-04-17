---
name: release-engineering
description: Versioning (semver), changelog, tag conventions, rollback strategy — the release process that does not fear itself.
when_to_trigger:
  - release
  - tag a version
  - changelog
  - semver
kind: magic_skill
attribution: https://github.com/semantic-release/semantic-release
---

# Release Engineering

## When to use

Use when preparing the first release of a project, or when an existing release process has become a pain point (inconsistent versions, missing changelogs, risky deploys). A boring release process is the goal — this skill gets you there.

## Process

1. **Semver is the default.** `MAJOR.MINOR.PATCH`. Breaking changes bump MAJOR, additive features bump MINOR, fixes bump PATCH. Pre-1.0 is a discount license to break things — but still use semver so tooling works.
2. **Conventional Commits drive the version.** `feat:` -> MINOR, `fix:` -> PATCH, `feat!:` or `BREAKING CHANGE:` footer -> MAJOR. Once the team commits in this style, automated release tooling just works.
3. **One changelog, generated from commits.** `CHANGELOG.md` at repo root. Generate via `git-cliff`, `semantic-release`, or `changesets`. Hand-maintained changelogs drift; generated ones stay correct.
4. **Git tags are the release record.** `v1.2.3` matching the version in the package manifest. Annotated tags (`git tag -a v1.2.3 -m "..."`) carry the release notes. Push tags on release.
5. **Release in CI, not locally.** A `release.yml` triggered on tag push: build artifacts, publish to registry, create GitHub Release with notes. Humans push tags; machines do the work. Makes releases reproducible.
6. **Immutable release artifacts.** Docker images tagged with the git SHA and the version (`polly:v1.2.3`, `polly:abc1234`). Never mutate an existing tag. Never use `:latest` in production deploys.
7. **Rollback strategy rehearsed.** Document the exact command/runbook: "kubectl rollout undo deployment/polly" or "vercel promote <previous-sha>". Do a rollback drill every quarter; do not wait for an incident to discover it is broken.
8. **Post-release verification.** Smoke tests against the deployed version. A release that green-lights at deploy without checking real traffic is a release that ships regressions.

## Example invocation

```bash
# Conventional commits enforced via commitlint
# commit messages:
feat(work): add cascade cancellation
fix(cli): correct --actor default
feat(api)!: remove deprecated /v0 routes  # MAJOR bump

# Generate changelog + tag
npx changeset version   # or: git cliff --bump
git commit -am "chore: release v1.2.0"
git tag -a v1.2.0 -m "$(sed -n '/^## \[1.2.0\]/,/^## /p' CHANGELOG.md | head -n -1)"
git push origin main --follow-tags
```

```yaml
# .github/workflows/release.yml
name: Release
on:
  push:
    tags: ['v*']

permissions:
  contents: write   # for creating GitHub Releases
  packages: write   # for publishing containers
  id-token: write   # for OIDC

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
      - name: Build
        run: |
          uv build
          docker build -t ghcr.io/${{ github.repository }}:${{ github.ref_name }} .
          docker build -t ghcr.io/${{ github.repository }}:${{ github.sha }} .
      - name: Publish
        run: |
          docker push ghcr.io/${{ github.repository }}:${{ github.ref_name }}
          docker push ghcr.io/${{ github.repository }}:${{ github.sha }}
      - name: GitHub Release
        uses: softprops/action-gh-release@c062e08bd532815e2082a85e87e3ef29c3e6d191  # v2
        with:
          body_path: .release-notes.md
          generate_release_notes: true
```

## Outputs

- Semver version in the package manifest, updated per release.
- `CHANGELOG.md` regenerated per release.
- An annotated git tag pushed.
- A release pipeline that builds, publishes, and creates the GitHub Release.
- A rehearsed rollback runbook.

## Common failure modes

- Manual changelog that drifts; nobody trusts it after two releases.
- Mutable image tags (`:latest`, `:stable`); rollback to "last working" is guesswork.
- Releases built locally; "works on my machine" leaks into production.
- No rollback drill; when you need it, the command has bit-rotted.
