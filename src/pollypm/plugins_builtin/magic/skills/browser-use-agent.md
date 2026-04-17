---
name: browser-use-agent
description: Drive a browser as an agent — navigate, fill forms, scrape, screenshot, with safe error recovery.
when_to_trigger:
  - browser
  - automate web task
  - browser automation
  - headless browser
kind: magic_skill
attribution: https://github.com/browser-use/browser-use
---

# Browser-Use Agent

## When to use

Use when an external task requires a real browser: filling a form that has no API, scraping a site behind JavaScript rendering, automating a workflow in a third-party web app. Do not use for load testing (wrong tool) or for sites you do not have authorization to access.

## Process

1. **Framework choice: browser-use for LLM-driven, Playwright for scripted.** `browser-use` wraps Playwright with an LLM that decides actions from a screenshot + DOM tree; Playwright is deterministic. For repeatable tasks, Playwright. For "do whatever it takes" one-offs, browser-use.
2. **Start with headed mode while developing.** Watch the agent work. Headless is for production / CI only. Half the bugs come from "I didn't realize the site showed a cookie banner."
3. **Describe the goal, not the steps.** `browser-use` style: "Log into example.com, navigate to Tasks, export CSV of tasks created this week, save as `export.csv`." Do not micromanage clicks; the agent reads the page.
4. **Pin the browser version.** `playwright install chromium@<version>`. A browser update can change accessibility tree labels and break selectors.
5. **Handle auth with storageState**, not re-login per run. Login once, save cookies+storage, reuse. Multi-factor auth stays a one-time human step.
6. **Screenshot on every major step.** When the agent gets stuck, the screenshots are the only forensics. Save to `artifacts/task-N/step-{i}.png`.
7. **Bound every action with a timeout.** 30s default per action. The agent must fail fast on stuck pages — infinite retries are how you get banned.
8. **Record the full run.** `recordVideo: { dir: 'runs/' }` captures a video. Essential for debugging; invaluable when the agent succeeds unexpectedly and you want to learn how.

## Example invocation

```python
# browser-use — LLM-driven
from browser_use import Agent
from langchain_openai import ChatOpenAI

agent = Agent(
    task=(
        "Log into example.com with credentials from env vars. "
        "Navigate to the Tasks section. Filter by 'created this week'. "
        "Export the results as CSV. Save the downloaded file to "
        ".pollypm/artifacts/task-47/export.csv."
    ),
    llm=ChatOpenAI(model='gpt-4o'),
    browser_config={'headless': False, 'storage_state': '.auth/example.json'},
)
result = await agent.run(max_steps=30)
print(result.final_result)
```

```python
# Playwright — scripted when the flow is stable
from playwright.async_api import async_playwright

async def export_tasks():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        ctx = await browser.new_context(storage_state='.auth/example.json')
        page = await ctx.new_page()

        await page.goto('https://example.com/tasks')
        await page.get_by_role('combobox', name='Filter').select_option('this-week')
        async with page.expect_download() as dl:
            await page.get_by_role('button', name='Export').click()
        download = await dl.value
        await download.save_as('.pollypm/artifacts/task-47/export.csv')
        await browser.close()
```

## Outputs

- The result artifact (file, data, screenshot).
- A run log with each step and its screenshot.
- A video recording of the session.
- Updated `storageState` if auth refreshed.

## Common failure modes

- Running headless from the start; cookie banners, auth walls go unseen.
- Logging in per run; triggers anti-abuse, breaks MFA.
- No screenshots or video; debugging is guesswork.
- No timeouts; stuck page hangs the run indefinitely.
