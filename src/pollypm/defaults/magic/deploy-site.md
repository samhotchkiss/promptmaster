Description: Deploy a site and verify it works
Trigger: when a site needs staging or production deployment

# Deploy Site

## What It Does
Builds, deploys, and verifies a web app or static site. Doesn't just push — confirms the live result.

## When To Use It
- User says "deploy," "push to staging," "make it live"
- After completing site changes that need to be visible
- When verifying an existing deployment still works

## Process
1. **Check for existing deploy config.** Look for: Vercel (`vercel.json`), Netlify (`netlify.toml`), Cloudflare (`wrangler.toml`), ItsAlive (`.pollypm/itsalive/`), or custom scripts (`scripts/deploy*`).
2. **Build first.** Run the project's build command (`npm run build`, `astro build`, etc.). If the build fails, fix it before deploying.
3. **Deploy.** Use the project's deploy mechanism. For ItsAlive sites, use `pm itsalive deploy`.
4. **Verify.** After deployment, check the live URL. If Playwright is available, take a screenshot. Report the URL and any issues.
5. **Report.** Send an inbox message: "Site deployed to <url>. Verified working. Screenshot attached if available."

## Quality Bar
A deployment is not done until the live site works. "I ran the deploy command" is not enough — verify the result.
