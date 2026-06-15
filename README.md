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

- Main LINE stock workflow: Monday-Friday, 19:30 Asia/Taipei (`30 11 * * 1-5` UTC)
- Watchdog: Monday-Friday, 19:40 Asia/Taipei (`40 11 * * 1-5` UTC)

## Required GitHub Actions secrets

Set these under `Settings -> Secrets and variables -> Actions`:

- `AI_POWERED_STOCK` LINE channel access token
- `LINE_TO` LINE recipient id for direct debug/production push
- `FINMIND_TOKEN` optional but recommended for fallback data

## Boundary Rules

- Do not add Telegram workflows or scripts to this repo.
- Do not use Telegram secrets in this repo.
- Keep LINE state under `.sent/line/` and `.cache/line-ai-stock-bot/` only.
