---
name: github-actions
description: GitHub Actions CI/CD — matrices, caching, secrets, reusable workflows, deployments.
when_to_trigger:
  - ci
  - github action
  - workflow
  - ci/cd
kind: magic_skill
attribution: https://github.com/actions/toolkit
---

# GitHub Actions

## When to use

Use whenever the project is on GitHub and needs CI. GitHub Actions is the default — zero setup, free minutes for public repos, tight integration with PRs. Move to a dedicated CI (Buildkite, CircleCI) only when you need fleet-wide reusable agents or deep M1/GPU support.

## Process

1. **One workflow file per concern.** `test.yml` runs tests on every push. `release.yml` publishes on tag. `deploy.yml` deploys on merge to main. Do not cram all logic into one `ci.yml` — triggers get tangled.
2. **Use the matrix for cross-variant builds.** Test against multiple Python/Node/OS versions. `strategy: { matrix: { os: [ubuntu-latest, macos-latest], python: ['3.11', '3.12'] } }`. Do not copy the job three times.
3. **Cache dependencies explicitly.** Every language has a cache pattern: `actions/setup-python@v5` with `cache: 'pip'`, `setup-node@v4` with `cache: 'pnpm'`, `actions/cache@v4` for anything custom. Cold runs should be the exception.
4. **Pin every action to a full SHA**, not a tag. `actions/checkout@v4` is convenient but mutable — attackers who compromise an action repo can push malicious code to the tag. Pin `actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11`.
5. **Least-privilege tokens.** Set `permissions: { contents: read }` at the workflow or job level — default is write-all, which is dangerous. Bump specific permissions (e.g. `pull-requests: write`) only where needed.
6. **Secrets in GitHub Secrets, never in the YAML.** Use environments (Settings -> Environments) for prod vs staging secrets + approval rules. Production deploys require a manual approver.
7. **Reusable workflows** via `workflow_call` for logic shared across repos. Org-level reusable workflows live in `.github` repo.
8. **Concurrency controls** to cancel superseded runs. `concurrency: { group: ${{ github.ref }}, cancel-in-progress: true }` — saves minutes and avoids double-deploys.

## Example invocation

```yaml
# .github/workflows/test.yml
name: Test
on:
  push: { branches: [main] }
  pull_request:

permissions:
  contents: read

concurrency:
  group: test-${{ github.ref }}
  cancel-in-progress: true

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python: ['3.11', '3.12', '3.13']
    steps:
      - uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11  # v4
      - uses: astral-sh/setup-uv@4edf4b2d4ea6b2ba56c0a7a89ec9d9b6f2d1e0c7 # v3
        with:
          enable-cache: true
      - run: uv python install ${{ matrix.python }}
      - run: uv sync --all-extras
      - run: uv run ruff check src/
      - run: uv run pytest --cov=pollypm --cov-report=xml
      - uses: codecov/codecov-action@0565863a31f2c772f9f0395002a31e3f06189574 # v5
        with:
          token: ${{ secrets.CODECOV_TOKEN }}
```

```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push: { branches: [main] }

permissions:
  contents: read
  id-token: write  # for OIDC-based cloud auth

jobs:
  deploy:
    environment: production   # requires approval + has its own secrets
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11
      - uses: ./.github/actions/deploy-polly
        with:
          api-token: ${{ secrets.DEPLOY_TOKEN }}
```

## Outputs

- One YAML per concern under `.github/workflows/`.
- Matrix builds, SHA-pinned actions, cached deps.
- Least-privilege `permissions` scope.
- Concurrency controls cancelling superseded runs.
- Prod deploys gated through a GitHub Environment with approval.

## Common failure modes

- One mega-workflow with all logic; triggers collide and debugging is painful.
- Tag-pinned actions; supply-chain attacks land silently.
- `permissions: write-all` (the default); a compromised step can push to main.
- No cache; every run spends 2 minutes re-downloading the world.
