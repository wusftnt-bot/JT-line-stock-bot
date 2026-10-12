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

- Main external trigger: 20:00 Asia/Taipei; native recovery slots: 20:01-21:46.
- AI review external trigger: 21:15; native retry slots: 21:15-21:59.
- Watchdog checks actual send and marker steps for both reports through 22:00.
- Official TWSE holidays and weekends are skipped; a new trading session must use that session's data.
- Scheduled runs outside the evening window are skipped to prevent stale overnight pushes.
- Manual test runs do not save the daily sent marker; only scheduled/backup runs mark the day as sent.
- LINE retry keys prevent duplicate acceptance for the same report/day (24-hour LINE validity).
- Every attempted report uploads a sanitized operational result for 30 days. A green skipped run is not a delivery.
- Weekly health checks persist one monthly operational record to keep public schedules active.
- Add temporary emergency closures with the `AI_STOCK_EXTRA_CLOSED_DATES` variable (ISO dates, comma-separated).
- Verify next year's TWSE calendar before year-end; unknown/unavailable calendars fail visibly.

## External scheduler recovery

If cron-job.org dispatch gets HTTP 404/422 after inactivity, first reenable the workflow in GitHub Actions and test again. A dispatch returning HTTP 204 only confirms the request was accepted; it does not prove LINE delivery.

Keep failure notifications enabled on both cron-job.org jobs. Check token expiry and `Actions: read/write` permission for this repo only. Never paste tokens in issues, logs, source code, or chat. The health workflow provides an additional recovery layer; independent external failure alerts are still required if GitHub itself does not wake.

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
