---
name: firecrawl-scrape
description: Scrape a single page or whole site with structured extraction via Firecrawl's API.
when_to_trigger:
  - scrape
  - extract from website
  - web scraping
  - crawl site
kind: magic_skill
attribution: https://github.com/mendableai/firecrawl
---

# Firecrawl Scrape

## When to use

Use when you need markdown (or structured JSON) from a website and do not want to babysit a headless browser. Firecrawl handles the rendering, bypasses bot protections on supported sites, and returns clean markdown. Reach for `browser-use-agent` when you need interactive behavior; Firecrawl is for read-only extraction.

## Process

1. **Pick the operation.** `scrape` for a single URL, `crawl` for a whole site, `map` for just the list of URLs, `extract` for structured fields from a URL. Do not crawl when you want one page.
2. **Always check robots.txt and ToS first.** Scraping that violates terms is a legal risk and an ethical one. Firecrawl respects robots.txt by default; do not disable without cause.
3. **Use the Python or TypeScript SDK** over raw HTTP. Handles retries, rate-limit honoring, and response parsing for you.
4. **For scrape: request markdown** (`formats: ['markdown']`). Markdown strips boilerplate and is LLM-friendly. Request `html` only when you need the original structure.
5. **For crawl: set sensible limits.** `limit: 100` by default, never `unlimited` on first run. Firecrawl charges per page; runaway crawls are expensive.
6. **For extract: define a JSON schema.** `schema: { type: 'object', properties: { ... } }`. The API applies LLM extraction against the schema — give it strong types so you get strong outputs.
7. **Cache results.** Write to `.cache/firecrawl/<hash>.json` keyed on URL + options. Re-scraping the same page on every run is wasteful and rude.
8. **Handle rate limits gracefully.** Firecrawl returns 429 with Retry-After; the SDK handles it, but your batching logic should back off if you are hammering a single domain.

## Example invocation

```python
from firecrawl import FirecrawlApp

app = FirecrawlApp(api_key=os.environ['FIRECRAWL_API_KEY'])

# 1. Single page -> markdown
result = app.scrape_url('https://example.com/pricing', {
    'formats': ['markdown'],
    'onlyMainContent': True,
})
with open('.pollypm/artifacts/task-47/pricing.md', 'w') as f:
    f.write(result['markdown'])

# 2. Structured extraction with schema
schema = {
    'type': 'object',
    'properties': {
        'plans': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'price_usd_monthly': {'type': 'number'},
                    'features': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['name', 'price_usd_monthly'],
            },
        },
    },
}

extracted = app.extract(
    ['https://example.com/pricing'],
    {'prompt': 'Extract each plan tier.', 'schema': schema},
)
plans = extracted['data']['plans']

# 3. Crawl a whole site (bounded)
crawl = app.crawl_url('https://example.com', {
    'limit': 50,
    'scrapeOptions': {'formats': ['markdown']},
    'includePaths': ['/docs/.*'],
})
```

## Outputs

- Markdown files (single scrape) or a structured JSON dataset (extract).
- All results saved to `.pollypm/artifacts/` and cached under `.cache/firecrawl/`.
- A manifest listing every URL scraped with timestamp and content hash.

## Common failure modes

- Crawling without a limit; bill surprise + operator anger at the source site.
- Extracting without a schema; get unstructured LLM output you have to parse again.
- Re-scraping the same page on every run; wasteful and rate-limited.
- Ignoring robots.txt / ToS; legal, ethical, practical risk.
