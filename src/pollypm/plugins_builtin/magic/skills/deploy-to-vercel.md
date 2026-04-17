---
name: deploy-to-vercel
description: Vercel deployment workflows — env vars, preview branches, edge functions, build optimization.
when_to_trigger:
  - deploy
  - vercel
  - preview deploy
  - edge function
kind: magic_skill
attribution: https://vercel.com/docs
---

# Deploy to Vercel

## When to use

Use when deploying a Next.js, SvelteKit, Nuxt, or SolidStart app — Vercel's primary use case. Also for serverless Node / Python APIs when "deploy the repo" is all you want. For long-lived processes, background workers, or websockets at scale, pick Fly.io or Railway instead.

## Process

1. **Connect via the Vercel CLI, not the dashboard.** `vercel link` inside the repo — writes `.vercel/project.json`. From then on, `vercel dev` runs locally identical to prod, `vercel --prod` deploys.
2. **Environment variables scoped to environment.** Three scopes: Development, Preview, Production. Never reuse the same value for prod and preview — preview is for testing, prod is live. `vercel env pull .env.local` for local dev.
3. **Every push creates a preview.** This is the killer feature. Never merge a PR without clicking the preview link. Lock down preview access with `vercel teams` + Vercel Authentication if the preview shows real user data.
4. **Build command and framework auto-detected**, but explicit is better. `vercel.json`:
   ```json
   {
     "framework": "nextjs",
     "buildCommand": "pnpm build",
     "installCommand": "pnpm install --frozen-lockfile"
   }
   ```
5. **Edge vs Serverless runtime per route.** Edge (`export const runtime = 'edge'`) for anything latency-sensitive that does not need Node APIs; serverless for anything that needs Node stdlib, native deps, or long-running processing. Cold starts differ; benchmark both for hot routes.
6. **Cron via `vercel.json` crons** (`{ "path": "/api/cron/daily", "schedule": "0 9 * * *" }`) — better than external cron services when your code is already on Vercel.
7. **Secrets via the CLI or env vars**, never in `vercel.json`. `vercel env add DATABASE_URL production` — prompts for value, never writes to git.
8. **Monitor via Vercel Analytics + Speed Insights.** Both are first-party. Pair with a real log aggregator (Axiom, Datadog) for searchable logs — Vercel's built-in logs roll over fast.

## Example invocation

```bash
# One-time setup
vercel link
vercel env add DATABASE_URL production
vercel env add DATABASE_URL preview
vercel env add DATABASE_URL development

# Deploy a preview — happens automatically on PR open
git push origin feature/x  # Vercel auto-builds; PR comment has the URL

# Promote to prod (from CLI if you prefer CLI over auto-promote)
vercel --prod
```

```json
// vercel.json
{
  "framework": "nextjs",
  "buildCommand": "pnpm build",
  "installCommand": "pnpm install --frozen-lockfile",
  "crons": [
    { "path": "/api/cron/daily-rollup", "schedule": "0 9 * * *" }
  ],
  "headers": [
    {
      "source": "/(.*)",
      "headers": [
        { "key": "X-Frame-Options", "value": "DENY" },
        { "key": "X-Content-Type-Options", "value": "nosniff" }
      ]
    }
  ]
}
```

## Outputs

- `.vercel/project.json` linked to the project.
- `vercel.json` with build config and any crons.
- Env vars configured per environment.
- Preview URL posted to every PR.
- Security headers set.

## Common failure modes

- Reusing prod env vars in preview; test runs mutate production data.
- Running `vercel dev` with missing env vars; works locally, fails at build in CI.
- Storing secrets in `vercel.json`; commits to git.
- Missing security headers; opens the app to clickjacking and MIME sniffing attacks.
