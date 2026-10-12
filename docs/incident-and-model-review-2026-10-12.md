# LINE bot incident and model review / 2026-10-12

## Evidence

- Last successful main dispatch: 2026-09-24 20:00 Taipei, run 35996372431.
- 2026-09-25 and 2026-09-28 main dispatches failed with `common_trade_date_not_advanced`; both dates were official TWSE holidays. The 2026-09-24 data was expected, not stale.
- The 2026-09-28 review found no same-day main candidates, suppressed its push, and nevertheless exited successfully.
- Delayed native runs executed after midnight and skipped the analysis and push steps. The watchdog had treated any successful workflow as healthy.
- GitHub's workflow page explicitly reports automatic disabling after 60 days without repository activity. Last code commit was 2026-07-28; last retained run was 2026-09-29 Taipei.
- cron-job.org account access and current token validity must be checked separately. Absence of GitHub dispatch runs after Sep 28 does not identify whether the external job was stopped or GitHub rejected it.

## Repairs

Official calendar guard; expected-session freshness rather than day-to-day date changes; LINE retry keys; sanitized JSON outcomes; sent-marker creation only after accepted delivery; empty review fails visibly; watchdog validates actual send+marker steps; weekly calendar/tests and monthly operational commit prevent inactivity; official Actions updated to Node 24 versions.

Temporary emergency closures are explicit, never inferred solely from a data-source outage. Unknown annual calendars fail rather than silently assume weekdays. GitHub schedules remain best-effort; external failures and token expiration still require independent alerts.

## Model assessment from source

Current structure is a usable, rules-based screening system, not a validated alpha predictor. It cannot establish that institutional investors have not discovered a stock, nor guarantee future outperformance.

Strengths: market-wide investability screen; multiple candidate sources; turnover-normalized institutional flows; overheating and event exclusions; profitability floor; industry caps without minimum quotas; data-date and candidate hash audit; Gemini reviews candidates rather than fabricating numerical inputs.

Material gaps:

1. Revenue acceleration compares current YoY with cumulative YoY and MoM, not a consistent 3/6/12-month history. High YoY may still reflect a low base or seasonality.
2. EPS prior data comes from a previous run's cache. Same-period refresh currently loses the prior EPS reference. Different periods are not verified as comparable standalone quarters or prior-year quarters. Do not treat these changes as verified QoQ/YoY acceleration.
3. ROE uses net income times four over ending equity. If the source is cumulative rather than standalone-quarter income, or average equity differs materially, this approximation can overstate ROE. Valuation fallback similarly needs a true four-quarter EPS rather than arbitrary annualization.
4. FinMind cash flow requests are limited to a small initial subset of the deep pool. Remaining companies can receive neutral cash flow scores. Official MOPS cash flow, receivables, inventory and debt history should cover all candidates consistently.
5. Both fund-flow industry entrances require institutional buying. Fundamentals have independent entrances, but composite ranking and final Top still favor already-observed flows. A stock with improving operating fundamentals before institutional confirmation needs an explicitly separate observation route.
6. Institutional amount is estimated as net shares times closing price, not execution-value disclosure. Relative-strength baseline uses the investable-universe median, not the requested 0050/TAIEX benchmark. Industry groups use themes and filtered peers rather than an unfiltered official industry census.
7. Gemini main report reviews only three candidates without Search enabled by default; second report enables Search for eight. It cannot discover companies excluded before the 220-stock pool. Batch-level source counts are not per-claim citations, source timestamps or verified earnings documents.
8. News, official material disclosures and investor-presentation texts do not feed a fully dated, market-wide event discovery stage. Macro candidate switch is disabled; hardcoded themes are hypotheses, not observed events.
9. A 60-day history requirement and fixed volume/price floors can miss recent listings or smaller companies. Such securities require a separate, risk-labeled observation universe rather than weakening Top.
10. No verified point-in-time missed-winner audit or rolling out-of-sample comparison with 0050 is currently established. A candidate entry log alone cannot show whether the system found stocks before a rally.

## Recommended order (scoring changes require validation)

1. Repair multi-period financial storage and comparable quarter/TTM calculations; record coverage and publication dates. Preserve missing values and remove neutral values from claims of confirmed quality.
2. Build an operating-turnaround observation route independent of institutional buying: revenue acceleration across several months, same-quarter margin improvement, positive cash conversion, acceptable valuation, mild price movement. No minimum quota; label unconfirmed flow.
3. Add dated official material disclosures and issuer investor presentations for this observation route. Gemini extracts evidence, catalyst, uncertainty and cited sources; scoring remains rule-driven.
4. Add daily missed-winner audit: top 30 by 20-day return, whether shortlisted 5/10 sessions before breakout, and rejection reasons. Avoid hindsight leakage.
5. Validate rank stability, coverage, turnover and forward 5/20/60-session returns against 0050, with transaction costs, corporate-action adjustment and survivorship controls. Only then change weights.

These revisions should be implemented in measured stages. Today's operational repair does not relax Top risk/quality thresholds or claim a newly validated black-horse strategy.
