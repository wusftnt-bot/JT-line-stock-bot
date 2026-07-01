from __future__ import annotations

import argparse
from collections import Counter
import csv
import io
import json
import math
import os
import re
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TWSE_REVENUE_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L"
MOPS_REVENUE_CSV_URL = "https://mopsfin.twse.com.tw/opendata/t187ap05_L.csv"
MOPS_REVENUE_OTC_CSV_URL = "https://mopsfin.twse.com.tw/opendata/t187ap05_O.csv"
MOPS_FINANCIAL_SUMMARY_CSV_URL = "https://mopsfin.twse.com.tw/opendata/t187ap14_L.csv"
MOPS_FINANCIAL_SUMMARY_OTC_CSV_URL = "https://mopsfin.twse.com.tw/opendata/t187ap14_O.csv"
MOPS_BALANCE_SHEET_CSV_URL = "https://mopsfin.twse.com.tw/opendata/t187ap07_L_ci.csv"
MOPS_BALANCE_SHEET_OTC_CSV_URL = "https://mopsfin.twse.com.tw/opendata/t187ap07_O_ci.csv"
TWSE_INCOME_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci"
TWSE_STOCK_DAY_URL = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TWSE_ATTENTION_URL = "https://openapi.twse.com.tw/v1/announcement/notice"
TPEX_DISPOSAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_disposal_information"
TPEX_ESB_DISPOSAL_URL = "https://www.tpex.org.tw/openapi/v1/tpex_esb_disposal_information"
FINMIND_DATA_URL = "https://api.finmindtrade.com/api/v4/data"
GEMINI_GENERATE_CONTENT_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
LINE_BROADCAST_URL = "https://api.line.me/v2/bot/message/broadcast"
DAILY_FINANCE_REPORT_URL = os.getenv("DAILY_FINANCE_REPORT_URL", "https://wusftnt-bot.github.io/daily-finance-report/")
REPORT_DIR = Path("reports")
DATA_CACHE_DIR = Path(os.getenv("AI_STOCK_DATA_CACHE_DIR", ".cache/line-ai-stock-bot"))
THEME_MAP_PATH = Path("data") / "industry_theme_map.csv"
TAIPEI_TZ = timezone(timedelta(hours=8))
LINE_TEXT_LIMIT = 5000
DATA_SOURCE_STATUS = {
    "revenue": "unknown",
    "margins": "unknown",
    "price": "TWSE",
    "institutional": "TWSE",
    "margin": "TWSE",
    "revenue_filter": "unknown",
    "candidate_pool": "unknown",
    "industry_rs": "unknown",
    "valuation": "unknown",
    "financial_quality": "unknown",
    "cash_flow": "unknown",
    "data_completeness": "unknown",
    "industry_balance": "unknown",
    "top_quality": "unknown",
    "top_quality_reasons": "unknown",
    "event_risk": "unknown",
    "strategy_tracker": "unknown",
    "market_score": "unknown",
    "industry_momentum": "unknown",
    "exit_alerts": "unknown",
    "gemini_review": "disabled",
}

THEME_BY_STOCK_ID: dict[str, str] | None = None


class RunDeadlineExceeded(TimeoutError):
    pass


def install_run_deadline() -> None:
    seconds = env_int("AI_STOCK_MAX_RUNTIME_SEC", 0)
    if seconds <= 0 or not hasattr(signal, "SIGALRM"):
        return

    def _raise_deadline(_signum: int, _frame: Any) -> None:
        raise RunDeadlineExceeded(f"AI stock bot exceeded runtime deadline {seconds}s")

    signal.signal(signal.SIGALRM, _raise_deadline)
    signal.alarm(seconds)

RENAME_MAP = {
    "公司代號": "stock_id",
    "公司名稱": "stock_name",
    "營業收入-當月營收": "monthly_revenue",
    "營業收入-上月營收": "last_month_revenue",
    "營業收入-去年當月營收": "last_year_month_revenue",
    "營業收入-上月比較增減(%)": "mom",
    "營業收入-去年同月增減(%)": "yoy",
    "累計營業收入-當月累計營收": "acc_revenue",
    "累計營業收入-去年累計營收": "last_year_acc_revenue",
    "累計營業收入-前期比較增減(%)": "acc_yoy",
}

KEEP_COLS = [
    "rank",
    "opportunity_rank",
    "stock_id",
    "stock_name",
    "candidate_source",
    "institutional_candidate_score",
    "institutional_candidate_reason",
    "market_type",
    "industry_category",
    "industry_theme",
    "model_version",
    "legacy_score",
    "total_score",
    "fundamental_score",
    "valuation_score",
    "technical_score",
    "chip_score",
    "industry_score",
    "eps_acceleration_score",
    "eps_acceleration_note",
    "eps_change_pct",
    "risk_penalty",
    "opportunity_score",
    "opportunity_reason",
    "industry_priority",
    "event_risk_level",
    "event_risk_flags",
    "event_risk_reason",
    "model_grade",
    "entry_plan",
    "exit_plan",
    "eps",
    "previous_eps",
    "net_income",
    "equity",
    "roe",
    "pe_ratio",
    "pbr",
    "valuation_source",
    "operating_cash_flow",
    "free_cash_flow",
    "cash_flow_score",
    "cash_flow_quality",
    "accumulation_score",
    "industry_relative_score",
    "market_relative_score",
    "industry_peer_count",
    "top_quality_pass",
    "top_quality_reason",
    "revenue_anomaly",
    "revenue_anomaly_reason",
    "monthly_revenue",
    "mom",
    "yoy",
    "acc_yoy",
    "gross_margin",
    "operating_margin",
    "net_margin",
    "prev_gross_margin",
    "prev_operating_margin",
    "prev_net_margin",
    "triple_margin_up",
    "close",
    "ma5",
    "ma20",
    "ma60",
    "volume_ratio",
    "latest_volume",
    "volume_20d_avg",
    "turnover_20d_avg",
    "latest_trade_date",
    "institutional_trade_date",
    "margin_trade_date",
    "price_change_1d",
    "price_change_20d",
    "break_120d_high",
    "foreign_5d_sum",
    "foreign_5d_positive_days",
    "trust_5d_sum",
    "trust_5d_positive_days",
    "foreign_10d_sum",
    "foreign_10d_positive_days",
    "trust_10d_sum",
    "trust_10d_positive_days",
    "latest_foreign_net",
    "latest_trust_net",
    "latest_institutional_net",
    "prior_institutional_net",
    "recent_2d_institutional_net",
    "institutional_10d_sum",
    "institutional_20d_sum",
    "radar_exclusion_reason",
    "institutional_20d_avg_volume_ratio",
    "foreign_prior_15d_sum",
    "foreign_reversal",
    "margin_balance",
    "margin_buy_sell",
    "margin_risk",
    "low_liquidity",
    "quiet_accumulation",
    "exit_alert_rank",
    "exit_alert_reasons",
    "history_status",
    "consecutive_days",
]

StockRow = dict[str, Any]


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return float(value)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return int(value)


def env_str(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def clean_number(value: object) -> float:
    if value is None:
        return 0.0
    normalized = str(value).strip()
    normalized = normalized.replace(",", "").replace("--", "0").replace("－", "0")
    normalized = normalized.replace("%", "").replace("(", "-").replace(")", "")
    if normalized in {"", "-", "—", "nan", "None"}:
        return 0.0
    try:
        return float(normalized)
    except ValueError:
        return 0.0


def clip(value: float, lower: float, upper: float) -> float:
    return min(max(value, lower), upper)


def request_json(url: str, params: dict[str, str] | None = None, timeout: int = 60) -> Any:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_with_retry(url: str, params: dict[str, str] | None = None, tries: int = 3, timeout: int = 60) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            return request_json(url, params=params, timeout=timeout)
        except Exception as error:
            last_error = error
            if attempt < tries:
                time.sleep(5 * attempt)
    raise RuntimeError(f"Fetch failed after {tries} attempts: {url}") from last_error


def ensure_row_list(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        return [payload]
    return []


def extract_common_stock_id(row: dict[str, Any]) -> str:
    preferred_keys = (
        "Code",
        "SecuritiesCompanyCode",
        "證券代號",
        "股票代號",
        "代號",
        "StockCode",
        "stock_id",
    )
    values: list[str] = []
    for key in preferred_keys:
        if key in row:
            values.append(str(row.get(key) or ""))
    values.extend(str(value or "") for value in row.values())
    for value in values:
        matches = re.findall(r"(?<!\d)(\d{4,6})(?!\d)", value)
        for match in matches:
            if len(match) == 6 and match.startswith("1"):
                month = int(match[3:5])
                day = int(match[5:6] or "0")
                if 1 <= month <= 12 and day <= 3:
                    continue
            stock_id = match[:4]
            if stock_id and stock_id != "0000":
                return stock_id
    return ""


def add_event_risk(
    event_map: dict[str, StockRow],
    stock_id: str,
    flag: str,
    level: str,
    reason: str,
) -> None:
    if not stock_id:
        return
    current = event_map.setdefault(
        stock_id,
        {
            "event_risk_level": "",
            "event_risk_flags": "",
            "event_risk_reason": "",
        },
    )
    flags = [item for item in str(current.get("event_risk_flags") or "").split(",") if item]
    reasons = [item for item in str(current.get("event_risk_reason") or "").split(";") if item]
    if flag not in flags:
        flags.append(flag)
    if reason and reason not in reasons:
        reasons.append(reason)
    if level == "disposition" or current.get("event_risk_level") != "disposition":
        current["event_risk_level"] = level
    current["event_risk_flags"] = ",".join(flags)
    current["event_risk_reason"] = ";".join(reasons[:3])


def fetch_event_risk_map() -> dict[str, StockRow]:
    event_map: dict[str, StockRow] = {}
    statuses: list[str] = []
    sources = [
        ("twse_attention", TWSE_ATTENTION_URL, "attention", "attention_stock"),
        ("tpex_disposal", TPEX_DISPOSAL_URL, "disposition", "disposition_stock"),
        ("tpex_esb_disposal", TPEX_ESB_DISPOSAL_URL, "disposition", "disposition_stock"),
    ]
    for source_name, url, level, flag in sources:
        try:
            payload = fetch_with_retry(url, tries=2, timeout=30)
            rows = ensure_row_list(payload)
            count = 0
            for row in rows:
                stock_id = extract_common_stock_id(row)
                if not stock_id:
                    continue
                reason = str(
                    row.get("DispositionReasons")
                    or row.get("TradingInfoForAttention")
                    or row.get("NumberOfAnnouncement")
                    or source_name
                ).strip()
                add_event_risk(event_map, stock_id, flag, level, reason[:80])
                count += 1
            statuses.append(f"{source_name}={count}")
        except Exception as error:
            statuses.append(f"{source_name}=unavailable:{type(error).__name__}")
    DATA_SOURCE_STATUS["event_risk"] = " ".join(statuses) if statuses else "unknown"
    return event_map


def cache_path(name: str) -> Path:
    return DATA_CACHE_DIR / f"{name}.json"


def write_cache(name: str, payload: Any) -> None:
    DATA_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path(name).write_text(
        json.dumps({"saved_at": datetime.now(TAIPEI_TZ).isoformat(), "payload": payload}, ensure_ascii=False),
        encoding="utf-8",
    )


def read_cache(name: str) -> Any | None:
    path = cache_path(name)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("payload")
    except Exception:
        return None


def read_cache_record(name: str) -> dict[str, Any] | None:
    path = cache_path(name)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        return record if isinstance(record, dict) else None
    except Exception:
        return None


def cache_age_hours(record: dict[str, Any] | None) -> float | None:
    if not record:
        return None
    saved_at = str(record.get("saved_at") or "")
    if not saved_at:
        return None
    try:
        saved_dt = datetime.fromisoformat(saved_at)
        if saved_dt.tzinfo is None:
            saved_dt = saved_dt.replace(tzinfo=TAIPEI_TZ)
        return max((datetime.now(TAIPEI_TZ) - saved_dt.astimezone(TAIPEI_TZ)).total_seconds() / 3600, 0.0)
    except Exception:
        return None


def format_cache_age(age_hours: float | None) -> str:
    if age_hours is None:
        return "age=unknown"
    if age_hours < 1:
        return f"age={age_hours * 60:.0f}m"
    return f"age={age_hours:.1f}h"


def is_l_plus_o_payload(payload: Any) -> bool:
    if not isinstance(payload, list):
        return False
    markets = {str(row.get("__market_type") or row.get("market_type") or "") for row in payload if isinstance(row, dict)}
    return "listed" in markets and "otc" in markets


def cache_is_fresh_enough(record: dict[str, Any] | None) -> bool:
    age = cache_age_hours(record)
    if age is None:
        return False
    return age <= env_float("AI_STOCK_REVENUE_CACHE_MAX_AGE_HOURS", 36.0)


def request_finmind(dataset: str, params: dict[str, str], timeout: int = 60) -> list[dict[str, Any]]:
    query = {"dataset": dataset, **params}
    token = os.getenv("FINMIND_TOKEN", "").strip()
    headers = {"User-Agent": "Mozilla/5.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = f"{FINMIND_DATA_URL}?{urlencode(query)}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if int(payload.get("status", 0)) != 200:
        raise RuntimeError(f"FinMind {dataset} failed: {payload.get('msg')}")
    data = payload.get("data", [])
    return data if isinstance(data, list) else []


def fetch_finmind_price_history(stock_id: str, days: int = 240) -> list[dict[str, float]]:
    start_date = (datetime.now(TAIPEI_TZ).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = request_finmind("TaiwanStockPrice", {"data_id": stock_id, "start_date": start_date}, timeout=45)
    records = [
        {
            "date": str(row.get("date", "")),
            "close": clean_number(row.get("close")),
            "volume": clean_number(row.get("Trading_Volume")),
        }
        for row in rows
        if clean_number(row.get("close")) > 0
    ]
    return sorted(records, key=lambda item: item["date"])


def fetch_finmind_institutional_records(stock_id: str, days: int = 45) -> list[dict[str, float]]:
    start_date = (datetime.now(TAIPEI_TZ).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = request_finmind(
        "TaiwanStockInstitutionalInvestorsBuySell",
        {"data_id": stock_id, "start_date": start_date},
        timeout=45,
    )
    grouped: dict[str, dict[str, float]] = {}
    for row in rows:
        date = str(row.get("date", ""))
        if not date:
            continue
        item = grouped.setdefault(date, {"date": float(date.replace("-", "")), "foreign": 0.0, "trust": 0.0})
        net = clean_number(row.get("buy")) - clean_number(row.get("sell"))
        name = str(row.get("name", ""))
        if name in {"Foreign_Investor", "Foreign_Dealer_Self"}:
            item["foreign"] += net
        elif name == "Investment_Trust":
            item["trust"] += net
    return sorted(grouped.values(), key=lambda item: item["date"], reverse=True)


def fetch_finmind_margin_record(stock_id: str, days: int = 14) -> dict[str, float]:
    start_date = (datetime.now(TAIPEI_TZ).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = request_finmind("TaiwanStockMarginPurchaseShortSale", {"data_id": stock_id, "start_date": start_date}, timeout=45)
    if not rows:
        return {}
    latest = sorted(rows, key=lambda row: str(row.get("date", "")))[-1]
    return {
        "margin_balance": clean_number(latest.get("MarginPurchaseTodayBalance")) * 1000,
        "margin_buy_sell": (clean_number(latest.get("MarginPurchaseBuy")) - clean_number(latest.get("MarginPurchaseSell"))) * 1000,
    }


def fetch_finmind_per_record(stock_id: str, days: int = 45) -> dict[str, float]:
    if not os.getenv("FINMIND_TOKEN", "").strip():
        return {}
    start_date = (datetime.now(TAIPEI_TZ).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = request_finmind("TaiwanStockPER", {"data_id": stock_id, "start_date": start_date}, timeout=30)
    if not rows:
        return {}
    latest = sorted(rows, key=lambda row: str(row.get("date", "")))[-1]
    return {
        "pe_ratio": clean_number(latest.get("PER") or latest.get("pe") or latest.get("price_earning_ratio")),
        "pbr": clean_number(latest.get("PBR") or latest.get("pbr") or latest.get("price_book_ratio")),
        "valuation_source": "FinMind",
    }


def fill_fallback_valuation(row: StockRow) -> bool:
    pe_ratio = float(row.get("pe_ratio") or 0.0)
    pbr = float(row.get("pbr") or 0.0)
    close = float(row.get("close") or 0.0)
    eps = float(row.get("eps") or 0.0)
    roe = float(row.get("roe") or 0.0)
    changed = False

    if pe_ratio <= 0 and close > 0 and eps > 0:
        annualized_eps = eps * env_float("AI_STOCK_VALUATION_EPS_ANNUALIZE_FACTOR", 4.0)
        if annualized_eps > 0:
            pe_ratio = close / annualized_eps
            row["pe_ratio"] = round(pe_ratio, 2)
            changed = True
    if pbr <= 0 and pe_ratio > 0 and roe > 0:
        pbr = pe_ratio * roe / 100
        row["pbr"] = round(pbr, 2)
        changed = True
    if changed:
        row["valuation_source"] = str(row.get("valuation_source") or "MOPS_EPS_ROE_FALLBACK")
    elif pe_ratio > 0 or pbr > 0:
        row["valuation_source"] = str(row.get("valuation_source") or "provided")
    return changed


def fetch_finmind_cash_flow_record(stock_id: str, days: int = 430) -> dict[str, float]:
    if not os.getenv("FINMIND_TOKEN", "").strip():
        return {}
    start_date = (datetime.now(TAIPEI_TZ).date() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = request_finmind("TaiwanStockCashFlowsStatement", {"data_id": stock_id, "start_date": start_date}, timeout=45)
    if not rows:
        return {}

    latest_date = max(str(row.get("date", "")) for row in rows)
    latest_rows = [row for row in rows if str(row.get("date", "")) == latest_date]

    def sum_by_keywords(keywords: list[str]) -> float:
        total = 0.0
        for row in latest_rows:
            text = " ".join(str(value) for value in row.values())
            if any(keyword in text for keyword in keywords):
                total += clean_number(row.get("value") or row.get("amount"))
        return total

    operating_cash_flow = sum_by_keywords(["營業活動", "Operating"])
    capex = abs(sum_by_keywords(["取得不動產", "購置", "capital expenditure", "Property"]))
    free_cash_flow = operating_cash_flow - capex if operating_cash_flow else 0.0
    return {
        "operating_cash_flow": round(operating_cash_flow, 0),
        "free_cash_flow": round(free_cash_flow, 0),
    }


def convert_finmind_revenue_rows(rows: list[dict[str, Any]]) -> list[StockRow]:
    target_to_source = {target: source for source, target in RENAME_MAP.items()}
    by_stock: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        stock_id = str(row.get("stock_id", "")).strip()
        if stock_id and str(row.get("country", "Taiwan")) == "Taiwan":
            by_stock.setdefault(stock_id, []).append(row)

    converted: list[StockRow] = []
    for stock_id, items in by_stock.items():
        items.sort(key=lambda item: str(item.get("date", "")))
        latest = items[-1]
        latest_month = int(clean_number(latest.get("revenue_month")))
        latest_year = int(clean_number(latest.get("revenue_year")))
        latest_revenue = clean_number(latest.get("revenue"))
        last_month = items[-2] if len(items) >= 2 else {}
        same_month_last_year = next(
            (
                item
                for item in reversed(items[:-1])
                if int(clean_number(item.get("revenue_year"))) == latest_year - 1
                and int(clean_number(item.get("revenue_month"))) == latest_month
            ),
            {},
        )
        current_acc = sum(
            clean_number(item.get("revenue"))
            for item in items
            if int(clean_number(item.get("revenue_year"))) == latest_year
            and int(clean_number(item.get("revenue_month"))) <= latest_month
        )
        prior_acc = sum(
            clean_number(item.get("revenue"))
            for item in items
            if int(clean_number(item.get("revenue_year"))) == latest_year - 1
            and int(clean_number(item.get("revenue_month"))) <= latest_month
        )
        last_month_revenue = clean_number(last_month.get("revenue"))
        last_year_revenue = clean_number(same_month_last_year.get("revenue"))
        mom = ((latest_revenue / last_month_revenue) - 1) * 100 if last_month_revenue else 0.0
        yoy = ((latest_revenue / last_year_revenue) - 1) * 100 if last_year_revenue else 0.0
        acc_yoy = ((current_acc / prior_acc) - 1) * 100 if prior_acc else 0.0
        converted.append(
            {
                target_to_source["stock_id"]: stock_id,
                target_to_source["stock_name"]: stock_id,
                target_to_source["monthly_revenue"]: latest_revenue,
                target_to_source["last_month_revenue"]: last_month_revenue,
                target_to_source["last_year_month_revenue"]: last_year_revenue,
                target_to_source["mom"]: round(mom, 2),
                target_to_source["yoy"]: round(yoy, 2),
                target_to_source["acc_revenue"]: current_acc,
                target_to_source["last_year_acc_revenue"]: prior_acc,
                target_to_source["acc_yoy"]: round(acc_yoy, 2),
                "__market_type": "unknown",
            }
        )
    return converted


def fetch_finmind_monthly_revenue() -> list[StockRow]:
    start_date = (datetime.now(TAIPEI_TZ).date() - timedelta(days=430)).strftime("%Y-%m-%d")
    rows = request_finmind("TaiwanStockMonthRevenue", {"start_date": start_date}, timeout=90)
    converted = convert_finmind_revenue_rows(rows)
    if not converted:
        raise RuntimeError("FinMind monthly revenue returned no usable rows.")
    return converted


def validate_revenue_columns(rows: list[StockRow], source: str) -> None:
    if not rows:
        raise RuntimeError(f"{source} revenue returned no rows")
    missing_cols = [column for column in RENAME_MAP if column not in rows[0]]
    if missing_cols:
        available = ", ".join(str(key) for key in rows[0].keys())
        raise ValueError(f"{source} revenue missing columns {missing_cols}. Available: {available}")


def fetch_twse_monthly_revenue() -> list[StockRow]:
    errors: list[str] = []
    cache_record = read_cache_record("monthly_revenue_raw")
    cached = cache_record.get("payload") if cache_record else None
    cache_age = cache_age_hours(cache_record)
    try:
        rows = fetch_mops_monthly_revenue_csv()
        validate_revenue_columns(rows, "MOPS L+O")
        DATA_SOURCE_STATUS["revenue"] = f"MOPS L+O fresh rows={len(rows)}"
        write_cache("monthly_revenue_raw", rows)
        return rows
    except Exception as error:
        errors.append(f"MOPS L+O={type(error).__name__}:{str(error)[:80]}")
    if cached and is_l_plus_o_payload(cached):
        if cache_is_fresh_enough(cache_record):
            try:
                validate_revenue_columns(cached, "Cache L+O")
                DATA_SOURCE_STATUS["revenue"] = f"Cache L+O fresh {format_cache_age(cache_age)} mops_error={errors[-1]}"
                return cached
            except Exception as error:
                errors.append(f"Cache L+O fresh invalid={type(error).__name__}")
        DATA_SOURCE_STATUS["revenue"] = f"Cache L+O stale {format_cache_age(cache_age)} trying backups"
        errors.append(f"Cache L+O stale {format_cache_age(cache_age)}")
    try:
        rows = fetch_finmind_monthly_revenue()
        validate_revenue_columns(rows, "FinMind")
        DATA_SOURCE_STATUS["revenue"] = "FinMind backup"
        write_cache("monthly_revenue_raw", rows)
        return rows
    except Exception as error:
        errors.append(f"FinMind={type(error).__name__}")
    if cached and is_l_plus_o_payload(cached):
        try:
            validate_revenue_columns(cached, "Cache L+O")
            DATA_SOURCE_STATUS["revenue"] = f"Cache L+O stale-used {format_cache_age(cache_age)} backups_failed"
            return cached
        except Exception as error:
            errors.append(f"Cache L+O stale invalid={type(error).__name__}")
    try:
        rows = fetch_with_retry(TWSE_REVENUE_URL)
        for row in rows if isinstance(rows, list) else []:
            row["__market_type"] = "listed"
        validate_revenue_columns(rows, "TWSE listed-only")
        DATA_SOURCE_STATUS["revenue"] = f"TWSE listed-only degraded errors={' | '.join(errors[:3])}"
        write_cache("monthly_revenue_listed_raw", rows)
        return rows
    except Exception as error:
        errors.append(f"TWSE={type(error).__name__}")
    if cached:
        try:
            validate_revenue_columns(cached, "Cache any")
            DATA_SOURCE_STATUS["revenue"] = f"Cache any stale-used {format_cache_age(cache_age)} all_sources_failed"
            return cached
        except Exception as error:
            errors.append(f"Cache any invalid={type(error).__name__}")
    raise RuntimeError(f"Monthly revenue unavailable; {'; '.join(errors)}")


def fetch_mops_monthly_revenue_csv_url(url: str, market_type: str) -> list[StockRow]:
    tries = env_int("AI_STOCK_MOPS_RETRY", 4)
    timeout = env_int("AI_STOCK_MOPS_TIMEOUT_SEC", 45)
    last_error: Exception | None = None
    for attempt in range(1, tries + 1):
        try:
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(request, timeout=timeout) as response:
                text = response.read().decode("utf-8-sig", errors="replace")
            rows = list(csv.DictReader(io.StringIO(text)))
            if not rows:
                raise RuntimeError(f"MOPS {market_type} CSV returned no rows")
            for row in rows:
                row["__market_type"] = market_type
            return rows
        except Exception as error:
            last_error = error
            if attempt < tries:
                time.sleep(min(3 * attempt, 12))
    raise RuntimeError(f"MOPS {market_type} CSV failed after {tries} tries") from last_error


def fetch_mops_monthly_revenue_csv() -> list[StockRow]:
    listed = fetch_mops_monthly_revenue_csv_url(MOPS_REVENUE_CSV_URL, "listed")
    otc = fetch_mops_monthly_revenue_csv_url(MOPS_REVENUE_OTC_CSV_URL, "otc")
    return listed + otc


def fetch_mops_csv_rows(url: str, market_type: str) -> list[StockRow]:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=env_int("AI_STOCK_MOPS_TIMEOUT_SEC", 45)) as response:
        text = response.read().decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    for row in rows:
        row["__market_type"] = market_type
    return rows


def row_value_by_keywords(row: dict[str, Any], keywords: list[str]) -> Any:
    for key, value in row.items():
        key_text = normalize_field_name(key)
        if all(keyword in key_text for keyword in keywords):
            return value
    return None


def fetch_mops_financial_quality_map() -> dict[str, dict[str, float]]:
    previous_quality = read_cache("financial_quality_raw") or {}
    try:
        summary_rows = fetch_mops_csv_rows(MOPS_FINANCIAL_SUMMARY_CSV_URL, "listed") + fetch_mops_csv_rows(
            MOPS_FINANCIAL_SUMMARY_OTC_CSV_URL,
            "otc",
        )
        balance_rows = fetch_mops_csv_rows(MOPS_BALANCE_SHEET_CSV_URL, "listed") + fetch_mops_csv_rows(
            MOPS_BALANCE_SHEET_OTC_CSV_URL,
            "otc",
        )
    except Exception as error:
        DATA_SOURCE_STATUS["financial_quality"] = f"MOPS unavailable {type(error).__name__}"
        return {}

    balance_by_stock: dict[str, dict[str, Any]] = {}
    for row in balance_rows:
        stock_id = str(row_value_by_keywords(row, ["公司", "代"]) or "").strip()
        if stock_id:
            balance_by_stock[stock_id] = row

    quality: dict[str, dict[str, float]] = {}
    for row in summary_rows:
        stock_id = str(row_value_by_keywords(row, ["公司", "代"]) or "").strip()
        if not stock_id:
            continue
        net_income = clean_number(row_value_by_keywords(row, ["稅", "淨", "利"]))
        eps = clean_number(row_value_by_keywords(row, ["每股", "盈"]))
        balance = balance_by_stock.get(stock_id, {})
        equity = clean_number(row_value_by_keywords(balance, ["權益", "總計"]))
        roe = round(net_income * 4 / equity * 100, 2) if equity > 0 else 0.0
        previous = previous_quality.get(stock_id, {}) if isinstance(previous_quality, dict) else {}
        previous_eps = clean_number(previous.get("eps")) if isinstance(previous, dict) else 0.0
        eps_change_pct = ((eps / previous_eps) - 1) * 100 if previous_eps > 0 and eps != previous_eps else 0.0
        quality[stock_id] = {
            "eps": eps,
            "previous_eps": previous_eps,
            "eps_change_pct": round(eps_change_pct, 2),
            "net_income": net_income,
            "equity": equity,
            "roe": roe,
        }

    DATA_SOURCE_STATUS["financial_quality"] = f"MOPS EPS/ROE rows={len(quality)}"
    write_cache("financial_quality_raw", quality)
    return quality


def normalize_revenue(rows: list[StockRow]) -> list[StockRow]:
    if not rows:
        return []

    validate_revenue_columns(rows, "Revenue")

    normalized_rows: list[StockRow] = []
    for row in rows:
        normalized = {target: row[source] for source, target in RENAME_MAP.items()}
        normalized["stock_id"] = str(normalized["stock_id"]).strip()
        normalized["stock_name"] = str(normalized["stock_name"]).strip()
        normalized["market_type"] = str(row.get("__market_type") or row.get("market_type") or "listed").strip()
        normalized["industry_category"] = str(row.get("產業別") or row.get("industry_category") or "").strip()
        for col in ["monthly_revenue", "mom", "yoy", "acc_revenue", "acc_yoy"]:
            normalized[col] = clean_number(normalized[col])
        normalized_rows.append(normalized)

    return normalized_rows


def calculate_revenue_score(row: StockRow) -> float:
    return round(
        clip(float(row["yoy"]), 0, 100) * 0.45
        + clip(float(row["acc_yoy"]), 0, 80) * 0.35
        + clip(float(row["mom"]), 0, 50) * 0.20,
        2,
    )


def median(values: list[float]) -> float:
    clean = sorted(value for value in values if math.isfinite(value))
    if not clean:
        return 0.0
    middle = len(clean) // 2
    if len(clean) % 2:
        return clean[middle]
    return (clean[middle - 1] + clean[middle]) / 2


def load_theme_map() -> dict[str, str]:
    global THEME_BY_STOCK_ID
    if THEME_BY_STOCK_ID is not None:
        return THEME_BY_STOCK_ID

    mapping: dict[str, str] = {}
    if THEME_MAP_PATH.exists():
        try:
            with THEME_MAP_PATH.open("r", encoding="utf-8-sig", newline="") as file:
                for row in csv.DictReader(file):
                    stock_id = str(row.get("stock_id") or "").strip()
                    theme = str(row.get("theme") or "").strip()
                    if stock_id and theme:
                        mapping[stock_id] = theme
        except Exception:
            mapping = {}

    THEME_BY_STOCK_ID = mapping
    return mapping


def classify_industry_theme(row: StockRow) -> str:
    stock_id = str(row.get("stock_id") or "")
    theme_map = load_theme_map()
    if stock_id in theme_map:
        return theme_map[stock_id]
    industry = str(row.get("industry_category") or "").strip()
    name = str(row.get("stock_name") or "")
    text = f"{industry} {name}"
    if any(keyword in text for keyword in ["半導體", "記憶", "DRAM", "IC", "晶圓"]):
        return "semiconductor"
    if any(keyword in text for keyword in ["電腦", "週邊", "電子零組件", "光電", "通信", "資訊服務", "電子通路"]):
        return "electronics"
    if any(keyword in text for keyword in ["生技", "醫療", "製藥"]):
        return "biotech_healthcare"
    if any(keyword in text for keyword in ["營建", "建材", "不動產"]):
        return "construction_property"
    return industry or "unknown"


def detect_revenue_anomaly(row: StockRow) -> tuple[bool, str]:
    if not env_bool("AI_STOCK_REVENUE_ANOMALY_FILTER", True):
        return False, ""

    yoy = float(row.get("yoy") or 0.0)
    mom = float(row.get("mom") or 0.0)
    acc_yoy = float(row.get("acc_yoy") or 0.0)
    monthly_revenue = float(row.get("monthly_revenue") or 0.0)
    last_year_revenue = float(row.get("last_year_month_revenue") or 0.0)
    last_month_revenue = float(row.get("last_month_revenue") or 0.0)

    max_yoy = env_float("AI_STOCK_MAX_YOY", 350.0)
    max_mom = env_float("AI_STOCK_MAX_MOM", 180.0)
    hard_max_yoy = env_float("AI_STOCK_HARD_MAX_YOY", 800.0)
    min_base = env_float("AI_STOCK_MIN_REVENUE_BASE", 50000.0)
    min_monthly_revenue = env_float("AI_STOCK_MIN_MONTHLY_REVENUE", 80000.0)

    reasons = []
    if monthly_revenue < min_monthly_revenue:
        reasons.append("small_current_revenue")
    if yoy > hard_max_yoy:
        reasons.append("extreme_yoy")
    if yoy > max_yoy and last_year_revenue < min_base:
        reasons.append("low_base_yoy")
    if mom > max_mom and last_month_revenue < min_base:
        reasons.append("low_base_mom")
    if yoy > max_yoy and acc_yoy < max(15.0, yoy * 0.25):
        reasons.append("monthly_spike_without_acc_support")
    if mom > max_mom and acc_yoy < mom * 0.50:
        reasons.append("mom_spike_without_acc_support")
    if mom > max_mom and yoy < mom * 0.75:
        reasons.append("mom_spike_above_yoy_trend")

    return bool(reasons), ",".join(reasons)


def apply_industry_relative_strength(rows: list[StockRow]) -> None:
    if not rows:
        DATA_SOURCE_STATUS["industry_rs"] = "empty"
        return

    market_price_median = median([float(row.get("price_change_20d") or 0.0) for row in rows])
    market_technical_median = median([float(row.get("technical_score") or 0.0) for row in rows])

    by_industry: dict[str, list[StockRow]] = {}
    for row in rows:
        industry = str(row.get("industry_category") or "unknown").strip() or "unknown"
        by_industry.setdefault(industry, []).append(row)

    for industry_rows in by_industry.values():
        industry_price_median = median([float(row.get("price_change_20d") or 0.0) for row in industry_rows])
        industry_technical_median = median([float(row.get("technical_score") or 0.0) for row in industry_rows])
        peer_count = len(industry_rows)
        for row in industry_rows:
            price_change = float(row.get("price_change_20d") or 0.0)
            technical = float(row.get("technical_score") or 0.0)
            industry_edge = price_change - industry_price_median
            market_edge = price_change - market_price_median
            technical_edge = technical - industry_technical_median

            score = 0.0
            if peer_count >= env_int("AI_STOCK_MIN_INDUSTRY_PEERS", 3):
                score += clip(industry_edge, -10, 10) * 0.35
                score += clip(technical_edge, -12, 12) * 0.25
            score += clip(market_edge, -12, 12) * 0.25
            score += clip(technical - market_technical_median, -12, 12) * 0.15

            row["industry_peer_count"] = peer_count
            row["market_relative_score"] = round(clip(market_edge, -20, 20), 2)
            row["industry_relative_score"] = round(clip(score, -8, 10), 2)

    DATA_SOURCE_STATUS["industry_rs"] = f"groups={len(by_industry)} market20d={market_price_median:.1f}%"


def calculate_market_score(rows: list[StockRow]) -> dict[str, Any]:
    score = 50.0
    notes: list[str] = []
    try:
        records = fetch_price_history(env_str("AI_STOCK_MARKET_PROXY", "0050"), months=7)
        closes = [row["close"] for row in records]
        volumes = [row["volume"] for row in records]
        if len(closes) >= 60:
            close = closes[-1]
            ma20 = moving_average(closes, 20)
            ma60 = moving_average(closes, 60)
            price_change_20d = ((close / closes[-21]) - 1) * 100 if closes[-21] else 0.0
            volume20 = moving_average(volumes, 20)
            volume_ratio = volumes[-1] / volume20 if volume20 else 0.0
            if close >= ma20:
                score += 12
                notes.append("proxy_above_ma20")
            else:
                score -= 12
                notes.append("proxy_below_ma20")
            if close >= ma60:
                score += 12
                notes.append("proxy_above_ma60")
            else:
                score -= 12
                notes.append("proxy_below_ma60")
            if price_change_20d > 5:
                score += 8
                notes.append("proxy_20d_positive")
            elif price_change_20d < -5:
                score -= 8
                notes.append("proxy_20d_negative")
            if volume_ratio >= 1.1 and price_change_20d > 0:
                score += 4
                notes.append("proxy_volume_confirm")
    except Exception as error:
        notes.append(f"proxy_unavailable:{type(error).__name__}")

    if rows:
        pass_count = sum(1 for row in rows if row.get("top_quality_pass"))
        c_or_b_count = sum(1 for row in rows if float(row.get("total_score") or 0.0) >= 65)
        median_20d = median([float(row.get("price_change_20d") or 0.0) for row in rows])
        if pass_count >= max(10, len(rows) * 0.25):
            score += 5
            notes.append("candidate_breadth_ok")
        else:
            score -= 5
            notes.append("candidate_breadth_weak")
        if c_or_b_count >= 5:
            score += 4
            notes.append("qualified_names_ok")
        if median_20d < -3:
            score -= 5
            notes.append("candidate_median_weak")

    score = round(clip(score, 0, 100), 1)
    if score < 40:
        regime = "defensive"
    elif score < 60:
        regime = "neutral"
    elif score < 75:
        regime = "constructive"
    else:
        regime = "risk_on"
    DATA_SOURCE_STATUS["market_score"] = f"score={score} regime={regime} notes={','.join(notes[:5])}"
    return {"score": score, "regime": regime, "notes": notes}


def summarize_industry_momentum(rows: list[StockRow]) -> None:
    by_theme: dict[str, list[StockRow]] = {}
    for row in rows:
        theme = str(row.get("industry_theme") or row.get("industry_category") or "unknown")
        by_theme.setdefault(theme, []).append(row)

    summaries = []
    for theme, theme_rows in by_theme.items():
        if len(theme_rows) < 2:
            continue
        price_20d = median([float(row.get("price_change_20d") or 0.0) for row in theme_rows])
        volume_ratio = median([float(row.get("volume_ratio") or 0.0) for row in theme_rows])
        inst_10d = sum(float(row.get("institutional_10d_sum") or 0.0) for row in theme_rows)
        summaries.append(
            {
                "theme": theme,
                "count": len(theme_rows),
                "price_20d": price_20d,
                "volume_ratio": volume_ratio,
                "inst_10d_lots": shares_to_lots(inst_10d),
                "score": price_20d + volume_ratio * 3 + clip(shares_to_lots(inst_10d) / 10000, -5, 5),
            }
        )

    summaries.sort(key=lambda item: item["score"], reverse=True)
    top = summaries[:3]
    if not top:
        DATA_SOURCE_STATUS["industry_momentum"] = "empty"
        return
    DATA_SOURCE_STATUS["industry_momentum"] = " | ".join(
        f"{item['theme']}:{item['price_20d']:.1f}%/{item['volume_ratio']:.2f}x/{item['inst_10d_lots']:.0f}lots"
        for item in top
    )


def select_industry_balanced_top(rows: list[StockRow], top_limit: int) -> list[StockRow]:
    max_per_industry = env_int("AI_STOCK_MAX_PER_INDUSTRY", 3)
    max_per_theme = env_int("AI_STOCK_MAX_PER_THEME", 2)
    if max_per_industry <= 0 and max_per_theme <= 0:
        return rows[:top_limit]

    selected: list[StockRow] = []
    industry_counts: dict[str, int] = {}
    theme_counts: dict[str, int] = {}

    def industry_key(row: StockRow) -> str:
        return str(row.get("industry_category") or "unknown").strip() or "unknown"

    def theme_key(row: StockRow) -> str:
        return str(row.get("industry_theme") or industry_key(row)).strip() or "unknown"

    def can_add(row: StockRow, enforce_industry: bool, enforce_theme: bool) -> bool:
        industry = industry_key(row)
        theme = theme_key(row)
        if enforce_industry and max_per_industry > 0 and industry_counts.get(industry, 0) >= max_per_industry:
            return False
        if enforce_theme and max_per_theme > 0 and theme_counts.get(theme, 0) >= max_per_theme:
            return False
        return True

    def add(row: StockRow) -> None:
        selected.append(row)
        industry = industry_key(row)
        theme = theme_key(row)
        industry_counts[industry] = industry_counts.get(industry, 0) + 1
        theme_counts[theme] = theme_counts.get(theme, 0) + 1

    for row in rows:
        if not can_add(row, enforce_industry=True, enforce_theme=True):
            continue
        add(row)
        if len(selected) >= top_limit:
            DATA_SOURCE_STATUS["industry_balance"] = format_industry_balance(industry_counts, theme_counts)
            return selected

    for row in rows:
        if row in selected or not can_add(row, enforce_industry=False, enforce_theme=True):
            continue
        add(row)
        if len(selected) >= top_limit:
            DATA_SOURCE_STATUS["industry_balance"] = format_industry_balance(industry_counts, theme_counts)
            return selected

    if env_bool("AI_STOCK_ALLOW_THEME_OVERFLOW", False):
        for row in rows:
            if row in selected:
                continue
            add(row)
            if len(selected) >= top_limit:
                break

    DATA_SOURCE_STATUS["industry_balance"] = format_industry_balance(industry_counts, theme_counts)
    return selected


def format_industry_balance(industry_counts: dict[str, int], theme_counts: dict[str, int]) -> str:
    top_industries = ",".join(f"{key}={value}" for key, value in sorted(industry_counts.items(), key=lambda item: item[1], reverse=True)[:3])
    top_themes = ",".join(f"{key}={value}" for key, value in sorted(theme_counts.items(), key=lambda item: item[1], reverse=True)[:3])
    return f"industries:{top_industries or 'none'} themes:{top_themes or 'none'}"


def opportunity_industry_priority(row: StockRow) -> tuple[float, str]:
    theme = str(row.get("industry_theme") or "")
    industry = str(row.get("industry_category") or "")
    name = str(row.get("stock_name") or "")
    text = f"{theme} {industry} {name}".lower()
    if any(
        keyword.lower() in text
        for keyword in [
            "ai",
            "semiconductor",
            "electronics",
            "memory",
            "storage",
            "通信",
            "通訊",
            "網通",
            "衛星",
            "機器人",
            "自動化",
            "電腦",
            "電子",
            "半導體",
            "光電",
            "pcb",
            "伺服器",
            "散熱",
        ]
    ):
        return 20.0, "AI/電子科技/通訊優先"
    if any(keyword in text for keyword in ["金融", "銀行", "保險", "金控", "證券"]):
        return 16.0, "金融優先"
    if any(keyword in text for keyword in ["食品", "百貨", "貿易", "生活", "消費", "觀光", "居家"]):
        return 14.0, "民生消費優先"
    if any(keyword in text for keyword in ["生技", "醫療", "製藥", "綠能", "電力", "能源"]):
        return 8.0, "次優先產業"
    return 4.0, "其他產業觀察"


def calculate_opportunity_score(row: StockRow) -> tuple[float, list[str]]:
    reasons: list[str] = []
    foreign_10d = float(row.get("foreign_10d_sum") or 0.0)
    trust_10d = float(row.get("trust_10d_sum") or 0.0)
    institutional_10d = float(row.get("institutional_10d_sum") or 0.0)
    institutional_volume_ratio = float(row.get("institutional_20d_avg_volume_ratio") or 0.0)
    foreign_reversal = bool(row.get("foreign_reversal"))
    trust_positive_days = int(row.get("trust_10d_positive_days") or 0)
    foreign_positive_days = int(row.get("foreign_10d_positive_days") or 0)
    yoy = float(row.get("yoy") or 0.0)
    mom = float(row.get("mom") or 0.0)
    acc_yoy = float(row.get("acc_yoy") or 0.0)
    gross_margin = float(row.get("gross_margin") or 0.0)
    operating_margin = float(row.get("operating_margin") or 0.0)
    roe = float(row.get("roe") or 0.0)
    price_change_20d = float(row.get("price_change_20d") or 0.0)
    price_change_1d = float(row.get("price_change_1d") or 0.0)
    volume_ratio = float(row.get("volume_ratio") or 0.0)
    close = float(row.get("close") or 0.0)
    ma20 = float(row.get("ma20") or 0.0)
    ma60 = float(row.get("ma60") or 0.0)

    institutional_score = 0.0
    if foreign_10d > 0:
        institutional_score += 8
        reasons.append("外資10日買超")
    if trust_10d > 0:
        institutional_score += 10
        reasons.append("投信10日買超")
    if foreign_10d > 0 and trust_10d > 0:
        institutional_score += 7
        reasons.append("外資投信同步偏買")
    if foreign_reversal:
        institutional_score += 6
        reasons.append("外資由賣轉買")
    if foreign_positive_days >= 6 or trust_positive_days >= 5:
        institutional_score += 4
        reasons.append("法人買超天數穩定")
    if institutional_volume_ratio >= 0.08:
        institutional_score += 5
        reasons.append("法人買超占量提高")
    institutional_score = clip(institutional_score, 0, 35)

    fundamental_score = 0.0
    if yoy >= 30:
        fundamental_score += 8
        reasons.append("營收年增強")
    if acc_yoy >= 20:
        fundamental_score += 7
        reasons.append("累計營收成長")
    if mom >= 0:
        fundamental_score += 3
    if row.get("triple_margin_up"):
        fundamental_score += 7
        reasons.append("三率同步改善")
    if gross_margin > 0 and operating_margin > 0:
        fundamental_score += 3
    if roe >= 12:
        fundamental_score += 5
        reasons.append("ROE達成長門檻")
    fundamental_score = clip(fundamental_score, 0, 30)

    industry_score, industry_reason = opportunity_industry_priority(row)
    row["industry_priority"] = industry_reason
    if industry_score >= 14:
        reasons.append(industry_reason)

    price_score = 0.0
    if -3 <= price_change_1d <= 6:
        price_score += 4
    if 0 <= price_change_20d <= env_float("AI_STOCK_OPPORTUNITY_MAX_20D_PRICE_CHANGE", 30.0):
        price_score += 5
        reasons.append("股價尚未過熱")
    if ma20 > 0 and close >= ma20:
        price_score += 3
    if ma60 > 0 and close >= ma60:
        price_score += 1
    if 0.5 <= volume_ratio <= 2.5:
        price_score += 2
        reasons.append("量能溫和")
    price_score = clip(price_score, 0, 15)

    score = institutional_score + fundamental_score + industry_score + price_score
    return round(clip(score, 0, 100), 2), reasons


def select_opportunity_stocks(results: list[StockRow], excluded: list[StockRow]) -> list[StockRow]:
    limit = env_int("AI_STOCK_OPPORTUNITY_LIMIT", 5)
    min_score = env_float("AI_STOCK_OPPORTUNITY_MIN_SCORE", 58.0)
    max_price_change_20d = env_float("AI_STOCK_OPPORTUNITY_MAX_20D_PRICE_CHANGE", 30.0)
    min_latest_volume = env_float("AI_STOCK_OPPORTUNITY_MIN_LATEST_VOLUME_LOTS", 300.0) * 1000
    min_close = env_float("AI_STOCK_OPPORTUNITY_MIN_CLOSE", 10.0)
    excluded_ids = {row["stock_id"] for row in excluded}
    candidates = []
    for row in results:
        if row["stock_id"] in excluded_ids:
            continue
        score, reasons = calculate_opportunity_score(row)
        row["opportunity_score"] = score
        row["opportunity_reason"] = "、".join(reasons[:5])
        if score < min_score:
            continue
        if float(row.get("institutional_10d_sum") or 0.0) <= 0:
            continue
        if float(row.get("price_change_20d") or 0.0) > max_price_change_20d:
            continue
        if float(row.get("price_change_1d") or 0.0) < -6.0:
            continue
        if float(row.get("latest_volume") or 0.0) < min_latest_volume:
            continue
        if float(row.get("close") or 0.0) < min_close:
            continue
        if row.get("margin_risk"):
            continue
        if row.get("event_risk_flags"):
            continue
        if float(row.get("eps") or 0.0) < 0 and float(row.get("yoy") or 0.0) < 40:
            continue
        candidates.append(row)
    candidates.sort(
        key=lambda row: (
            row.get("opportunity_score", 0.0),
            float(row.get("institutional_20d_avg_volume_ratio") or 0.0),
            float(row.get("trust_10d_sum") or 0.0),
            float(row.get("foreign_10d_sum") or 0.0),
            -float(row.get("price_change_20d") or 0.0),
        ),
        reverse=True,
    )
    selected = candidates[:limit]
    for rank, row in enumerate(selected, start=1):
        row["opportunity_rank"] = rank
    return selected


def evaluate_top_liquidity_quality(row: StockRow, include_model_score: bool = True) -> tuple[bool, str]:
    min_avg_volume = env_float("AI_STOCK_TOP_MIN_AVG_VOLUME_LOTS", 1500.0) * 1000
    min_latest_volume = env_float("AI_STOCK_TOP_MIN_LATEST_VOLUME_LOTS", 800.0) * 1000
    min_turnover = env_float("AI_STOCK_TOP_MIN_20D_TURNOVER_TWD", 100000000.0)
    min_model_score = env_float("AI_STOCK_TOP_MIN_MODEL_SCORE", 65.0)
    min_volume_ratio = env_float("AI_STOCK_TOP_MIN_VOLUME_RATIO", 0.60)
    max_volume_ratio = env_float("AI_STOCK_TOP_MAX_VOLUME_RATIO", 3.00)
    min_close = env_float("AI_STOCK_TOP_MIN_CLOSE", 10.0)
    max_1d_drop = env_float("AI_STOCK_TOP_MAX_1D_DROP", -4.0)
    max_price_change = env_float("AI_STOCK_TOP_MAX_20D_PRICE_CHANGE", 35.0)

    volume_20d_avg = float(row.get("volume_20d_avg") or 0.0)
    latest_volume = float(row.get("latest_volume") or 0.0)
    turnover_20d_avg = float(row.get("turnover_20d_avg") or 0.0)
    volume_ratio = float(row.get("volume_ratio") or 0.0)
    close = float(row.get("close") or 0.0)
    price_change_1d = float(row.get("price_change_1d") or 0.0)
    price_change_20d = float(row.get("price_change_20d") or 0.0)
    total_score = float(row.get("total_score") or 0.0)

    reasons = []
    if include_model_score and total_score and total_score < min_model_score:
        reasons.append("low_model_score")
    if volume_20d_avg < min_avg_volume:
        reasons.append("low_avg_volume")
    if latest_volume < min_latest_volume:
        reasons.append("low_latest_volume")
    if turnover_20d_avg < min_turnover:
        reasons.append("low_turnover")
    if volume_ratio < min_volume_ratio:
        reasons.append("weak_volume_ratio")
    if volume_ratio > max_volume_ratio:
        reasons.append("overheated_volume_ratio")
    if close < min_close:
        reasons.append("low_price")
    if price_change_1d < max_1d_drop:
        reasons.append("heavy_1d_drop")
    if price_change_20d > max_price_change:
        reasons.append("overheated_20d_price")

    return not reasons, ",".join(reasons)


def select_quality_balanced_top(rows: list[StockRow], top_limit: int) -> list[StockRow]:
    passed: list[StockRow] = []
    fallback: list[StockRow] = []
    reason_counts: Counter[str] = Counter()
    for row in rows:
        ok, reason = evaluate_top_liquidity_quality(row)
        row["top_quality_pass"] = ok
        row["top_quality_reason"] = "" if ok else reason
        if ok:
            passed.append(row)
        else:
            fallback.append(row)
            reason_counts.update(item for item in reason.split(",") if item)

    if env_bool("AI_STOCK_TOP_QUALITY_STRICT", False):
        selected = select_industry_balanced_top(passed, top_limit)
    else:
        selected = select_industry_balanced_top(passed + fallback, top_limit)
    fallback_used = sum(1 for row in selected if not row.get("top_quality_pass"))

    DATA_SOURCE_STATUS["top_quality"] = (
        f"passed={len(passed)} failed={len(fallback)} fallback_used={fallback_used} "
        f"min_avg_lots={env_float('AI_STOCK_TOP_MIN_AVG_VOLUME_LOTS', 1500.0):.0f} "
        f"min_ratio={env_float('AI_STOCK_TOP_MIN_VOLUME_RATIO', 0.60):.2f}"
    )
    DATA_SOURCE_STATUS["top_quality_reasons"] = ",".join(
        f"{reason}={count}" for reason, count in reason_counts.most_common(5)
    ) or "none"
    selected.sort(
        key=lambda row: (
            row["total_score"],
            row["technical_score"],
            row["chip_score"],
            row["fundamental_score"],
            row["yoy"],
        ),
        reverse=True,
    )
    return selected[:top_limit]


def summarize_top_quality(rows: list[StockRow]) -> None:
    passed = [row for row in rows if row.get("top_quality_pass")]
    failed = [row for row in rows if not row.get("top_quality_pass")]
    reason_counts: Counter[str] = Counter()
    for row in failed:
        reason_counts.update(item for item in str(row.get("top_quality_reason") or "").split(",") if item)
    DATA_SOURCE_STATUS["top_quality"] = (
        f"passed={len(passed)} failed={len(failed)} fallback_used=0 "
        f"min_avg_lots={env_float('AI_STOCK_TOP_MIN_AVG_VOLUME_LOTS', 1500.0):.0f} "
        f"min_ratio={env_float('AI_STOCK_TOP_MIN_VOLUME_RATIO', 0.60):.2f}"
    )
    DATA_SOURCE_STATUS["top_quality_reasons"] = ",".join(
        f"{reason}={count}" for reason, count in reason_counts.most_common(5)
    ) or "none"


def split_top_and_watchlist(rows: list[StockRow], top_limit: int) -> tuple[list[StockRow], list[StockRow]]:
    min_score = env_float("AI_STOCK_TOP_MIN_MODEL_SCORE", 65.0)
    watch_limit = env_int("AI_STOCK_WATCH_LIMIT", 5)
    formal_candidates = [
        row
        for row in rows
        if row.get("top_quality_pass")
        and float(row.get("total_score") or 0.0) >= min_score
        and not row.get("risk_notes")
    ]
    top_stocks = select_industry_balanced_top(formal_candidates, top_limit)
    top_ids = {row["stock_id"] for row in top_stocks}
    watch_candidates = [
        row
        for row in rows
        if row["stock_id"] not in top_ids
        and float(row.get("total_score") or 0.0) >= env_float("AI_STOCK_WATCH_MIN_MODEL_SCORE", 55.0)
    ]
    watchlist = select_industry_balanced_top(watch_candidates, watch_limit)
    return top_stocks, watchlist


def calculate_valuation_score(row: StockRow) -> float:
    pe_ratio = float(row.get("pe_ratio") or 0.0)
    pbr = float(row.get("pbr") or 0.0)
    roe = float(row.get("roe") or 0.0)
    if pe_ratio <= 0 and pbr <= 0:
        return env_float("AI_STOCK_VALUATION_NEUTRAL_SCORE", 6.0)

    score = 0.0
    if 0 < pe_ratio < 15:
        score += 5
    elif 0 < pe_ratio < 25:
        score += 3
    elif pe_ratio >= 40:
        score -= 2
    if 0 < pbr < 2:
        score += 3
    elif 0 < pbr < 4:
        score += 1
    if 0 < pe_ratio < 25 and roe >= 15:
        score += 2
    return round(clip(score, 0, 10), 2)


def calculate_cash_flow_score(row: StockRow) -> tuple[float, str]:
    has_cash_flow_data = any(key in row for key in ("operating_cash_flow", "free_cash_flow"))
    if not has_cash_flow_data:
        return env_float("AI_STOCK_CASH_FLOW_NEUTRAL_SCORE", 3.0), "cash_flow_neutral_missing"

    operating_cash_flow = float(row.get("operating_cash_flow") or 0.0)
    free_cash_flow = float(row.get("free_cash_flow") or 0.0)
    net_income = float(row.get("net_income") or 0.0)
    score = 0.0
    labels = []
    if operating_cash_flow > 0:
        score += 2
        labels.append("ocf_positive")
    if free_cash_flow > 0:
        score += 2
        labels.append("fcf_positive")
    if net_income > 0 and operating_cash_flow / net_income > 1:
        score += 3
        labels.append("ocf_gt_net_income")
    if operating_cash_flow < 0:
        labels.append("ocf_negative")
    if free_cash_flow < 0:
        labels.append("fcf_negative")
    return round(clip(score, 0, 7), 2), ",".join(labels)


def calculate_eps_acceleration_score(row: StockRow) -> tuple[float, str]:
    eps = float(row.get("eps") or 0.0)
    previous_eps = float(row.get("previous_eps") or 0.0)
    eps_change_pct = float(row.get("eps_change_pct") or 0.0)
    yoy = float(row.get("yoy") or 0.0)
    acc_yoy = float(row.get("acc_yoy") or 0.0)
    notes: list[str] = []
    score = 0.0

    if previous_eps > 0 and eps > 0 and eps_change_pct:
        if eps_change_pct >= 80:
            score += 5
            notes.append("eps_accel_strong")
        elif eps_change_pct >= 30:
            score += 3
            notes.append("eps_accel_positive")
        elif eps_change_pct <= -30:
            score -= 3
            notes.append("eps_deceleration")
    elif eps > 0 and yoy >= 30 and acc_yoy >= 20:
        score += 1
        notes.append("eps_positive_revenue_proxy")
    elif eps < 0 and yoy < 40:
        score -= 2
        notes.append("eps_loss_no_growth")
    else:
        notes.append("eps_accel_neutral")

    return round(clip(score, -3, 5), 2), ",".join(notes)


def calculate_fundamental_v2_score(row: StockRow) -> float:
    revenue_component = clip(float(row.get("fundamental_score") or 0.0) / 40 * 20, 0, 20)
    eps = float(row.get("eps") or 0.0)
    roe = float(row.get("roe") or 0.0)
    net_income = float(row.get("net_income") or 0.0)
    cash_flow_score, cash_flow_quality = calculate_cash_flow_score(row)
    row["cash_flow_score"] = cash_flow_score
    row["cash_flow_quality"] = cash_flow_quality

    quality_score = 0.0
    has_financial_quality_data = any(key in row for key in ("eps", "roe", "net_income"))
    if not has_financial_quality_data:
        quality_score += env_float("AI_STOCK_FINANCIAL_NEUTRAL_SCORE", 6.0)
        row["financial_quality_note"] = "financial_neutral_missing"
    if eps > 0:
        quality_score += 2
    if eps >= 2:
        quality_score += 2
    if net_income > 0:
        quality_score += 1
    if roe >= 20:
        quality_score += 6
    elif roe >= 15:
        quality_score += 4
    elif roe >= 12:
        quality_score += 2
    elif 0 < roe < 5:
        quality_score -= 2

    return round(clip(revenue_component + quality_score + cash_flow_score, 0, 35), 2)


def calculate_industry_score(row: StockRow) -> float:
    relative = float(row.get("industry_relative_score") or 0.0)
    peer_count = int(row.get("industry_peer_count") or 0)
    score = 5.0 + clip(relative, -5, 5)
    if peer_count >= env_int("AI_STOCK_MIN_INDUSTRY_PEERS", 3) and relative > 0:
        score += 1.0
    return round(clip(score, 0, 10), 2)


def calculate_risk_penalty(row: StockRow) -> tuple[float, list[str]]:
    penalty = 0.0
    notes = []
    yoy = float(row.get("yoy") or 0.0)
    monthly_revenue = float(row.get("monthly_revenue") or 0.0)
    price_change_1d = float(row.get("price_change_1d") or 0.0)
    price_change_20d = float(row.get("price_change_20d") or 0.0)
    margin_risk = bool(row.get("margin_risk"))
    foreign_10d = float(row.get("foreign_10d_sum") or 0.0)
    trust_10d = float(row.get("trust_10d_sum") or 0.0)
    top_quality_pass = bool(row.get("top_quality_pass"))
    eps = float(row.get("eps") or 0.0)
    roe = float(row.get("roe") or 0.0)
    operating_cash_flow = float(row.get("operating_cash_flow") or 0.0)
    free_cash_flow = float(row.get("free_cash_flow") or 0.0)
    event_risk_level = str(row.get("event_risk_level") or "")
    event_risk_flags = str(row.get("event_risk_flags") or "")

    if yoy > env_float("AI_STOCK_RISK_YOY_OVERHEAT", 300.0):
        penalty += 5
        notes.append("yoy_over_300")
    if monthly_revenue < env_float("AI_STOCK_MIN_MONTHLY_REVENUE", 80000.0):
        penalty += 5
        notes.append("small_revenue")
    if margin_risk:
        penalty += 8
        notes.append("margin_surge")
    if foreign_10d < 0 and trust_10d < 0:
        penalty += 5
        notes.append("institutional_selling")
    if eps < 0:
        penalty += 8
        notes.append("eps_loss")
    if 0 < roe < 5:
        penalty += 5
        notes.append("roe_below_5")
    if operating_cash_flow < 0:
        penalty += 3
        notes.append("ocf_negative")
    if free_cash_flow < 0:
        penalty += 3
        notes.append("fcf_negative")
    if price_change_1d < env_float("AI_STOCK_RISK_MAX_1D_DROP", -3.0):
        penalty += 8
        notes.append("heavy_1d_drop")
    if price_change_20d > env_float("AI_STOCK_RISK_MAX_20D_PRICE_CHANGE", 50.0):
        penalty += 10
        notes.append("price_overheat")
    if "attention_stock" in event_risk_flags or event_risk_level == "attention":
        penalty += 8
        notes.append("attention_stock")
    if "disposition_stock" in event_risk_flags or event_risk_level == "disposition":
        penalty += 15
        notes.append("disposition_stock")
    if not top_quality_pass:
        penalty += 3
        notes.append("liquidity_fallback")

    return round(clip(penalty, 0, 30), 2), notes


def model_grade(score: float) -> str:
    if score >= 85:
        return "A"
    if score >= 75:
        return "B"
    if score >= 65:
        return "C"
    return "D"


def apply_institutional_growth_model(rows: list[StockRow]) -> None:
    for row in rows:
        legacy_score = (
            float(row.get("fundamental_score") or 0.0)
            + float(row.get("technical_score") or 0.0)
            + float(row.get("chip_score") or 0.0)
            + float(row.get("industry_relative_score") or 0.0)
        )
        fundamental_weighted = calculate_fundamental_v2_score(row)
        chip_weighted = clip(float(row.get("chip_score") or 0.0) / 25 * 25, 0, 25)
        technical_weighted = clip(float(row.get("technical_score") or 0.0) / 35 * 20, 0, 20)
        valuation_score = calculate_valuation_score(row)
        industry_score = calculate_industry_score(row)
        eps_acceleration_score, eps_acceleration_note = calculate_eps_acceleration_score(row)
        risk_penalty, risk_notes = calculate_risk_penalty(row)
        total_score = (
            fundamental_weighted
            + chip_weighted
            + technical_weighted
            + valuation_score
            + industry_score
            + eps_acceleration_score
            - risk_penalty
        )
        grade = model_grade(total_score)

        row["model_version"] = "V2.1 Institutional Growth + EPS Trial"
        row["legacy_score"] = round(legacy_score, 2)
        row["fundamental_score"] = round(fundamental_weighted, 2)
        row["chip_score"] = round(chip_weighted, 2)
        row["technical_score"] = round(technical_weighted, 2)
        row["valuation_score"] = round(valuation_score, 2)
        row["industry_score"] = round(industry_score, 2)
        row["eps_acceleration_score"] = eps_acceleration_score
        row["eps_acceleration_note"] = eps_acceleration_note
        row["risk_penalty"] = risk_penalty
        row["risk_notes"] = risk_notes
        row["total_score"] = round(clip(total_score, 0, 100), 2)
        row["model_grade"] = grade
        row["entry_plan"] = "score>=85 or top10 observation; confirm liquidity and institutional flow"
        row["exit_plan"] = "stop -8%; time stop 20 trading days; below MA20 trim 50%; below MA60 exit; profit +30% trail"


def parse_income_margins() -> dict[str, dict[str, float]]:
    try:
        rows = fetch_with_retry(TWSE_INCOME_URL, tries=2, timeout=60)
    except Exception:
        return {}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows if isinstance(rows, list) else []:
        stock_id = str(row.get("公司代號") or row.get("公司代碼") or row.get("證券代號") or "").strip()
        if stock_id:
            grouped.setdefault(stock_id, []).append(row)

    margins: dict[str, dict[str, float]] = {}
    for stock_id, items in grouped.items():
        revenue_values = find_statement_values(items, ["營業收入", "收入總額", "收益"])
        gross_values = find_statement_values(items, ["營業毛利", "銷貨毛利"])
        operating_values = find_statement_values(items, ["營業利益", "營業淨利"])
        net_values = find_statement_values(items, ["本期淨利", "稅後淨利", "淨利"])
        if not revenue_values:
            continue

        current_revenue = revenue_values[-1]
        previous_revenue = revenue_values[-2] if len(revenue_values) >= 2 else 0.0
        current_gross = gross_values[-1] if gross_values else 0.0
        previous_gross = gross_values[-2] if len(gross_values) >= 2 else 0.0
        current_operating = operating_values[-1] if operating_values else 0.0
        previous_operating = operating_values[-2] if len(operating_values) >= 2 else 0.0
        current_net = net_values[-1] if net_values else 0.0
        previous_net = net_values[-2] if len(net_values) >= 2 else 0.0

        gross_margin = ratio_percent(current_gross, current_revenue)
        operating_margin = ratio_percent(current_operating, current_revenue)
        net_margin = ratio_percent(current_net, current_revenue)
        prev_gross_margin = ratio_percent(previous_gross, previous_revenue)
        prev_operating_margin = ratio_percent(previous_operating, previous_revenue)
        prev_net_margin = ratio_percent(previous_net, previous_revenue)
        triple_margin_up = (
            gross_margin > prev_gross_margin > 0
            and operating_margin > prev_operating_margin > 0
            and net_margin > prev_net_margin > 0
        )

        margins[stock_id] = {
            "gross_margin": gross_margin,
            "operating_margin": operating_margin,
            "net_margin": net_margin,
            "prev_gross_margin": prev_gross_margin,
            "prev_operating_margin": prev_operating_margin,
            "prev_net_margin": prev_net_margin,
            "triple_margin_up": triple_margin_up,
        }
    return margins


def ratio_percent(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator * 100, 2)


def find_statement_values(items: list[dict[str, Any]], keywords: list[str]) -> list[float]:
    for row in items:
        text = " ".join(str(value) for value in row.values())
        if not any(keyword in text for keyword in keywords):
            continue
        values = []
        for key, value in row.items():
            key_text = str(key)
            if any(label in key_text for label in ["代號", "名稱", "會計", "項目", "年度", "季別"]):
                continue
            number = clean_number(value)
            if number != 0:
                values.append(number)
        return values
    return []


def select_fundamental_candidates(rows: list[StockRow], margins: dict[str, dict[str, float]]) -> list[StockRow]:
    min_yoy = env_float("AI_STOCK_MIN_YOY", 20)
    min_mom = env_float("AI_STOCK_MIN_MOM", 0)
    min_acc_yoy = env_float("AI_STOCK_MIN_ACC_YOY", 15)
    candidate_limit = env_int("AI_STOCK_CANDIDATE_LIMIT", 80)

    selected = []
    anomaly_count = 0
    financial_count = 0
    for row in rows:
        industry = str(row.get("industry_category") or "")
        if env_bool("AI_STOCK_EXCLUDE_FINANCIAL", True) and any(keyword in industry for keyword in ["金融", "證券", "保險"]):
            financial_count += 1
            continue
        anomaly, anomaly_reason = detect_revenue_anomaly(row)
        row["revenue_anomaly"] = anomaly
        row["revenue_anomaly_reason"] = anomaly_reason
        if anomaly:
            anomaly_count += 1
            continue
        if row["yoy"] <= min_yoy or row["mom"] <= min_mom or row["acc_yoy"] <= min_acc_yoy:
            continue
        enriched = row.copy()
        enriched.update(margins.get(row["stock_id"], {}))
        enriched.setdefault("gross_margin", 0.0)
        enriched.setdefault("operating_margin", 0.0)
        enriched.setdefault("net_margin", 0.0)
        enriched.setdefault("prev_gross_margin", 0.0)
        enriched.setdefault("prev_operating_margin", 0.0)
        enriched.setdefault("prev_net_margin", 0.0)
        enriched.setdefault("triple_margin_up", False)
        margin_score = (
            clip(enriched["gross_margin"], 0, 50) * 0.16
            + clip(enriched["operating_margin"], 0, 35) * 0.18
            + clip(enriched["net_margin"], 0, 30) * 0.14
        )
        revenue_score = calculate_revenue_score(enriched)
        triple_margin_bonus = 8 if enriched["triple_margin_up"] else 0
        enriched["fundamental_score"] = round(clip(revenue_score * 0.58 + margin_score + triple_margin_bonus, 0, 40), 2)
        selected.append(enriched)

    selected.sort(key=lambda row: (row["fundamental_score"], row["yoy"], row["acc_yoy"]), reverse=True)
    DATA_SOURCE_STATUS["revenue_filter"] = f"anomaly_removed={anomaly_count} financial_removed={financial_count}"
    return selected[:candidate_limit]


def enrich_revenue_candidate(row: StockRow, margins: dict[str, dict[str, float]]) -> StockRow:
    enriched = row.copy()
    enriched.update(margins.get(row["stock_id"], {}))
    enriched.setdefault("gross_margin", 0.0)
    enriched.setdefault("operating_margin", 0.0)
    enriched.setdefault("net_margin", 0.0)
    enriched.setdefault("prev_gross_margin", 0.0)
    enriched.setdefault("prev_operating_margin", 0.0)
    enriched.setdefault("prev_net_margin", 0.0)
    enriched.setdefault("triple_margin_up", False)
    margin_score = (
        clip(enriched["gross_margin"], 0, 50) * 0.16
        + clip(enriched["operating_margin"], 0, 35) * 0.18
        + clip(enriched["net_margin"], 0, 30) * 0.14
    )
    revenue_score = calculate_revenue_score(enriched)
    triple_margin_bonus = 8 if enriched["triple_margin_up"] else 0
    enriched["fundamental_score"] = round(clip(revenue_score * 0.58 + margin_score + triple_margin_bonus, 0, 40), 2)
    return enriched


def institutional_candidate_score(stock_id: str, records: list[dict[str, float]], row: StockRow) -> tuple[float, str]:
    latest = sorted(records, key=lambda item: item["date"], reverse=True)
    recent_10 = latest[:10]
    recent_20 = latest[:20]
    if not recent_10:
        return 0.0, ""
    foreign_10 = sum(item["foreign"] for item in recent_10)
    trust_10 = sum(item["trust"] for item in recent_10)
    inst_20 = sum(item["foreign"] + item["trust"] for item in recent_20)
    foreign_days = sum(1 for item in recent_10 if item["foreign"] > 0)
    trust_days = sum(1 for item in recent_10 if item["trust"] > 0)
    prior_10 = sum(item["foreign"] + item["trust"] for item in latest[10:20])
    yoy = float(row.get("yoy") or 0.0)
    acc_yoy = float(row.get("acc_yoy") or 0.0)

    score = 0.0
    reasons = []
    if foreign_10 > 0:
        score += 10
        reasons.append("外資10日轉買")
    if trust_10 > 0:
        score += 14
        reasons.append("投信10日建倉")
    if foreign_10 > 0 and trust_10 > 0:
        score += 8
        reasons.append("外資投信同步")
    if foreign_days >= 6:
        score += 6
        reasons.append("外資買超天數多")
    if trust_days >= 5:
        score += 7
        reasons.append("投信買超天數多")
    if prior_10 < 0 and foreign_10 + trust_10 > 0:
        score += 7
        reasons.append("法人由賣轉買")
    if inst_20 > 0:
        score += 4
    if yoy >= 10:
        score += 4
    if acc_yoy >= 10:
        score += 4
    return round(score, 2), "、".join(reasons[:4])


def select_institutional_candidates(
    rows: list[StockRow],
    margins: dict[str, dict[str, float]],
    institutional: dict[str, list[dict[str, float]]],
    existing: list[StockRow],
) -> list[StockRow]:
    limit = env_int("AI_STOCK_INSTITUTIONAL_CANDIDATE_LIMIT", 50)
    min_score = env_float("AI_STOCK_INSTITUTIONAL_CANDIDATE_MIN_SCORE", 22.0)
    existing_ids = {row["stock_id"] for row in existing}
    candidates: list[StockRow] = []
    for row in rows:
        stock_id = row["stock_id"]
        if stock_id in existing_ids:
            continue
        anomaly, _reason = detect_revenue_anomaly(row)
        if anomaly:
            continue
        records = institutional.get(stock_id, [])
        score, reason = institutional_candidate_score(stock_id, records, row)
        if score < min_score:
            continue
        enriched = enrich_revenue_candidate(row, margins)
        enriched["candidate_source"] = "institutional"
        enriched["institutional_candidate_score"] = score
        enriched["institutional_candidate_reason"] = reason
        candidates.append(enriched)
    candidates.sort(
        key=lambda row: (
            float(row.get("institutional_candidate_score") or 0.0),
            float(row.get("fundamental_score") or 0.0),
            float(row.get("yoy") or 0.0),
            float(row.get("acc_yoy") or 0.0),
        ),
        reverse=True,
    )
    return candidates[:limit]


def merge_candidate_pools(fundamental: list[StockRow], institutional_candidates: list[StockRow]) -> list[StockRow]:
    merged: list[StockRow] = []
    seen: set[str] = set()
    for row in fundamental:
        row["candidate_source"] = str(row.get("candidate_source") or "fundamental")
        if row["stock_id"] in seen:
            continue
        merged.append(row)
        seen.add(row["stock_id"])
    for row in institutional_candidates:
        if row["stock_id"] in seen:
            continue
        merged.append(row)
        seen.add(row["stock_id"])
    DATA_SOURCE_STATUS["candidate_pool"] = (
        f"fundamental={len(fundamental)} institutional={len(institutional_candidates)} merged={len(merged)}"
    )
    return merged


def fetch_price_history(stock_id: str, months: int = 7) -> list[dict[str, float]]:
    today = datetime.now(TAIPEI_TZ).date()
    month_starts = []
    year, month = today.year, today.month
    for offset in range(months):
        m = month - offset
        y = year
        while m <= 0:
            m += 12
            y -= 1
        month_starts.append(f"{y}{m:02d}01")

    records: list[dict[str, float]] = []
    seen_dates: set[str] = set()
    for date in month_starts:
        try:
            payload = fetch_with_retry(
                TWSE_STOCK_DAY_URL,
                params={"response": "json", "date": date, "stockNo": stock_id},
                tries=2,
                timeout=30,
            )
        except Exception:
            continue
        fields = payload.get("fields", []) if isinstance(payload, dict) else []
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not fields or not data:
            continue
        for values in data:
            row = dict(zip(fields, values))
            trade_date = str(row.get("日期", ""))
            if not trade_date or trade_date in seen_dates:
                continue
            close = clean_number(row.get("收盤價"))
            volume = clean_number(row.get("成交股數"))
            if close <= 0:
                continue
            seen_dates.add(trade_date)
            records.append({"date": trade_date, "close": close, "volume": volume})
        time.sleep(0.1)

    records = sorted(records, key=lambda item: item["date"])
    if len(records) >= 60:
        return records
    try:
        finmind_records = fetch_finmind_price_history(stock_id)
        if len(finmind_records) > len(records):
            DATA_SOURCE_STATUS["price"] = "TWSE+FinMind"
            return finmind_records
    except Exception:
        pass
    return records


def moving_average(values: list[float], window: int) -> float:
    if len(values) < window:
        return 0.0
    return sum(values[-window:]) / window


def calculate_technical_score(stock_id: str) -> dict[str, Any]:
    records = fetch_price_history(stock_id)
    closes = [row["close"] for row in records]
    volumes = [row["volume"] for row in records]
    latest_trade_date = str(records[-1].get("date", "")) if records else ""
    if len(closes) < 60:
        price_change_1d = ((closes[-1] / closes[-2]) - 1) * 100 if len(closes) >= 2 and closes[-2] else 0.0
        return {
            "technical_score": 0.0,
            "close": closes[-1] if closes else 0.0,
            "ma5": 0.0,
            "ma20": 0.0,
            "ma60": 0.0,
            "volume_ratio": 0.0,
            "latest_volume": volumes[-1] if volumes else 0.0,
            "volume_20d_avg": 0.0,
            "turnover_20d_avg": 0.0,
            "latest_trade_date": latest_trade_date,
            "price_change_1d": round(price_change_1d, 2),
            "price_change_20d": 0.0,
            "break_120d_high": False,
        }

    close = closes[-1]
    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    volume20 = moving_average(volumes, 20)
    latest_volume = volumes[-1]
    volume_ratio = volumes[-1] / volume20 if volume20 else 0.0
    turnover_20d_avg = close * volume20
    price_change_1d = ((close / closes[-2]) - 1) * 100 if len(closes) >= 2 and closes[-2] else 0.0
    price_change_20d = ((close / closes[-21]) - 1) * 100 if len(closes) >= 21 and closes[-21] else 0.0
    prior_high_120 = max(closes[-121:-1]) if len(closes) >= 121 else max(closes[:-1])
    break_120d_high = close > prior_high_120

    score = 0.0
    if ma5 > ma20 > ma60:
        score += 12
    elif ma5 > ma20:
        score += 6
    if close > ma20:
        score += 5
    if break_120d_high:
        score += 10
    if volume_ratio >= 1.5:
        score += 8
    elif volume_ratio >= 1.2:
        score += 4

    return {
        "technical_score": round(clip(score, 0, 35), 2),
        "close": round(close, 2),
        "ma5": round(ma5, 2),
        "ma20": round(ma20, 2),
        "ma60": round(ma60, 2),
        "volume_ratio": round(volume_ratio, 2),
        "latest_volume": round(latest_volume, 0),
        "volume_20d_avg": round(volume20, 0),
        "turnover_20d_avg": round(turnover_20d_avg, 0),
        "latest_trade_date": latest_trade_date,
        "price_change_1d": round(price_change_1d, 2),
        "price_change_20d": round(price_change_20d, 2),
        "break_120d_high": break_120d_high,
    }


def recent_market_dates(days: int = 12) -> list[str]:
    today = datetime.now(TAIPEI_TZ).date()
    dates = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        if day.weekday() < 5:
            dates.append(day.strftime("%Y%m%d"))
    return dates


def fetch_institutional_maps() -> dict[str, list[dict[str, float]]]:
    by_stock: dict[str, list[dict[str, float]]] = {}
    for date in recent_market_dates(35):
        try:
            payload = fetch_with_retry(
                TWSE_T86_URL,
                params={"response": "json", "date": date, "selectType": "ALLBUT0999"},
                tries=1,
                timeout=30,
            )
        except Exception:
            continue
        fields = payload.get("fields", []) if isinstance(payload, dict) else []
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not fields or not data:
            continue
        for values in data:
            row = dict(zip(fields, values))
            stock_id = str(row.get("證券代號") or row.get("股票代號") or "").strip()
            if not stock_id:
                continue
            foreign = pick_exact_number(
                row,
                [
                    "外資及陸資買賣超股數(不含外資自營商)",
                    "外陸資買賣超股數(不含外資自營商)",
                    "外資及陸資買賣超股數",
                    "外資買賣超股數",
                ],
            )
            trust = pick_exact_number(row, ["投信買賣超股數"])
            by_stock.setdefault(stock_id, []).append({"date": float(date), "trade_date": date, "foreign": foreign, "trust": trust})
        if by_stock:
            time.sleep(0.1)
    return by_stock


def fetch_margin_map() -> dict[str, dict[str, float]]:
    for date in recent_market_dates(10):
        try:
            payload = fetch_with_retry(
                TWSE_MARGIN_URL,
                params={"response": "json", "date": date, "selectType": "ALL"},
                tries=1,
                timeout=30,
            )
        except Exception:
            continue
        fields = payload.get("fields", []) if isinstance(payload, dict) else []
        data = payload.get("data", []) if isinstance(payload, dict) else []
        if not fields or not data:
            continue
        margin_by_stock: dict[str, dict[str, float]] = {}
        for values in data:
            row = dict(zip(fields, values))
            stock_id = str(row.get("股票代號") or row.get("證券代號") or "").strip()
            if not stock_id:
                continue
            margin_buy = pick_exact_number(row, ["融資買進"])
            margin_sell = pick_exact_number(row, ["融資賣出"])
            margin_balance = pick_exact_number(row, ["融資今日餘額", "融資餘額"])
            margin_by_stock[stock_id] = {
                "margin_trade_date": date,
                "margin_balance": margin_balance,
                "margin_buy_sell": margin_buy - margin_sell,
            }
        if margin_by_stock:
            return margin_by_stock
    return {}


def pick_exact_number(row: dict[str, Any], aliases: list[str]) -> float:
    normalized = {normalize_field_name(key): value for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(normalize_field_name(alias))
        if value is not None:
            return clean_number(value)
    return 0.0


def normalize_field_name(value: object) -> str:
    return str(value).replace(" ", "").replace("　", "").strip()


def shares_to_lots(value: float) -> float:
    return value / 1000


def calculate_chip_score(stock_id: str, institutional: dict[str, list[dict[str, float]]]) -> dict[str, Any]:
    records = sorted(institutional.get(stock_id, []), key=lambda row: row["date"], reverse=True)[:5]
    foreign_values = [row["foreign"] for row in records]
    trust_values = [row["trust"] for row in records]
    foreign_sum = sum(foreign_values)
    trust_sum = sum(trust_values)
    foreign_positive_days = sum(1 for value in foreign_values if value > 0)
    trust_positive_days = sum(1 for value in trust_values if value > 0)

    score = 0.0
    if len(records) >= 5 and foreign_positive_days == 5:
        score += 12
    elif foreign_sum > 0:
        score += 6
    if trust_sum > 0:
        score += 8
    if foreign_sum > 0 and trust_sum > 0:
        score += 5

    return {
        "chip_score": round(clip(score, 0, 25), 2),
        "institutional_trade_date": str(int(records[0]["date"])) if records else "",
        "foreign_5d_sum": round(foreign_sum, 0),
        "foreign_5d_positive_days": foreign_positive_days,
        "trust_5d_sum": round(trust_sum, 0),
        "trust_5d_positive_days": trust_positive_days,
    }


def normalize_trade_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "/" in text:
        parts = [part.strip() for part in text.split("/")]
        if len(parts) >= 3 and all(part.isdigit() for part in parts[:3]):
            year = int(parts[0])
            if year < 1911:
                year += 1911
            return f"{year:04d}{int(parts[1]):02d}{int(parts[2]):02d}"
    if len(text) >= 10 and text[4] == "-":
        return text[:10].replace("-", "")
    if "." in text:
        text = text.split(".", 1)[0]
    return "".join(ch for ch in text if ch.isdigit())[:8]


def check_data_completeness(rows: list[StockRow]) -> tuple[bool, str]:
    if not rows:
        DATA_SOURCE_STATUS["data_completeness"] = "empty"
        return False, "empty"

    price_dates = Counter(normalize_trade_date(row.get("latest_trade_date")) for row in rows)
    institutional_dates = Counter(normalize_trade_date(row.get("institutional_trade_date")) for row in rows)
    margin_dates = Counter(normalize_trade_date(row.get("margin_trade_date")) for row in rows if row.get("margin_trade_date"))

    price_date = price_dates.most_common(1)[0][0] if price_dates else ""
    institutional_date = institutional_dates.most_common(1)[0][0] if institutional_dates else ""
    margin_date = margin_dates.most_common(1)[0][0] if margin_dates else ""
    missing_institutional = sum(1 for row in rows if not normalize_trade_date(row.get("institutional_trade_date")))
    missing_margin = sum(1 for row in rows if not normalize_trade_date(row.get("margin_trade_date")))

    ready = bool(price_date) and bool(institutional_date) and price_date == institutional_date
    margin_ready = bool(margin_date) and margin_date == price_date

    status = (
        f"ready={int(ready)} price={price_date or 'missing'} "
        f"institutional={institutional_date or 'missing'} margin={margin_date or 'missing'} "
        f"margin_ready={int(margin_ready)} missing_inst={missing_institutional} missing_margin={missing_margin}"
    )
    DATA_SOURCE_STATUS["data_completeness"] = status
    return ready, status


def calculate_accumulation_score(
    row: StockRow,
    institutional: dict[str, list[dict[str, float]]],
    margins: dict[str, dict[str, float]],
) -> dict[str, Any]:
    all_records = sorted(institutional.get(row["stock_id"], []), key=lambda item: item["date"], reverse=True)
    records = all_records[:10]
    records_20d = all_records[:20]
    foreign_values = [item["foreign"] for item in records]
    trust_values = [item["trust"] for item in records]
    foreign_20d_values = [item["foreign"] for item in records_20d]
    trust_20d_values = [item["trust"] for item in records_20d]
    foreign_sum = sum(foreign_values)
    trust_sum = sum(trust_values)
    institutional_sum = foreign_sum + trust_sum
    institutional_20d_sum = sum(foreign_20d_values) + sum(trust_20d_values)
    latest_foreign_net = foreign_values[0] if foreign_values else 0.0
    latest_trust_net = trust_values[0] if trust_values else 0.0
    latest_institutional_net = latest_foreign_net + latest_trust_net
    prior_institutional_net = (foreign_values[1] + trust_values[1]) if len(foreign_values) >= 2 and len(trust_values) >= 2 else 0.0
    recent_2d_institutional_net = latest_institutional_net + prior_institutional_net
    foreign_prior_15d_sum = sum(foreign_20d_values[5:20])
    foreign_positive_days = sum(1 for value in foreign_values if value > 0)
    trust_positive_days = sum(1 for value in trust_values if value > 0)
    volume_ratio = float(row.get("volume_ratio") or 0.0)
    volume_20d_avg = float(row.get("volume_20d_avg") or 0.0)
    price_change_20d = float(row.get("price_change_20d") or 0.0)
    close = float(row.get("close") or 0.0)
    ma20 = float(row.get("ma20") or 0.0)
    min_avg_volume = env_float("AI_STOCK_MIN_AVG_VOLUME_LOTS", 1000.0) * 1000
    institutional_volume_ratio = institutional_20d_sum / volume_20d_avg if volume_20d_avg > 0 else 0.0
    low_liquidity = volume_20d_avg < min_avg_volume
    foreign_reversal = foreign_prior_15d_sum < 0 and foreign_sum > 0 and foreign_positive_days >= 3
    margin_data = margins.get(row["stock_id"], {})
    margin_trade_date = str(margin_data.get("margin_trade_date") or "")
    margin_balance = float(margin_data.get("margin_balance") or 0.0)
    margin_buy_sell = float(margin_data.get("margin_buy_sell") or 0.0)
    margin_buy_sell_ratio = margin_buy_sell / volume_20d_avg if volume_20d_avg > 0 else 0.0
    margin_risk = margin_buy_sell_ratio >= env_float("AI_STOCK_MARGIN_RISK_RATIO", 0.20)

    score = 0.0
    if len(records) >= 8 and trust_positive_days >= 6:
        score += 22
    elif trust_sum > 0:
        score += 14
    if len(records) >= 8 and foreign_positive_days >= 6:
        score += 20
    elif foreign_sum > 0:
        score += 12
    if foreign_sum > 0 and trust_sum > 0:
        score += 15
    if 0.15 <= institutional_volume_ratio <= 0.30:
        score += 15
    elif 0.05 <= institutional_volume_ratio < 0.15:
        score += 8
    elif institutional_volume_ratio > 0.50:
        score -= 8
    if 1.05 <= volume_ratio <= 1.60:
        score += 15
    elif 0.90 <= volume_ratio < 1.05:
        score += 8
    elif volume_ratio > 2.00:
        score -= 6
    if -5.0 <= price_change_20d <= 15.0:
        score += 15
    elif 15.0 < price_change_20d <= 25.0:
        score += 8
    elif price_change_20d > 25.0:
        score -= 8
    if close > ma20 and not row.get("break_120d_high"):
        score += 8
    if foreign_reversal:
        score += 12
    if margin_risk:
        score -= 15
    if float(row.get("acc_yoy") or 0.0) > 15 and float(row.get("yoy") or 0.0) > 10:
        score += 5

    score = round(clip(score, 0, 100), 2)
    quiet_accumulation = score >= 65 and price_change_20d <= 20 and institutional_sum > 0 and not low_liquidity
    return {
        "accumulation_score": score,
        "foreign_10d_sum": round(foreign_sum, 0),
        "foreign_10d_positive_days": foreign_positive_days,
        "trust_10d_sum": round(trust_sum, 0),
        "trust_10d_positive_days": trust_positive_days,
        "latest_foreign_net": round(latest_foreign_net, 0),
        "latest_trust_net": round(latest_trust_net, 0),
        "latest_institutional_net": round(latest_institutional_net, 0),
        "prior_institutional_net": round(prior_institutional_net, 0),
        "recent_2d_institutional_net": round(recent_2d_institutional_net, 0),
        "institutional_10d_sum": round(institutional_sum, 0),
        "institutional_20d_sum": round(institutional_20d_sum, 0),
        "institutional_20d_avg_volume_ratio": round(institutional_volume_ratio, 4),
        "foreign_prior_15d_sum": round(foreign_prior_15d_sum, 0),
        "foreign_reversal": foreign_reversal,
        "margin_balance": round(margin_balance, 0),
        "margin_buy_sell": round(margin_buy_sell, 0),
        "margin_trade_date": margin_trade_date,
        "margin_risk": margin_risk,
        "low_liquidity": low_liquidity,
        "quiet_accumulation": quiet_accumulation,
    }


def radar_exclusion_reasons(row: StockRow) -> list[str]:
    reasons: list[str] = []
    price_change_1d = float(row.get("price_change_1d") or 0.0)
    yoy = float(row.get("yoy") or 0.0)
    acc_yoy = float(row.get("acc_yoy") or 0.0)
    eps = float(row.get("eps") or 0.0)
    roe = float(row.get("roe") or 0.0)
    net_income = float(row.get("net_income") or 0.0)
    operating_margin = float(row.get("operating_margin") or 0.0)
    latest_net = float(row.get("latest_institutional_net") or 0.0)
    prior_net = float(row.get("prior_institutional_net") or 0.0)
    recent_2d_net = float(row.get("recent_2d_institutional_net") or 0.0)
    latest_foreign = float(row.get("latest_foreign_net") or 0.0)
    latest_trust = float(row.get("latest_trust_net") or 0.0)
    volume_20d_avg = float(row.get("volume_20d_avg") or 0.0)
    large_sell_lots = env_float("AI_STOCK_RADAR_FOREIGN_BIG_SELL_LOTS", 1000.0) * 1000
    large_sell_ratio = env_float("AI_STOCK_RADAR_FOREIGN_BIG_SELL_AVG_VOLUME_RATIO", 0.05)
    large_sell_threshold = max(large_sell_lots, volume_20d_avg * large_sell_ratio)
    industry_score, _industry_reason = opportunity_industry_priority(row)
    has_profit_quality = eps > 0 or roe >= 5 or net_income > 0 or operating_margin > 0

    if price_change_1d <= env_float("AI_STOCK_RADAR_MAX_1D_DROP", -6.0):
        reasons.append("radar_heavy_1d_drop")
    if price_change_1d <= env_float("AI_STOCK_RADAR_LIMIT_DOWN_THRESHOLD", -9.0):
        reasons.append("radar_near_limit_down")
    if latest_net < 0:
        reasons.append("radar_latest_institutional_selling")
    if latest_net < 0 and prior_net < 0:
        reasons.append("radar_2d_institutional_selling")
    if recent_2d_net < 0:
        reasons.append("radar_recent_2d_net_selling")
    if latest_foreign <= -large_sell_threshold and latest_trust <= max(0.0, abs(latest_foreign) * 0.25):
        reasons.append("radar_foreign_big_sell_no_trust_support")
    if yoy <= 0 and acc_yoy <= 0:
        reasons.append("radar_no_revenue_growth")
    if eps < 0 and max(yoy, acc_yoy) < env_float("AI_STOCK_RADAR_LOSS_HIGH_GROWTH_YOY", 40.0):
        reasons.append("radar_eps_loss_without_high_growth")
    if industry_score < env_float("AI_STOCK_RADAR_MIN_INDUSTRY_PRIORITY", 8.0) and not has_profit_quality:
        reasons.append("radar_no_priority_or_profit_quality")
    if row.get("event_risk_flags"):
        reasons.append("radar_event_risk")
    return reasons


def select_accumulation_radar(results: list[StockRow], top_stocks: list[StockRow]) -> list[StockRow]:
    radar_limit = env_int("AI_STOCK_RADAR_LIMIT", 5)
    min_score = env_float("AI_STOCK_RADAR_MIN_SCORE", 55.0)
    max_price_change_20d = env_float("AI_STOCK_RADAR_MAX_20D_PRICE_CHANGE", 25.0)
    min_volume_ratio = env_float("AI_STOCK_RADAR_MIN_VOLUME_RATIO", 0.50)
    min_latest_volume = env_float("AI_STOCK_RADAR_MIN_LATEST_VOLUME_LOTS", 300.0) * 1000
    min_close = env_float("AI_STOCK_RADAR_MIN_CLOSE", 10.0)
    top_ids = {row["stock_id"] for row in top_stocks}
    candidates = []
    for row in results:
        exclusion_reasons = radar_exclusion_reasons(row)
        row["radar_exclusion_reason"] = ",".join(exclusion_reasons)
        if row["stock_id"] in top_ids:
            continue
        if exclusion_reasons:
            continue
        if float(row.get("accumulation_score") or 0.0) < min_score:
            continue
        if float(row.get("institutional_10d_sum") or 0.0) <= 0:
            continue
        if float(row.get("institutional_20d_avg_volume_ratio") or 0.0) <= 0:
            continue
        if float(row.get("price_change_20d") or 0.0) > max_price_change_20d:
            continue
        if float(row.get("volume_ratio") or 0.0) < min_volume_ratio:
            continue
        if float(row.get("latest_volume") or 0.0) < min_latest_volume:
            continue
        if float(row.get("close") or 0.0) < min_close:
            continue
        if row.get("low_liquidity") or row.get("margin_risk"):
            continue
        candidates.append(row)
    candidates.sort(
        key=lambda row: (
            bool(row.get("quiet_accumulation")),
            bool(row.get("foreign_reversal")),
            row["accumulation_score"],
            row["institutional_20d_avg_volume_ratio"],
            row["institutional_10d_sum"],
            row["trust_10d_sum"],
            row["foreign_10d_sum"],
        ),
        reverse=True,
    )
    for rank, row in enumerate(candidates[:radar_limit], start=1):
        row["radar_rank"] = rank
    return candidates[:radar_limit]


def build_exit_alerts(results: list[StockRow]) -> list[StockRow]:
    tracker = read_cache("strategy_tracker")
    history = tracker.get("history", []) if isinstance(tracker, dict) else []
    if not history:
        DATA_SOURCE_STATUS["exit_alerts"] = "empty"
        return []

    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    current_by_stock = {str(row.get("stock_id")): row for row in results}
    seen: set[str] = set()
    alerts: list[StockRow] = []
    lookback_days = env_int("AI_STOCK_EXIT_ALERT_LOOKBACK_DAYS", 20)

    for day_record in reversed(history[-lookback_days:]):
        record_date = str(day_record.get("date") or "")
        if record_date >= today:
            continue
        for entry in day_record.get("entries", []):
            stock_id = str(entry.get("stock_id") or "")
            if not stock_id or stock_id in seen or stock_id not in current_by_stock:
                continue
            row = current_by_stock[stock_id]
            reasons: list[str] = []
            close = float(row.get("close") or 0.0)
            ma20 = float(row.get("ma20") or 0.0)
            ma60 = float(row.get("ma60") or 0.0)
            price_change_1d = float(row.get("price_change_1d") or 0.0)
            latest_net = float(row.get("latest_institutional_net") or 0.0)
            prior_net = float(row.get("prior_institutional_net") or 0.0)
            yoy = float(row.get("yoy") or 0.0)
            acc_yoy = float(row.get("acc_yoy") or 0.0)
            event_flags = str(row.get("event_risk_flags") or "")

            if price_change_1d <= env_float("AI_STOCK_EXIT_ALERT_MAX_1D_DROP", -6.0):
                reasons.append("heavy_1d_drop")
            if ma20 > 0 and close < ma20:
                reasons.append("below_ma20")
            if ma60 > 0 and close < ma60:
                reasons.append("below_ma60")
            if latest_net < 0 and prior_net < 0:
                reasons.append("institutional_2d_selling")
            if yoy < 0 and acc_yoy < 0:
                reasons.append("revenue_growth_negative")
            if event_flags:
                reasons.append("event_risk")

            if not reasons:
                continue
            alert = row.copy()
            alert["exit_alert_reasons"] = ",".join(reasons)
            alert["exit_source"] = entry.get("source")
            alert["exit_entry_date"] = entry.get("date") or record_date
            alert["exit_entry_close"] = entry.get("entry_close")
            alert["exit_entry_rank"] = entry.get("rank")
            alerts.append(alert)
            seen.add(stock_id)

    alerts.sort(
        key=lambda row: (
            "below_ma60" in str(row.get("exit_alert_reasons") or ""),
            "heavy_1d_drop" in str(row.get("exit_alert_reasons") or ""),
            "institutional_2d_selling" in str(row.get("exit_alert_reasons") or ""),
            abs(float(row.get("price_change_1d") or 0.0)),
        ),
        reverse=True,
    )
    alerts = alerts[: env_int("AI_STOCK_EXIT_ALERT_LIMIT", 5)]
    for rank, row in enumerate(alerts, start=1):
        row["exit_alert_rank"] = rank
    DATA_SOURCE_STATUS["exit_alerts"] = f"count={len(alerts)}"
    return alerts


def apply_recommendation_history(
    top_stocks: list[StockRow],
    opportunity_stocks: list[StockRow],
    watchlist_stocks: list[StockRow],
    radar_stocks: list[StockRow],
) -> None:
    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    history = read_cache("recommendation_history") or {}
    stocks = history.get("stocks", {}) if isinstance(history, dict) else {}

    for row in top_stocks + opportunity_stocks + watchlist_stocks + radar_stocks:
        stock_id = str(row.get("stock_id", ""))
        previous = stocks.get(stock_id, {}) if isinstance(stocks, dict) else {}
        last_seen = previous.get("last_seen")
        previous_days = int(previous.get("consecutive_days") or 0)
        if last_seen == today:
            status = "same-day"
            consecutive = max(previous_days, 1)
        elif last_seen:
            try:
                last_seen_date = datetime.strptime(str(last_seen), "%Y-%m-%d").date()
                is_consecutive = (datetime.now(TAIPEI_TZ).date() - last_seen_date).days == 1
            except ValueError:
                is_consecutive = False
            consecutive = previous_days + 1 if is_consecutive else 1
            status = f"連續推薦{consecutive}天" if is_consecutive else "回榜"
        else:
            status = "新進榜"
            consecutive = 1
        row["history_status"] = status
        row["consecutive_days"] = consecutive

    updated_stocks = stocks.copy() if isinstance(stocks, dict) else {}
    for row in top_stocks + opportunity_stocks + watchlist_stocks + radar_stocks:
        updated_stocks[str(row["stock_id"])] = {
            "last_seen": today,
            "consecutive_days": int(row.get("consecutive_days") or 1),
            "last_rank": row.get("rank") or row.get("opportunity_rank") or row.get("watch_rank") or row.get("radar_rank"),
            "list": (
                "top"
                if row in top_stocks
                else "opportunity"
                if row in opportunity_stocks
                else "watchlist"
                if row in watchlist_stocks
                else "radar"
            ),
        }
    write_cache(
        "recommendation_history",
        {
            "updated_at": datetime.now(TAIPEI_TZ).isoformat(),
            "stocks": updated_stocks,
            "today_top": [row["stock_id"] for row in top_stocks],
            "today_opportunity": [row["stock_id"] for row in opportunity_stocks],
            "today_watchlist": [row["stock_id"] for row in watchlist_stocks],
            "today_radar": [row["stock_id"] for row in radar_stocks],
        },
    )


def build_v2_selection() -> tuple[list[StockRow], list[StockRow], list[StockRow], list[StockRow], list[StockRow]]:
    raw_rows = fetch_twse_monthly_revenue()
    revenue_rows = normalize_revenue(raw_rows)
    listed_count = sum(1 for row in revenue_rows if row.get("market_type") == "listed")
    otc_count = sum(1 for row in revenue_rows if row.get("market_type") == "otc")
    DATA_SOURCE_STATUS["pool"] = f"listed={listed_count} otc={otc_count} total={len(revenue_rows)}"
    margins = parse_income_margins()
    financial_quality = fetch_mops_financial_quality_map()
    fundamental_candidates = select_fundamental_candidates(revenue_rows, margins)
    institutional = fetch_institutional_maps()
    institutional_candidates = select_institutional_candidates(revenue_rows, margins, institutional, fundamental_candidates)
    candidates = merge_candidate_pools(fundamental_candidates, institutional_candidates)
    margin_map = fetch_margin_map()
    event_risk_map = fetch_event_risk_map()
    top_limit = env_int("AI_STOCK_TOP_LIMIT", 10)
    finmind_fundamental_limit = env_int("AI_STOCK_FINMIND_FUNDAMENTAL_LIMIT", 25)
    finmind_valuation_count = 0
    fallback_valuation_count = 0
    finmind_cash_flow_count = 0

    results: list[StockRow] = []
    for index, row in enumerate(candidates):
        enriched = row.copy()
        enriched["industry_theme"] = classify_industry_theme(enriched)
        enriched.update(
            event_risk_map.get(
                enriched["stock_id"],
                {"event_risk_level": "", "event_risk_flags": "", "event_risk_reason": ""},
            )
        )
        enriched.update(financial_quality.get(enriched["stock_id"], {}))
        enriched.update(calculate_technical_score(enriched["stock_id"]))
        if index < finmind_fundamental_limit:
            try:
                valuation_record = fetch_finmind_per_record(enriched["stock_id"])
                if valuation_record:
                    enriched.update(valuation_record)
                    if float(enriched.get("pe_ratio") or 0.0) > 0 or float(enriched.get("pbr") or 0.0) > 0:
                        finmind_valuation_count += 1
            except Exception:
                pass
            try:
                cash_flow_record = fetch_finmind_cash_flow_record(enriched["stock_id"])
                if cash_flow_record:
                    enriched.update(cash_flow_record)
                    finmind_cash_flow_count += 1
            except Exception:
                pass
        if fill_fallback_valuation(enriched):
            fallback_valuation_count += 1
        if enriched["stock_id"] not in institutional:
            try:
                records = fetch_finmind_institutional_records(enriched["stock_id"])
                if records:
                    institutional[enriched["stock_id"]] = records
                    DATA_SOURCE_STATUS["institutional"] = "TWSE+FinMind"
            except Exception:
                pass
        if enriched["stock_id"] not in margin_map:
            try:
                margin_record = fetch_finmind_margin_record(enriched["stock_id"])
                if margin_record:
                    margin_map[enriched["stock_id"]] = margin_record
                    DATA_SOURCE_STATUS["margin"] = "TWSE+FinMind"
            except Exception:
                pass
        enriched.update(calculate_chip_score(enriched["stock_id"], institutional))
        enriched.update(calculate_accumulation_score(enriched, institutional, margin_map))
        results.append(enriched)
    if finmind_valuation_count or fallback_valuation_count:
        DATA_SOURCE_STATUS["valuation"] = (
            f"FinMind PER/PBR rows={finmind_valuation_count} "
            f"fallback_mops={fallback_valuation_count}"
        )
    else:
        DATA_SOURCE_STATUS["valuation"] = "neutral_no_per_source"
    DATA_SOURCE_STATUS["cash_flow"] = (
        f"FinMind cashflow rows={finmind_cash_flow_count}"
        if finmind_cash_flow_count
        else "neutral_no_cashflow_source"
    )

    apply_industry_relative_strength(results)
    for row in results:
        ok, reason = evaluate_top_liquidity_quality(row, include_model_score=False)
        row["top_quality_pass"] = ok
        row["top_quality_reason"] = "" if ok else reason
        row["legacy_score"] = round(
            row["fundamental_score"]
            + row["technical_score"]
            + row["chip_score"]
            + float(row.get("industry_relative_score") or 0.0),
            2,
        )
        row["total_score"] = round(
            row["legacy_score"],
            2,
        )
    apply_institutional_growth_model(results)
    summarize_top_quality(results)
    calculate_market_score(results)
    summarize_industry_momentum(results)

    results.sort(
        key=lambda row: (
            row["total_score"],
            row["technical_score"],
            row["chip_score"],
            row["fundamental_score"],
            row["yoy"],
        ),
        reverse=True,
    )
    top_stocks, watchlist_stocks = split_top_and_watchlist(results, top_limit)
    for rank, row in enumerate(top_stocks, start=1):
        row["rank"] = rank
    opportunity_stocks = select_opportunity_stocks(results, top_stocks)
    opportunity_ids = {row["stock_id"] for row in opportunity_stocks}
    watchlist_stocks = [row for row in watchlist_stocks if row["stock_id"] not in opportunity_ids]
    for rank, row in enumerate(watchlist_stocks, start=1):
        row["watch_rank"] = rank
    ranked_for_exclusion = top_stocks + opportunity_stocks + watchlist_stocks
    radar_stocks = select_accumulation_radar(results, ranked_for_exclusion)
    exit_alerts = build_exit_alerts(results)
    ready, status = check_data_completeness(top_stocks + opportunity_stocks + watchlist_stocks + radar_stocks)
    if not ready:
        DATA_SOURCE_STATUS["data_completeness"] = status
    apply_recommendation_history(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks)
    return top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks, exit_alerts


def build_markdown_report(top_stocks: list[StockRow], radar_stocks: list[StockRow]) -> str:
    taipei_now = datetime.now(TAIPEI_TZ)
    lines = [
        "# AI Stock Bot V2 Top 10",
        "",
        f"Generated at: {taipei_now:%Y-%m-%d %H:%M} Taipei time",
        "",
        "Score: Fundamental 40 + Technical 35 + Chip 25.",
        "",
        "| Rank | Stock | Market | History | Strong | Total | Fundamental | Technical | Chip | YoY | MoM | Acc YoY | Gross | Op | Net | Close | Volume | Foreign 5D | Trust 5D |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in top_display:
        strong = "三率三升強推" if row.get("triple_margin_up") else ""
        lines.append(
            f"| {row['rank']} | {row['stock_id']} {row['stock_name']} | {row.get('market_type', '')} | {row.get('history_status', '')} | {strong} | {row['total_score']:.1f} | "
            f"{row['fundamental_score']:.1f} | {row['technical_score']:.1f} | {row['chip_score']:.1f} | "
            f"{row['yoy']:.1f}% | {row['mom']:.1f}% | {row['acc_yoy']:.1f}% | "
            f"{row['gross_margin']:.1f}% | {row['operating_margin']:.1f}% | {row['net_margin']:.1f}% | "
            f"{row['close']:.2f} | {row['volume_ratio']:.2f}x | {shares_to_lots(row['foreign_5d_sum']):,.0f}張 | {shares_to_lots(row['trust_5d_sum']):,.0f}張 |"
        )
        if len(watchlist_stocks) > len(watchlist_display):
            lines.append(f"另有 {len(watchlist_stocks) - len(watchlist_display)} 檔 Watchlist 未顯示")

    if radar_display:
        lines.extend([
            "",
            "## Accumulation Radar",
            "",
            "Radar focuses on institutional accumulation outside the Top 10, preferring steady foreign/trust buying, controlled 20-day price move, and moderate volume expansion.",
            "",
            "| Rank | Stock | Market | History | Radar | Quiet | F Reversal | Inst/AvgVol | Foreign 10D | Trust 10D | Buy Days | 20D Price | Volume | Margin |",
            "|---:|---|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for row in radar_display:
            quiet = "Yes" if row.get("quiet_accumulation") else ""
            reversal = "Yes" if row.get("foreign_reversal") else ""
            lines.append(
                f"| {row['radar_rank']} | {row['stock_id']} {row['stock_name']} | {row.get('market_type', '')} | {row.get('history_status', '')} | {row['accumulation_score']:.1f} | {quiet} | {reversal} | "
                f"{row['institutional_20d_avg_volume_ratio'] * 100:.1f}% | "
                f"{shares_to_lots(row['foreign_10d_sum']):,.0f}張 | {shares_to_lots(row['trust_10d_sum']):,.0f}張 | "
                f"F{row['foreign_10d_positive_days']}/T{row['trust_10d_positive_days']} | "
                f"{row['price_change_20d']:.1f}% | {row['volume_ratio']:.2f}x | {shares_to_lots(row['margin_buy_sell']):,.0f}張 |"
            )
    return "\n".join(lines) + "\n"


def readable_data_error(error: Exception) -> str:
    text = str(error)
    if isinstance(error, ValueError) and ("missing columns" in text or "Unexpected TWSE columns" in text):
        return "營收資料欄位格式異常，系統已改走備援資料源，請重新執行測試。"
    if "Monthly revenue unavailable" in text:
        return "月營收資料源暫時不可用，系統已保留狀態通知。"
    if "market close data not ready" in text:
        return "目前尚未到收盤資料可用時間，請於資料結算後重跑。"
    return f"資料源暫時無法完成分析（{type(error).__name__}）。"


def build_data_unavailable_message(error: Exception) -> str:
    taipei_now = datetime.now(TAIPEI_TZ)
    return "\n".join(
        [
            "AI Stock Bot V2",
            "====================",
            f"資料源暫時無法完整取得：{taipei_now:%Y-%m-%d %H:%M} 台北時間",
            "",
            "今日選股 Top10 與建倉雷達尚未完成計算。",
            "原因：TWSE/MOPS 公開資料源連線逾時或暫時無回應。",
            "",
            "處理方式：本次先推播狀態通知，避免排程靜默失敗；下一次排程或手動重跑會重新取得資料。",
            f"狀態摘要：{readable_data_error(error)}",
        ]
    )


def assert_market_close_ready() -> None:
    if env_bool("AI_STOCK_ALLOW_PRE_CLOSE_PUSH", False) or env_bool("AI_STOCK_ALLOW_INCOMPLETE_DATA_PUSH", False):
        return
    now = datetime.now(TAIPEI_TZ)
    ready_at = now.replace(hour=14, minute=30, second=0, microsecond=0)
    if now < ready_at:
        raise RuntimeError(
            f"Taiwan market close data not ready until 14:30 Taipei time; now={now:%Y-%m-%d %H:%M}"
        )


def is_pre_close_error(error: Exception) -> bool:
    return "market close data not ready" in str(error).lower()


def assert_data_completeness_ready() -> None:
    if env_bool("AI_STOCK_ALLOW_INCOMPLETE_DATA_PUSH", False):
        return
    status = DATA_SOURCE_STATUS.get("data_completeness", "unknown")
    if "ready=1" not in str(status):
        raise RuntimeError(f"Market data incomplete; {status}")


def ensure_finance_reference(message: str) -> str:
    reference = f"完整財經看板：{DAILY_FINANCE_REPORT_URL}"
    if DAILY_FINANCE_REPORT_URL in message:
        return message
    lines = message.splitlines()
    insert_at = 1 if lines else 0
    lines[insert_at:insert_at] = ["", reference, ""]
    return "\n".join(lines)


def trim_line_text(message: str) -> str:
    if len(message) <= LINE_TEXT_LIMIT:
        return message
    reference = f"完整財經看板：{DAILY_FINANCE_REPORT_URL}"
    suffix = f"\n\n訊息過長，已截斷。\n{reference}"
    if len(suffix) >= LINE_TEXT_LIMIT:
        return suffix[:LINE_TEXT_LIMIT]
    return message[: LINE_TEXT_LIMIT - len(suffix)] + suffix


def _status_has(status: str, keyword: str) -> bool:
    return keyword.lower() in str(status or "").lower()


def _status_value(status: str, key: str) -> str | None:
    prefix = f"{key}="
    for part in str(status or "").replace("/", " ").split():
        if part.startswith(prefix):
            return part[len(prefix):]
    return None


def _compact_pool_status(status: str) -> str:
    listed = _status_value(status, "listed")
    otc = _status_value(status, "otc")
    total = _status_value(status, "total")
    if listed and otc and total:
        return f"上市 {listed} / 上櫃 {otc} / 共 {total} 檔"
    return "上市與上櫃股票池"


def _compact_quality_status(status: str) -> str:
    passed = _status_value(status, "passed")
    failed = _status_value(status, "failed")
    fallback = _status_value(status, "fallback_used")
    if passed and failed:
        suffix = "，未使用放寬備援" if fallback == "0" else f"，備援補入 {fallback} 檔"
        return f"{passed} 檔通過高流動性/量能門檻，{failed} 檔未通過{suffix}"
    return "已套用流動性與量能品質門檻"


def _compact_tracker_status(status: str) -> str:
    entries_today = _status_value(status, "entries_today")
    updated = _status_value(status, "performance_updated")
    if entries_today and updated:
        return f"今日新增 {entries_today} 檔追蹤，更新 {updated} 筆後續績效"
    return "已寫入推薦追蹤紀錄"


def _compact_line_source_summary(top_stocks: list[StockRow]) -> list[str]:
    revenue_status = DATA_SOURCE_STATUS.get("revenue", "unknown")
    pool_status = DATA_SOURCE_STATUS.get("pool", "unknown")
    financial_status = DATA_SOURCE_STATUS.get("financial_quality", "unknown")
    valuation_status = DATA_SOURCE_STATUS.get("valuation", "unknown")
    cash_flow_status = DATA_SOURCE_STATUS.get("cash_flow", "unknown")
    top_quality_status = DATA_SOURCE_STATUS.get("top_quality", "unknown")
    top_quality_reasons = DATA_SOURCE_STATUS.get("top_quality_reasons", "unknown")
    industry_balance = DATA_SOURCE_STATUS.get("industry_balance", "unknown")
    data_completeness = DATA_SOURCE_STATUS.get("data_completeness", "unknown")
    event_risk = DATA_SOURCE_STATUS.get("event_risk", "unknown")
    tracker_status = DATA_SOURCE_STATUS.get("strategy_tracker", "unknown")

    source_items = []
    source_items.append(f"EventRisk={event_risk}")
    if _status_has(revenue_status, "MOPS"):
        source_items.append("營收 MOPS")
    elif _status_has(revenue_status, "cache"):
        source_items.append("營收快取備援")
    else:
        source_items.append("營收資料備援")
    source_items.extend(["股價 TWSE/TPEx", "法人與融資 TWSE/TPEx"])
    if _status_has(financial_status, "MOPS"):
        source_items.append("EPS/ROE MOPS")
    if _status_has(valuation_status, "neutral"):
        source_items.append("估值暫中性")
    if _status_has(cash_flow_status, "neutral"):
        source_items.append("現金流暫中性")

    a_count = sum(1 for row in top_stocks if row.get("model_grade") == "A")
    b_count = sum(1 for row in top_stocks if row.get("model_grade") == "B")
    c_count = sum(1 for row in top_stocks if row.get("model_grade") == "C")

    return [
        "資料摘要：" + "、".join(source_items),
        f"股票池：{_compact_pool_status(pool_status)}",
        f"資料完整性：{data_completeness}",
        f"品質門檻：{_compact_quality_status(top_quality_status)}",
        f"分散控管：{industry_balance}",
        f"條件診斷：{top_quality_reasons}",
        f"今日等級：A {a_count} / B {b_count} / C {c_count}",
        f"追蹤紀錄：{_compact_tracker_status(tracker_status)}",
    ]


def _line_grade_note(top_stocks: list[StockRow]) -> str:
    best_score = max(float(row.get("total_score") or 0.0) for row in top_stocks)
    best_grade = next((row.get("model_grade") for row in top_stocks if float(row.get("total_score") or 0.0) == best_score), "")
    if best_score >= 85:
        return "等級說明：A=可列入正式 Top10；B=觀察池；C=法人建倉雷達/觀察，不代表立即買進。"
    if best_score >= 75:
        return "等級說明：今日最高為 B 級，偏觀察名單；A=正式 Top10，C=法人建倉雷達/觀察。"
    return f"等級說明：今日最高為 {best_grade or 'C'} 級，沒有 A/B 強訊號；以下為觀察名單，不代表立即買進。"


REASON_LABELS = {
    "low_avg_volume": "20日均量不足",
    "low_latest_volume": "今日量不足",
    "low_turnover": "成交金額不足",
    "weak_volume_ratio": "量能偏弱",
    "overheated_volume_ratio": "量能過熱",
    "low_price": "股價過低",
    "heavy_1d_drop": "單日跌幅過重",
    "overheated_20d_price": "20日漲幅過熱",
    "yoy_over_300": "營收年增過熱",
    "small_revenue": "營收規模偏小",
    "margin_surge": "融資增幅偏高",
    "institutional_selling": "外資投信同步賣超",
    "eps_loss": "EPS虧損",
    "roe_below_5": "ROE偏低",
    "ocf_negative": "營業現金流為負",
    "fcf_negative": "自由現金流為負",
    "price_overheat": "股價短線過熱",
    "liquidity_fallback": "流動性未達正式門檻",
    "attention_stock": "交易所注意股",
    "disposition_stock": "交易所處置股",
    "observation": "觀察",
}

REASON_LABELS.update(
    {
        "institutional_2d_selling": "\u6cd5\u4eba\u9023\u7e8c2\u65e5\u8ce3\u8d85",
        "below_ma20": "\u8dcc\u7834\u6708\u7dda",
        "below_ma60": "\u8dcc\u7834\u5b63\u7dda",
        "revenue_growth_negative": "\u71df\u6536\u6210\u9577\u8f49\u8ca0",
        "event_risk": "\u4e8b\u4ef6\u98a8\u96aa",
        "eps_deceleration": "EPS\u6210\u9577\u6e1b\u901f",
        "eps_loss_no_growth": "EPS\u8667\u640d\u4e14\u71df\u6536\u672a\u9ad8\u6210\u9577",
    }
)

THEME_LABELS = {
    "semiconductor": "半導體",
    "semiconductor_equipment": "半導體設備",
    "electronics": "電子科技",
    "memory_storage": "記憶體/儲存",
    "construction_property": "營建資產",
    "unknown": "未分類",
}

MARKET_LABELS = {"listed": "上市", "otc": "上櫃", "unknown": "市場未明"}
HISTORY_LABELS = {
    "same-day": "今日新增",
    "new": "今日新增",
    "repeat": "連續追蹤",
    "re-entry": "重新進榜",
}


def _readable_count_status(status: str, labels: dict[str, str]) -> str:
    items = []
    for key, label in labels.items():
        value = _status_value(status, key)
        if value is not None:
            items.append(f"{label}{value}")
    return " / ".join(items) if items else "無資料"


def _readable_event_risk(status: str) -> str:
    return _readable_count_status(
        status,
        {
            "twse_attention": "注意股",
            "tpex_disposal": "上櫃處置",
            "tpex_esb_disposal": "興櫃處置",
        },
    )


def _readable_data_completeness(status: str) -> str:
    if status == "empty":
        return "本次沒有候選資料"
    ready = _status_value(status, "ready")
    price = format_trade_date(_status_value(status, "price") or "")
    institutional = format_trade_date(_status_value(status, "institutional") or "")
    missing_margin = _status_value(status, "missing_margin")
    if ready == "1":
        suffix = f"，融資缺 {missing_margin} 檔" if missing_margin and missing_margin != "0" else ""
        return f"已同步：股價 {price} / 法人 {institutional}{suffix}"
    return f"未同步：股價 {price or '缺'} / 法人 {institutional or '缺'}"


def _readable_valuation(status: str) -> str:
    finmind = _status_value(status, "rows")
    fallback = _status_value(status, "fallback_mops")
    if _status_has(status, "neutral"):
        return "估值：來源缺漏，採中性分"
    if fallback is not None:
        return f"估值：FinMind {finmind or '0'} 檔 / 自行回推 {fallback} 檔"
    return f"估值：{status}"


def _readable_cash_flow(status: str) -> str:
    if _status_has(status, "neutral"):
        return "現金流：來源缺漏，採中性分"
    return f"現金流：{status}"


def _readable_quality_status(status: str) -> str:
    passed = _status_value(status, "passed")
    failed = _status_value(status, "failed")
    fallback = _status_value(status, "fallback_used")
    if passed and failed:
        relaxed = "無放寬" if fallback == "0" else f"放寬 {fallback} 檔"
        return f"高流動性通過 {passed} 檔，未通過 {failed} 檔，{relaxed}"
    return "品質門檻狀態未明"


def _readable_candidate_pool(status: str) -> str:
    fundamental = _status_value(status, "fundamental")
    institutional = _status_value(status, "institutional")
    merged = _status_value(status, "merged")
    if fundamental and institutional and merged:
        return f"基本面 {fundamental} 檔 / 法人建倉 {institutional} 檔 / 合併分析 {merged} 檔"
    return "候選池狀態未明"


def _readable_reason_token(token: str) -> str:
    token = token.strip()
    if not token:
        return ""
    if "=" in token:
        key, value = token.split("=", 1)
        return f"{REASON_LABELS.get(key, key)} {value}"
    return REASON_LABELS.get(token, token)


def _readable_reason_text(value: Any, limit: int = 4) -> str:
    if isinstance(value, list):
        tokens = [str(item) for item in value]
    else:
        tokens = []
        for part in str(value or "").replace(" / ", ",").split(","):
            tokens.append(part)
    labels = []
    for token in tokens:
        label = _readable_reason_token(token)
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return "觀察"
    if len(labels) > limit:
        return "、".join(labels[:limit]) + f" 等{len(labels)}項"
    return "、".join(labels)


def _readable_industry_balance(status: str) -> str:
    text = str(status or "")
    if not text or text == "unknown":
        return "分散狀態未明"
    text = text.replace("industries:", "產業 ")
    text = text.replace(" themes:", "；題材 ")
    for key, label in THEME_LABELS.items():
        text = text.replace(key, label)
    text = text.replace("=", " ")
    return text


def _readable_theme(value: Any) -> str:
    text = str(value or "").strip()
    return THEME_LABELS.get(text, text or "未分類")


def _readable_market_score(status: str) -> str:
    text = str(status or "")
    if not text or text == "unknown":
        return "大盤資料不足，暫不調整個股分數"
    score = _status_value(text, "score") or "未知"
    regime = _status_value(text, "regime") or ""
    regime_label = {
        "risk_on": "偏多",
        "constructive": "中性偏多",
        "neutral": "中性",
        "defensive": "防守",
    }.get(regime, regime or "未判定")
    note_labels = {
        "proxy_above_ma20": "0050站上月線",
        "proxy_below_ma20": "0050跌破月線",
        "proxy_above_ma60": "0050站上季線",
        "proxy_below_ma60": "0050跌破季線",
        "proxy_20d_positive": "20日趨勢偏多",
        "proxy_20d_negative": "20日趨勢偏弱",
        "proxy_volume_confirm": "量價確認",
        "candidate_breadth_ok": "候選池廣度正常",
        "candidate_breadth_weak": "候選池廣度偏弱",
        "qualified_names_ok": "達標標的足夠",
        "candidate_median_weak": "候選股中位數偏弱",
    }
    notes_text = ""
    notes = _status_value(text, "notes")
    if notes:
        labels = [note_labels.get(note.strip(), note.strip()) for note in str(notes).split(",") if note.strip()]
        notes_text = f"，{ '、'.join(labels[:4]) }" if labels else ""
    return f"{score} / {regime_label}{notes_text}"


def _readable_industry_momentum(status: str) -> str:
    text = str(status or "")
    if not text or text in {"unknown", "empty"}:
        return "暫無明顯產業輪動"
    items = []
    for part in text.split(" | "):
        if ":" not in part:
            continue
        theme, metrics = part.split(":", 1)
        values = metrics.split("/")
        price = values[0] if len(values) > 0 else ""
        volume = values[1] if len(values) > 1 else ""
        lots = values[2].replace("lots", "張") if len(values) > 2 else ""
        label = _readable_theme(theme)
        summary = " / ".join(item for item in [f"20日{price}", f"量比{volume}" if volume else "", f"法人{lots}" if lots else ""] if item)
        items.append(f"{label}：{summary}")
    return "；".join(items) if items else "暫無明顯產業輪動"


def _readable_exit_alert_status(status: str) -> str:
    text = str(status or "")
    if not text or text in {"unknown", "empty"}:
        return "無"
    count = _status_value(text, "count")
    return f"{count} 檔需追蹤" if count else text


def _readable_market_meta(row: StockRow) -> str:
    market = MARKET_LABELS.get(str(row.get("market_type") or ""), str(row.get("market_type") or ""))
    history = HISTORY_LABELS.get(str(row.get("history_status") or ""), str(row.get("history_status") or ""))
    return "｜".join(item for item in [market, history] if item)


def _readable_top_summary(rows: list[StockRow]) -> list[str]:
    a_count = sum(1 for row in rows if row.get("model_grade") == "A")
    b_count = sum(1 for row in rows if row.get("model_grade") == "B")
    c_count = sum(1 for row in rows if row.get("model_grade") == "C")
    return [
        f"大盤濾網：{_readable_market_score(DATA_SOURCE_STATUS.get('market_score', 'unknown'))}",
        f"產業輪動：{_readable_industry_momentum(DATA_SOURCE_STATUS.get('industry_momentum', 'unknown'))}",
        f"今日等級：A {a_count} / B {b_count} / C {c_count}",
        f"Exit Alert：{_readable_exit_alert_status(DATA_SOURCE_STATUS.get('exit_alerts', 'unknown'))}",
        f"追蹤紀錄：{_compact_tracker_status(DATA_SOURCE_STATUS.get('strategy_tracker', 'unknown'))}",
    ]


def format_trade_date(value: Any) -> str:
    normalized = normalize_trade_date(value)
    if len(normalized) == 8:
        return f"{normalized[:4]}-{normalized[4:6]}-{normalized[6:]}"
    return str(value or "")


def build_ai_review_lines(
    top_stocks: list[StockRow],
    opportunity_stocks: list[StockRow],
    watchlist_stocks: list[StockRow],
    radar_stocks: list[StockRow],
) -> list[str]:
    rows: list[StockRow] = []
    seen: set[str] = set()
    for source_rows in [top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks]:
        for row in source_rows:
            stock_id = str(row.get("stock_id") or "")
            if stock_id and stock_id not in seen and row.get("ai_review"):
                rows.append(row)
                seen.add(stock_id)
            if len(rows) >= env_int("AI_STOCK_LINE_AI_REVIEW_DISPLAY_LIMIT", 3):
                break
        if len(rows) >= env_int("AI_STOCK_LINE_AI_REVIEW_DISPLAY_LIMIT", 3):
            break
    if not rows:
        return []
    lines = ["AI質化摘要：僅輔助候選股審查，不取代量化分數"]
    for row in rows:
        review_line = format_ai_review(row)
        if review_line:
            lines.append(f"{row.get('stock_id')} {row.get('stock_name')}｜{review_line}")
    return lines


def build_line_message(
    top_stocks: list[StockRow],
    opportunity_stocks: list[StockRow],
    watchlist_stocks: list[StockRow],
    radar_stocks: list[StockRow],
    exit_alerts: list[StockRow],
) -> str:
    rows_for_grade = top_stocks or opportunity_stocks or watchlist_stocks or radar_stocks or exit_alerts
    if not rows_for_grade:
        return "AI Stock Bot V2\n====================\n今日沒有股票通過篩選。"

    lines = [
        "AI Stock Bot V2 分層觀察",
        f"完整財經看板：{DAILY_FINANCE_REPORT_URL}",
        "",
        _line_grade_note(rows_for_grade),
        "模型：基本面35 + 法人籌碼25 + 技術面20 + 估值10 + 產業10",
        "====================",
    ]
    lines.extend(_readable_top_summary(rows_for_grade))
    lines.append("====================")
    ai_review_lines = build_ai_review_lines(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks)
    if ai_review_lines:
        lines.extend(ai_review_lines)
        lines.append("====================")
    top_display = top_stocks[: env_int("AI_STOCK_LINE_TOP_DISPLAY_LIMIT", 5)]
    opportunity_display = opportunity_stocks[: env_int("AI_STOCK_LINE_OPPORTUNITY_DISPLAY_LIMIT", 3)]
    watchlist_display = watchlist_stocks[: env_int("AI_STOCK_LINE_WATCH_DISPLAY_LIMIT", 3)]
    radar_display = radar_stocks[: env_int("AI_STOCK_LINE_RADAR_DISPLAY_LIMIT", 3)]
    exit_display = exit_alerts[: env_int("AI_STOCK_LINE_EXIT_DISPLAY_LIMIT", 3)]

    lines.append("正式 Top：品質通過、C級以上、無主要風險扣分" if top_stocks else "正式 Top：今日沒有完全通過品質與風險條件的標的")
    for row in top_display:
        rank = int(row["rank"])
        breakout = "創120日高" if row.get("break_120d_high") else "未創高"
        strong = " 三率三升" if row.get("triple_margin_up") else ""
        lines.extend([
            f"{rank}. {row['stock_id']} {row['stock_name']}{strong}",
            f"{row.get('model_grade', '')}級｜總分 {row['total_score']:.1f}｜基本 {row['fundamental_score']:.1f} 技術 {row['technical_score']:.1f} 籌碼 {row['chip_score']:.1f} 估值 {row.get('valuation_score', 0):.1f} 產業 {row.get('industry_score', 0):.1f} EPS加速 {row.get('eps_acceleration_score', 0):.1f}",
            f"{_readable_market_meta(row)}｜營收 YoY {row['yoy']:.1f}% / 累計 {row['acc_yoy']:.1f}%｜1日 {row.get('price_change_1d', 0):.1f}%｜20日 {row['price_change_20d']:.1f}%｜量比 {row['volume_ratio']:.2f}x｜{breakout}｜{_readable_theme(row.get('industry_theme'))}",
            f"外資5日 {shares_to_lots(row['foreign_5d_sum']):,.0f}張｜投信5日 {shares_to_lots(row['trust_5d_sum']):,.0f}張｜MA {row['ma5']:.1f}>{row['ma20']:.1f}>{row['ma60']:.1f}",
        ])

    if len(top_stocks) > len(top_display):
        lines.append(f"另有 {len(top_stocks) - len(top_display)} 檔正式 Top 未顯示")

    if opportunity_display:
        lines.extend(["====================", "機會股：法人早期建倉、基本面成長，仍需觀察風險與進場節奏"])
        for row in opportunity_display:
            lines.extend([
                f"O{row['opportunity_rank']} {row['stock_id']} {row['stock_name']}｜機會分 {row.get('opportunity_score', 0):.1f}｜{row.get('model_grade', '')}級 總分 {row['total_score']:.1f}",
                f"理由：{row.get('opportunity_reason') or '法人/基本面早期訊號'}",
                f"{_readable_theme(row.get('industry_theme'))}｜1日 {row.get('price_change_1d', 0):.1f}%｜20日 {row['price_change_20d']:.1f}%｜量比 {row['volume_ratio']:.2f}x｜外資10日 {shares_to_lots(row['foreign_10d_sum']):,.0f}張｜投信10日 {shares_to_lots(row['trust_10d_sum']):,.0f}張",
            ])

        if len(opportunity_stocks) > len(opportunity_display):
            lines.append(f"另有 {len(opportunity_stocks) - len(opportunity_display)} 檔機會股未顯示")

    if watchlist_display:
        lines.extend(["====================", "Watchlist：分數有潛力，但品質、風險或分散條件未達正式 Top"])
        for row in watchlist_display:
            reason_values = [
                row.get("top_quality_reason"),
                row.get("risk_notes", []),
                row.get("event_risk_reason"),
            ]
            reason = "；".join(_readable_reason_text(item) for item in reason_values if item)
            lines.extend([
                f"W{row['watch_rank']} {row['stock_id']} {row['stock_name']}｜{row.get('model_grade', '')}級｜總分 {row['total_score']:.1f}",
                f"原因：{reason or '觀察'}",
                f"1日 {row.get('price_change_1d', 0):.1f}%｜20日 {row['price_change_20d']:.1f}%｜{_readable_theme(row.get('industry_theme'))}｜外資5日 {shares_to_lots(row['foreign_5d_sum']):,.0f}張｜投信5日 {shares_to_lots(row['trust_5d_sum']):,.0f}張",
            ])

    if radar_display:
        lines.extend([
            "====================",
            "法人建倉雷達",
            "條件：Top/機會股之外，最近法人未反手賣超、未重跌或跌停，且通過基本面底線。",
        ])
        for row in radar_display:
            quiet = "｜低調建倉" if row.get("quiet_accumulation") else "｜籌碼觀察"
            lines.extend([
                f"R{row['radar_rank']} {row['stock_id']} {row['stock_name']}｜雷達 {row['accumulation_score']:.1f}{quiet}",
                f"外資1日 {shares_to_lots(row.get('latest_foreign_net', 0)):,.0f}張｜投信1日 {shares_to_lots(row.get('latest_trust_net', 0)):,.0f}張｜近2日法人 {shares_to_lots(row.get('recent_2d_institutional_net', 0)):,.0f}張",
                f"10日外資/投信 {shares_to_lots(row['foreign_10d_sum']):,.0f}/{shares_to_lots(row['trust_10d_sum']):,.0f}張｜占量 {row['institutional_20d_avg_volume_ratio'] * 100:.1f}%｜20日 {row['price_change_20d']:.1f}%｜量比 {row['volume_ratio']:.2f}x",
            ])
        if len(radar_stocks) > len(radar_display):
            lines.append(f"另有 {len(radar_stocks) - len(radar_display)} 檔法人建倉雷達未顯示")
    if exit_display:
        lines.extend(["====================", "Exit Alert 持股風險追蹤", "條件：曾列入追蹤名單，今日出現跌破均線、法人轉賣、重跌或營收轉弱。"])
        for row in exit_display:
            lines.extend([
                f"E{row['exit_alert_rank']} {row['stock_id']} {row['stock_name']}｜來源 {row.get('exit_source', 'tracked')}",
                f"風險：{_readable_reason_text(row.get('exit_alert_reasons'), limit=5)}",
                f"1日 {row.get('price_change_1d', 0):.1f}%｜20日 {row.get('price_change_20d', 0):.1f}%｜MA20 {row.get('ma20', 0):.1f}｜近2日法人 {shares_to_lots(row.get('recent_2d_institutional_net', 0)):,.0f}張",
            ])
    return "\n".join(lines)


def push_line_message(message: str) -> None:
    message = ensure_finance_reference(message)
    if env_bool("LINE_TEST_PUSH") and not message.startswith("[TEST]"):
        message = f"[TEST] {message}"
    payload_text = trim_line_text(message)

    channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    line_to = os.getenv("LINE_TO")
    use_broadcast = env_bool("LINE_BROADCAST", False)
    if not channel_access_token:
        raise ValueError("Missing LINE_CHANNEL_ACCESS_TOKEN.")

    if use_broadcast:
        endpoint = LINE_BROADCAST_URL
        payload_obj = {"messages": [{"type": "text", "text": payload_text}]}
    else:
        if not line_to:
            raise ValueError("Missing LINE_TO. Use a LINE userId or groupId, or set LINE_BROADCAST=true.")
        endpoint = LINE_PUSH_URL
        payload_obj = {"to": line_to, "messages": [{"type": "text", "text": payload_text}]}

    print(
        "LINE_MESSAGE_CHECK "
        f"finance_url={DAILY_FINANCE_REPORT_URL in payload_text} "
        f"test_push={payload_text.startswith('[TEST]')} "
        f"payload_length={len(payload_text)}"
    )

    payload = json.dumps(payload_obj, ensure_ascii=False).encode("utf-8")

    request = Request(
        endpoint,
        data=payload,
        headers={
            "Authorization": f"Bearer {channel_access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=60):
            return
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        mode = "broadcast" if use_broadcast else "push"
        raise RuntimeError(f"LINE Bot {mode} failed: HTTP {error.code} {details}") from error


def compact_for_gemini(row: StockRow, bucket: str) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "stock_id": row.get("stock_id"),
        "stock_name": row.get("stock_name"),
        "grade": row.get("model_grade"),
        "total_score": row.get("total_score"),
        "fundamental_score": row.get("fundamental_score"),
        "technical_score": row.get("technical_score"),
        "chip_score": row.get("chip_score"),
        "valuation_score": row.get("valuation_score"),
        "industry_score": row.get("industry_score"),
        "eps_acceleration_score": row.get("eps_acceleration_score"),
        "industry": row.get("industry_category"),
        "theme": row.get("industry_theme"),
        "monthly_revenue_yoy": row.get("yoy"),
        "monthly_revenue_mom": row.get("mom"),
        "accumulated_revenue_yoy": row.get("acc_yoy"),
        "eps": row.get("eps"),
        "roe": row.get("roe"),
        "gross_margin": row.get("gross_margin"),
        "operating_margin": row.get("operating_margin"),
        "net_margin": row.get("net_margin"),
        "pe_ratio": row.get("pe_ratio"),
        "pbr": row.get("pbr"),
        "price_change_1d": row.get("price_change_1d"),
        "price_change_20d": row.get("price_change_20d"),
        "volume_ratio": row.get("volume_ratio"),
        "foreign_5d_lots": shares_to_lots(row.get("foreign_5d_sum", 0)),
        "trust_5d_lots": shares_to_lots(row.get("trust_5d_sum", 0)),
        "foreign_10d_lots": shares_to_lots(row.get("foreign_10d_sum", 0)),
        "trust_10d_lots": shares_to_lots(row.get("trust_10d_sum", 0)),
        "latest_foreign_lots": shares_to_lots(row.get("latest_foreign_net", 0)),
        "latest_trust_lots": shares_to_lots(row.get("latest_trust_net", 0)),
        "risk_notes": row.get("risk_notes"),
        "event_risk_reason": row.get("event_risk_reason"),
        "top_quality_reason": row.get("top_quality_reason"),
        "opportunity_reason": row.get("opportunity_reason"),
    }


def gemini_prompt(candidates: list[dict[str, Any]]) -> str:
    payload = {
        "instructions": [
            "你是台股候選股質化審查員，只能根據提供的資料判斷，不可編造新聞、法說會或未提供的財報文字。",
            "請輔助既有量化模型，不要給買賣指令，不要用保證語氣。",
            "重點檢查：財務品質、月營收可持續性、法人籌碼是否一致、短線題材或過熱風險、是否需要等待量縮或回測。",
            "若缺少新聞、法說會或財報文字來源，請在風險中用簡短文字指出。",
            "只回 JSON array，每個元素包含 stock_id, catalyst, risk, sustainability, theme_quality, confidence, summary。",
            "confidence 使用 0-10 整數；summary 限 45 個中文字內。",
        ],
        "candidates": candidates,
    }
    return json.dumps(payload, ensure_ascii=False, default=str)


def parse_json_array_from_text(text: str) -> list[dict[str, Any]]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, list):
        raise ValueError("Gemini response is not a JSON array.")
    return [item for item in parsed if isinstance(item, dict)]


def request_gemini_reviews(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        DATA_SOURCE_STATUS["gemini_review"] = "disabled missing_key"
        return []
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": gemini_prompt(candidates)}],
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }
    timeout = env_int("GEMINI_TIMEOUT_SEC", 30)
    models = [
        model.strip()
        for model in env_str("GEMINI_MODEL_SEQUENCE", env_str("GEMINI_MODEL", "gemini-2.5-flash-lite,gemini-2.5-flash")).split(",")
        if model.strip()
    ]
    last_error: Exception | None = None
    for model in models:
        endpoint = GEMINI_GENERATE_CONTENT_URL.format(model=model)
        url = f"{endpoint}?{urlencode({'key': api_key})}"
        request = Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        for attempt in range(1, env_int("GEMINI_RETRY", 2) + 1):
            try:
                with urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                text = (
                    payload.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                reviews = parse_json_array_from_text(text)
                DATA_SOURCE_STATUS["gemini_review"] = f"enabled reviewed={len(reviews)} model={model}"
                return reviews
            except HTTPError as error:
                last_error = RuntimeError(f"model={model} attempt={attempt} http={error.code}")
                if error.code not in {429, 500, 502, 503, 504}:
                    raise last_error from error
            except Exception as error:
                last_error = error
            time.sleep(2 * attempt)
    raise RuntimeError(f"Gemini review failed after fallbacks: {last_error}")


def attach_gemini_reviews(
    top_stocks: list[StockRow],
    opportunity_stocks: list[StockRow],
    watchlist_stocks: list[StockRow],
    radar_stocks: list[StockRow],
) -> None:
    limit = env_int("AI_STOCK_GEMINI_REVIEW_LIMIT", 6)
    if limit <= 0:
        DATA_SOURCE_STATUS["gemini_review"] = "disabled limit=0"
        return

    ordered: list[tuple[str, StockRow]] = []
    for bucket, rows in [
        ("top", top_stocks),
        ("opportunity", opportunity_stocks),
        ("watchlist", watchlist_stocks),
        ("radar", radar_stocks),
    ]:
        for row in rows:
            stock_id = str(row.get("stock_id") or "")
            if stock_id and all(str(existing.get("stock_id") or "") != stock_id for _, existing in ordered):
                ordered.append((bucket, row))
            if len(ordered) >= limit:
                break
        if len(ordered) >= limit:
            break

    if not ordered:
        DATA_SOURCE_STATUS["gemini_review"] = "empty"
        return

    candidates = [compact_for_gemini(row, bucket) for bucket, row in ordered]
    try:
        reviews = request_gemini_reviews(candidates)
    except Exception as error:
        DATA_SOURCE_STATUS["gemini_review"] = f"failed {type(error).__name__}"
        print(f"GEMINI_REVIEW_FAILED {type(error).__name__}: {error}")
        return

    if not reviews:
        if DATA_SOURCE_STATUS.get("gemini_review") == "disabled missing_key":
            return
        DATA_SOURCE_STATUS["gemini_review"] = "enabled reviewed=0"
        return

    by_stock = {str(item.get("stock_id") or ""): item for item in reviews}
    attached = 0
    for _, row in ordered:
        review = by_stock.get(str(row.get("stock_id") or ""))
        if not review:
            continue
        summary = str(review.get("summary") or "").strip()
        risk = str(review.get("risk") or "").strip()
        confidence = review.get("confidence")
        row["ai_review"] = {
            "summary": summary[:90],
            "risk": risk[:80],
            "confidence": confidence,
            "catalyst": str(review.get("catalyst") or "").strip()[:80],
            "sustainability": str(review.get("sustainability") or "").strip()[:80],
            "theme_quality": str(review.get("theme_quality") or "").strip()[:80],
        }
        attached += 1
    DATA_SOURCE_STATUS["gemini_review"] = f"enabled reviewed={len(reviews)} attached={attached}"


def format_ai_review(row: StockRow) -> str | None:
    review = row.get("ai_review")
    if not isinstance(review, dict):
        return None
    summary = str(review.get("summary") or "").strip()
    if not summary:
        return None
    confidence = review.get("confidence")
    try:
        confidence_text = f"{float(confidence):.0f}/10"
    except (TypeError, ValueError):
        confidence_text = "?/10"
    return f"AI摘要：{summary}｜信心 {confidence_text}"


def write_csv(path: Path, rows: list[StockRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=KEEP_COLS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def strategy_entry_tags(row: StockRow, source: str) -> list[str]:
    tags = [source]
    if float(row.get("fundamental_score") or 0.0) >= 35:
        tags.append("fundamental_high")
    if float(row.get("technical_score") or 0.0) >= 20:
        tags.append("technical_positive")
    if float(row.get("chip_score") or 0.0) >= 12:
        tags.append("chip_positive")
    if row.get("quiet_accumulation"):
        tags.append("quiet_accumulation")
    if row.get("foreign_reversal"):
        tags.append("foreign_reversal")
    if row.get("top_quality_pass"):
        tags.append("liquidity_pass")
    return tags


def compute_entry_performance(stock_id: str, entry_date: str, entry_close: float) -> dict[str, Any]:
    if not entry_date or entry_close <= 0:
        return {}
    records = fetch_price_history(stock_id, months=8)
    after_entry = [row for row in records if str(row.get("date", "")) >= entry_date and float(row.get("close") or 0.0) > 0]
    if not after_entry:
        return {}

    closes = [float(row["close"]) for row in after_entry]

    def return_at(index: int) -> float | None:
        if len(closes) <= index:
            return None
        return round((closes[index] / entry_close - 1) * 100, 2)

    current_return = round((closes[-1] / entry_close - 1) * 100, 2)
    max_return = round((max(closes) / entry_close - 1) * 100, 2)
    max_drawdown = round((min(closes) / entry_close - 1) * 100, 2)
    latest_close = closes[-1]
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)

    return {
        "last_eval_date": str(after_entry[-1].get("date", "")),
        "holding_days_observed": len(closes) - 1,
        "latest_close": round(latest_close, 2),
        "return_1d_pct": return_at(1),
        "return_5d_pct": return_at(5),
        "return_20d_pct": return_at(20),
        "current_return_pct": current_return,
        "max_return_pct": max_return,
        "max_drawdown_pct": max_drawdown,
        "stop_loss_hit": max_drawdown <= -8.0,
        "profit_trailing_active": max_return >= 30.0,
        "below_ma20": bool(ma20 and latest_close < ma20),
        "below_ma60": bool(ma60 and latest_close < ma60),
    }


def update_strategy_performance(history: list[dict[str, Any]], today: str) -> tuple[list[dict[str, Any]], int]:
    max_updates = env_int("AI_STOCK_TRACKER_MAX_PER_RUN", 30)
    updated = 0
    price_failures = 0
    history_by_stock: dict[str, list[dict[str, float]]] = {}

    for day_record in reversed(history):
        if updated >= max_updates:
            break
        if day_record.get("date") >= today:
            continue
        for entry in day_record.get("entries", []):
            if updated >= max_updates:
                break
            stock_id = str(entry.get("stock_id") or "")
            entry_close = float(entry.get("entry_close") or 0.0)
            if not stock_id or entry_close <= 0:
                continue
            try:
                if stock_id not in history_by_stock:
                    history_by_stock[stock_id] = fetch_price_history(stock_id, months=8)
                records = history_by_stock[stock_id]
                after_entry = [
                    row
                    for row in records
                    if str(row.get("date", "")) >= str(entry.get("date") or "")
                    and float(row.get("close") or 0.0) > 0
                ]
                if not after_entry:
                    continue
                closes = [float(row["close"]) for row in after_entry]

                def ret(index: int) -> float | None:
                    if len(closes) <= index:
                        return entry.get(f"return_{index}d_pct")
                    return round((closes[index] / entry_close - 1) * 100, 2)

                latest_close = closes[-1]
                max_return = round((max(closes) / entry_close - 1) * 100, 2)
                max_drawdown = round((min(closes) / entry_close - 1) * 100, 2)
                ma20 = moving_average(closes, 20)
                ma60 = moving_average(closes, 60)
                entry["performance"] = {
                    "last_eval_date": str(after_entry[-1].get("date", "")),
                    "holding_days_observed": len(closes) - 1,
                    "latest_close": round(latest_close, 2),
                    "return_1d_pct": ret(1),
                    "return_5d_pct": ret(5),
                    "return_20d_pct": ret(20),
                    "current_return_pct": round((latest_close / entry_close - 1) * 100, 2),
                    "max_return_pct": max_return,
                    "max_drawdown_pct": max_drawdown,
                    "stop_loss_hit": max_drawdown <= -8.0,
                    "profit_trailing_active": max_return >= 30.0,
                    "below_ma20": bool(ma20 and latest_close < ma20),
                    "below_ma60": bool(ma60 and latest_close < ma60),
                }
                updated += 1
            except Exception:
                price_failures += 1
                continue

    DATA_SOURCE_STATUS["strategy_tracker"] = f"performance_updated={updated} price_failures={price_failures}"
    return history, updated


def write_strategy_tracker(
    top_stocks: list[StockRow],
    opportunity_stocks: list[StockRow],
    watchlist_stocks: list[StockRow],
    radar_stocks: list[StockRow],
    exit_alerts: list[StockRow],
) -> None:
    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    existing = read_cache("strategy_tracker")
    history = existing.get("history", []) if isinstance(existing, dict) else []
    history = [item for item in history if item.get("date") != today]
    history, performance_updated = update_strategy_performance(history, today)

    def serialize(row: StockRow, source: str) -> dict[str, Any]:
        return {
            "date": today,
            "source": source,
            "rank": row.get("rank") or row.get("opportunity_rank") or row.get("watch_rank") or row.get("radar_rank"),
            "stock_id": row.get("stock_id"),
            "stock_name": row.get("stock_name"),
            "candidate_source": row.get("candidate_source"),
            "institutional_candidate_score": row.get("institutional_candidate_score"),
            "institutional_candidate_reason": row.get("institutional_candidate_reason"),
            "market_type": row.get("market_type"),
            "industry_category": row.get("industry_category"),
            "industry_theme": row.get("industry_theme"),
            "entry_close": row.get("close"),
            "latest_trade_date": row.get("latest_trade_date"),
            "institutional_trade_date": row.get("institutional_trade_date"),
            "margin_trade_date": row.get("margin_trade_date"),
            "entry_volume_ratio": row.get("volume_ratio"),
            "entry_volume_20d_avg": row.get("volume_20d_avg"),
            "entry_price_change_1d": row.get("price_change_1d"),
            "entry_price_change_20d": row.get("price_change_20d"),
            "model_version": row.get("model_version"),
            "model_grade": row.get("model_grade"),
            "total_score": row.get("total_score"),
            "legacy_score": row.get("legacy_score"),
            "fundamental_score": row.get("fundamental_score"),
            "valuation_score": row.get("valuation_score"),
            "technical_score": row.get("technical_score"),
            "chip_score": row.get("chip_score"),
            "industry_score": row.get("industry_score"),
            "eps_acceleration_score": row.get("eps_acceleration_score"),
            "eps_acceleration_note": row.get("eps_acceleration_note"),
            "eps_change_pct": row.get("eps_change_pct"),
            "risk_penalty": row.get("risk_penalty"),
            "opportunity_score": row.get("opportunity_score"),
            "opportunity_reason": row.get("opportunity_reason"),
            "industry_priority": row.get("industry_priority"),
            "risk_notes_model": row.get("risk_notes", []),
            "eps": row.get("eps"),
            "net_income": row.get("net_income"),
            "equity": row.get("equity"),
            "roe": row.get("roe"),
            "pe_ratio": row.get("pe_ratio"),
            "pbr": row.get("pbr"),
            "operating_cash_flow": row.get("operating_cash_flow"),
            "free_cash_flow": row.get("free_cash_flow"),
            "cash_flow_score": row.get("cash_flow_score"),
            "cash_flow_quality": row.get("cash_flow_quality"),
            "accumulation_score": row.get("accumulation_score"),
            "industry_relative_score": row.get("industry_relative_score"),
            "foreign_5d_sum": row.get("foreign_5d_sum"),
            "trust_5d_sum": row.get("trust_5d_sum"),
            "foreign_10d_sum": row.get("foreign_10d_sum"),
            "trust_10d_sum": row.get("trust_10d_sum"),
            "latest_foreign_net": row.get("latest_foreign_net"),
            "latest_trust_net": row.get("latest_trust_net"),
            "latest_institutional_net": row.get("latest_institutional_net"),
            "prior_institutional_net": row.get("prior_institutional_net"),
            "recent_2d_institutional_net": row.get("recent_2d_institutional_net"),
            "radar_exclusion_reason": row.get("radar_exclusion_reason"),
            "exit_alert_reasons": row.get("exit_alert_reasons"),
            "top_quality_pass": row.get("top_quality_pass"),
            "top_quality_reason": row.get("top_quality_reason"),
            "entry_plan": row.get("entry_plan"),
            "exit_plan": row.get("exit_plan"),
            "entry_tags": strategy_entry_tags(row, source),
            "risk_notes": [
                note
                for note, active in [
                    ("watchlist_quality_gap", source == "watchlist" and not row.get("top_quality_pass")),
                    ("opportunity_quality_gap", source == "opportunity" and not row.get("top_quality_pass")),
                    ("margin_risk", bool(row.get("margin_risk"))),
                    ("low_liquidity", bool(row.get("low_liquidity"))),
                    ("heavy_1d_drop", float(row.get("price_change_1d") or 0.0) < -4.0),
                    ("overheated_20d_price", float(row.get("price_change_20d") or 0.0) > 35),
                ]
                if active
            ],
        }

    entries = (
        [serialize(row, "top") for row in top_stocks]
        + [serialize(row, "opportunity") for row in opportunity_stocks]
        + [serialize(row, "watchlist") for row in watchlist_stocks]
        + [serialize(row, "radar") for row in radar_stocks]
    )
    history.append({"date": today, "entries": entries})
    history = history[-90:]
    write_cache(
        "strategy_tracker",
        {
            "updated_at": datetime.now(TAIPEI_TZ).isoformat(),
            "schema": "strategy_tracker_v1",
            "exit_alerts_today": [
                {
                    "rank": row.get("exit_alert_rank"),
                    "stock_id": row.get("stock_id"),
                    "stock_name": row.get("stock_name"),
                    "reasons": row.get("exit_alert_reasons"),
                    "source": row.get("exit_source"),
                }
                for row in exit_alerts
            ],
            "history": history,
        },
    )
    DATA_SOURCE_STATUS["strategy_tracker"] = (
        f"days={len(history)} entries_today={len(entries)} performance_updated={performance_updated}"
    )


def print_run_metrics(phase: str, started_at: datetime, started_perf: float, counts: dict[str, int]) -> None:
    elapsed = time.perf_counter() - started_perf
    finished_at = datetime.now(TAIPEI_TZ)
    print(
        "AI_STOCK_RUN_METRICS "
        f"phase={phase} "
        f"started_at={started_at.isoformat()} "
        f"finished_at={finished_at.isoformat()} "
        f"elapsed_seconds={elapsed:.1f} "
        f"candidate_pool={DATA_SOURCE_STATUS.get('candidate_pool', 'unknown')} "
        f"top={counts.get('top', 0)} "
        f"opportunity={counts.get('opportunity', 0)} "
        f"watchlist={counts.get('watchlist', 0)} "
        f"radar={counts.get('radar', 0)} "
        f"exit={counts.get('exit', 0)}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="AI V2 stock selection bot for TWSE stocks.")
    parser.add_argument("--push-line", action="store_true", help="Push the report through LINE Messaging API.")
    parser.add_argument("--write-report", action="store_true", help="Write the report under reports/.")
    args = parser.parse_args()

    started_at = datetime.now(TAIPEI_TZ)
    started_perf = time.perf_counter()
    push_enabled = args.push_line or env_bool("ENABLE_LINE_PUSH")
    install_run_deadline()
    try:
        if push_enabled:
            assert_market_close_ready()
        top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks, exit_alerts = build_v2_selection()
        if push_enabled:
            assert_data_completeness_ready()
        write_strategy_tracker(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks, exit_alerts)
        attach_gemini_reviews(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks)
        message = build_line_message(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks, exit_alerts)
    except Exception as error:
        if not push_enabled:
            raise
        if is_pre_close_error(error):
            raise
        top_stocks = []
        opportunity_stocks = []
        watchlist_stocks = []
        radar_stocks = []
        exit_alerts = []
        message = build_data_unavailable_message(error)
    print(message)
    counts = {
        "top": len(top_stocks),
        "opportunity": len(opportunity_stocks),
        "watchlist": len(watchlist_stocks),
        "radar": len(radar_stocks),
        "exit": len(exit_alerts),
    }
    print_run_metrics("analysis_complete", started_at, started_perf, counts)

    if args.write_report or env_bool("AI_STOCK_WRITE_REPORT"):
        today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / f"ai-stock-v2-top-{today}.md").write_text(
            build_markdown_report(top_stocks, radar_stocks),
            encoding="utf-8",
        )
        write_csv(REPORT_DIR / f"ai-stock-v2-top-{today}.csv", top_stocks)
        write_csv(REPORT_DIR / f"ai-stock-v2-opportunity-{today}.csv", opportunity_stocks)
        write_csv(REPORT_DIR / f"ai-stock-v2-watchlist-{today}.csv", watchlist_stocks)
        write_csv(REPORT_DIR / f"ai-stock-v2-radar-{today}.csv", radar_stocks)
        write_csv(REPORT_DIR / f"ai-stock-v2-exit-alerts-{today}.csv", exit_alerts)

    if push_enabled:
        push_line_message(message)
    print_run_metrics("finished", started_at, started_perf, counts)


if __name__ == "__main__":
    main()
