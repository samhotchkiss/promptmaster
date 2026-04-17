---
name: web-asset-generator
description: Generate favicons, app icons, OG images, and social cards from a source logo — every size, every format.
when_to_trigger:
  - favicon
  - social card
  - og image
  - app icon
  - web assets
kind: magic_skill
attribution: https://github.com/madewithclaude/awesome-claude-artifacts
---

# Web Asset Generator

## When to use

Use when launching or rebranding a site and you need the full set of web assets derived from one source logo: favicons, PWA icons, Apple touch icons, Open Graph images, Twitter/X cards. Doing this by hand takes an afternoon; this skill does it in five minutes with a checklist.

## Process

1. **Start from a single master asset.** Square SVG at 1024x1024 is the gold standard — vector means every size downscales cleanly. If you only have raster, use the largest version you have (2048x2048+).
2. **Generate favicons at every required size.** Modern baseline: `favicon.ico` (16/32/48 multi-res), `favicon-16x16.png`, `favicon-32x32.png`, `favicon.svg` (the modern hotness — browsers prefer SVG when available).
3. **Apple touch icons: one size is enough now** — `apple-touch-icon.png` at 180x180. Historical granularity (57, 60, 72...) is no longer needed; Safari scales.
4. **Android PWA icons**: `android-chrome-192x192.png`, `android-chrome-512x512.png`. Plus a `manifest.webmanifest` referencing them.
5. **Open Graph image: 1200x630.** This is the primary social preview for Facebook, LinkedIn, Slack, Discord. One composition, one file: `og-image.png`. Design it in `canvas-design`, export here.
6. **Twitter/X uses OG by default.** If you want a different card, produce `twitter-card.png` at 1200x600 and reference via `twitter:image` meta.
7. **`<head>` tag block**: emit the full canonical set:
   ```html
   <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
   <link rel="icon" type="image/png" sizes="32x32" href="/favicon-32x32.png" />
   <link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
   <link rel="manifest" href="/site.webmanifest" />
   <meta property="og:image" content="https://site/og-image.png" />
   <meta name="twitter:card" content="summary_large_image" />
   ```
8. **Test with realfavicongenerator.net or favicon-checker.com** after deploy. They crawl your site and tell you what is missing.

## Example invocation

```bash
# Generate from a master SVG
SRC=assets/logo.svg
OUT=public/

# Favicons (rsvg-convert or sharp via node)
rsvg-convert -w 16  -h 16  $SRC > $OUT/favicon-16x16.png
rsvg-convert -w 32  -h 32  $SRC > $OUT/favicon-32x32.png
rsvg-convert -w 48  -h 48  $SRC > /tmp/favicon-48.png
convert /tmp/favicon-*.png $OUT/favicon.ico   # ImageMagick multi-res ICO
cp $SRC $OUT/favicon.svg

# Apple + Android
rsvg-convert -w 180 -h 180 $SRC > $OUT/apple-touch-icon.png
rsvg-convert -w 192 -h 192 $SRC > $OUT/android-chrome-192x192.png
rsvg-convert -w 512 -h 512 $SRC > $OUT/android-chrome-512x512.png

# Manifest
cat > $OUT/site.webmanifest <<EOF
{
  "name": "Polly",
  "short_name": "Polly",
  "icons": [
    { "src": "/android-chrome-192x192.png", "sizes": "192x192", "type": "image/png" },
    { "src": "/android-chrome-512x512.png", "sizes": "512x512", "type": "image/png" }
  ],
  "theme_color": "#0d0d0d",
  "background_color": "#0d0d0d",
  "display": "standalone"
}
EOF

# OG image (call canvas-design skill with a 1200x630 spec)
# -> public/og-image.png
```

## Outputs

- Favicons: `.ico`, 16/32 PNG, SVG.
- Apple touch icon: 180x180 PNG.
- Android/PWA: 192x192 + 512x512 PNG plus `site.webmanifest`.
- Open Graph: `og-image.png` 1200x630.
- `<head>` snippet ready to paste into the layout.

## Common failure modes

- Generating from a raster source at 256x256; all derived sizes look soft.
- Forgetting the `.ico`; older browsers and many scrapers still look for it.
- Missing `apple-touch-icon.png`; iOS home-screen icons render as a blurry screenshot.
- OG image too text-heavy; unreadable at Slack's thumbnail size.
