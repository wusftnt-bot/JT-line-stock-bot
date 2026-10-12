"""Comparable MOPS periods and explicitly labelled valuation estimates."""
from __future__ import annotations

import math
import re


def number(value) -> float | None:
    if value is None or str(value).strip() in {"", "--", "-", "N/A", "nan"}:
        return None
    text = str(value).strip().replace(",", "")
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        result = float(text)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def financial_period(year, quarter) -> str:
    y, q = number(year), number(quarter)
    if y is None or q is None or y != int(y) or q not in {1, 2, 3, 4}:
        return ""
    y = int(y) + 1911 if y < 300 else int(y)
    return f"{y}Q{int(q)}" if 1900 <= y <= 2100 else ""


def canonical_period(period) -> str:
    match = re.fullmatch(r"(\d{3,4})Q([1-4])", str(period or ""))
    return financial_period(*match.groups()) if match else ""


def update_period_history(history: dict, previous: dict, current: dict) -> dict:
    stocks = {sid: dict(periods) for sid, periods in history.get("stocks", {}).items() if isinstance(periods, dict)}
    # Migrate the prior latest snapshot without replacing its earlier period.
    for records in (previous, current):
        for sid, record in records.items():
            if not isinstance(record, dict):
                continue
            period = canonical_period(record.get("financial_period"))
            if period:
                existing = stocks.setdefault(sid, {}).get(period, {})
                merged = dict(existing)
                merged.update({key: value for key, value in record.items() if value is not None or key not in existing})
                merged["financial_period"] = period
                stocks[sid][period] = merged
    return {"schema": "mops_period_history_v2", "stocks": {
        sid: {period: periods[period] for period in sorted(periods)[-16:]}
        for sid, periods in stocks.items()
    }}


def derive_financial_metrics(record: dict, periods: dict) -> dict:
    period = canonical_period(record.get("financial_period"))
    result = dict(record)
    result.update({
        "eps_basis": "ytd", "previous_eps": None, "eps_change_pct": None,
        "eps_comparison_available": False, "eps_comparison_basis": "missing_same_period",
        "eps_turnaround_confirmed": False, "annualized_eps": None,
        "eps_ttm": None, "eps_ttm_basis": "missing", "roe": None, "roe_basis": "missing",
    })
    if not period:
        result["financial_data_status"] = "period_unknown"
        return result
    year, quarter = int(period[:4]), int(period[-1])
    eps = number(record.get("eps"))
    result["financial_period"] = period
    result["financial_data_status"] = "available" if eps is not None else "missing_eps"
    if eps is not None:
        result["annualized_eps"] = round(eps * 4 / quarter, 6)
        result["eps_annualization_factor"] = 4 / quarter
    prior = periods.get(f"{year - 1}Q{quarter}", {})
    prior_eps = number(prior.get("eps"))
    if eps is not None and prior_eps is not None:
        result.update({"previous_eps": prior_eps, "eps_comparison_available": True,
                       "eps_comparison_basis": "same_period_yoy", "previous_financial_period": f"{year - 1}Q{quarter}"})
        if prior_eps > 0:
            result["eps_change_pct"] = round((eps / prior_eps - 1) * 100, 2)
        result["eps_turnaround_confirmed"] = prior_eps <= 0 < eps
    annual = periods.get(f"{year - 1}Q4", {})
    annual_eps = number(annual.get("eps"))
    if quarter == 4:
        result.update({"eps_ttm": eps, "eps_ttm_basis": "reported_annual"})
    elif eps is not None and prior_eps is not None and annual_eps is not None:
        # EPS denominators may differ between reports, so this bridge is an estimate.
        result.update({"eps_ttm": round(annual_eps + eps - prior_eps, 6), "eps_ttm_basis": "ytd_bridge_estimate"})

    use_parent = number(record.get("owner_net_income")) is not None and number(record.get("parent_equity")) is not None
    income_key, equity_key = ("owner_net_income", "parent_equity") if use_parent else ("net_income", "equity")
    income, equity = number(record.get(income_key)), number(record.get(equity_key))
    result["roe_scope"] = "parent" if use_parent else "consolidated"
    if equity is not None and equity <= 0:
        result["roe_basis"] = "non_positive_equity"
    if income is not None and equity is not None and equity > 0:
        beginning_equity = number(annual.get(equity_key))
        divisor = (equity + beginning_equity) / 2 if beginning_equity is not None and beginning_equity > 0 else equity
        result["roe"] = round(income * 4 / quarter / divisor * 100, 2)
        result["roe_basis"] = "annualized_ytd_average_equity_estimate" if divisor != equity else "annualized_ytd_end_equity_estimate"
        prior_income, annual_income, prior_equity = (number(prior.get(income_key)), number(annual.get(income_key)), number(prior.get(equity_key)))
        if quarter != 4 and prior_income is not None and annual_income is not None and prior_equity is not None and prior_equity > 0:
            result["roe"] = round((annual_income + income - prior_income) / ((equity + prior_equity) / 2) * 100, 2)
            result["roe_basis"] = "ttm_average_equity"
    return result


def fill_valuation_estimates(row: dict) -> bool:
    close, pe, pb = number(row.get("close")), number(row.get("pe_ratio")), number(row.get("pbr"))
    changed = False
    if close is None or close <= 0:
        return False
    if pe is None or pe <= 0:
        eps_ttm = number(row.get("eps_ttm"))
        eps_annualized = number(row.get("annualized_eps"))
        denominator = eps_ttm if eps_ttm is not None else eps_annualized
        if row.get("official_per_missing") and eps_ttm is None:
            denominator = None
        if denominator is not None and denominator > 0:
            row["pe_ratio"] = round(close / denominator, 2)
            row["pe_ratio_estimated"] = row.get("eps_ttm_basis") != "reported_annual"
            row["per_source"] = "MOPS_TTM_EPS" if eps_ttm is not None else "MOPS_YTD_EPS_ANNUALIZED"
            changed = True
    if pb is None or pb <= 0:
        book = number(row.get("book_value_per_share"))
        if book is not None and book > 0:
            row["pbr"] = round(close / book, 2)
            row["pbr_source"] = "MOPS_BVPS"
            changed = True
    sources = [str(row.get(key)) for key in ("per_source", "pbr_source") if row.get(key)]
    if sources:
        row["valuation_source"] = "+".join(dict.fromkeys(sources))
    return changed
