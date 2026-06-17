# JT Line Stock Bot

Dedicated LINE stock-selection bot repository, separated from `wusftnt-bot/JT-PM` Telegram workflows.

## Scope

This repo contains only LINE stock bot assets:

- `.github/workflows/daily-stock-line-bot.yml`
- `.github/workflows/line-stock-watchdog.yml`
- `scripts/ai_stock_bot.py`
- `data/industry_theme_map.csv`

Telegram workflows and scripts intentionally stay in `JT-PM` and are not copied here.

## Schedule

- Main LINE stock workflow: Monday-Friday, 19:20 / 19:35 / 19:50 Asia/Taipei (`20,35,50 11 * * 1-5` UTC)
- Watchdog: Monday-Friday, 19:45 / 20:00 Asia/Taipei (`45 11 * * 1-5`, `0 12 * * 1-5` UTC)
- Scheduled runs outside 19:15-20:10 Asia/Taipei are skipped to prevent stale overnight pushes.

## Required GitHub Actions secrets

Set these under `Settings -> Secrets and variables -> Actions`:

- `AI_POWERED_STOCK` LINE channel access token
- `LINE_TO` LINE recipient id for direct debug/production push
- `FINMIND_TOKEN` optional but recommended for fallback data
- `GEMINI_API_KEY` optional; enables candidate-only qualitative review. Never commit the key to this repo.

## Boundary Rules

- Do not add Telegram workflows or scripts to this repo.
- Do not use Telegram secrets in this repo.
- Keep LINE state under `.sent/line/` and `.cache/line-ai-stock-bot/` only.
