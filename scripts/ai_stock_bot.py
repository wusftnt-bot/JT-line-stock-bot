from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
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
TWSE_ALL_MARKET_DAY_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TWSE_T86_URL = "https://www.twse.com.tw/rwd/zh/fund/T86"
TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TWSE_ATTENTION_URL = "https://openapi.twse.com.tw/v1/announcement/notice"
TPEX_ALL_MARKET_DAY_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes"
TPEX_INSTITUTIONAL_CSV_URL = (
    "https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
)
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
LINE_FLEX_CAROUSEL_MAX_BUBBLES = 12
LINE_FLEX_CAROUSEL_MAX_BYTES = 50 * 1024
LINE_FLEX_ALT_TEXT_LIMIT = 1500
DATA_SOURCE_STATUS = {
    "revenue": "unknown",
    "margins": "unknown",
    "price": "TWSE",
    "institutional": "TWSE",
    "margin": "TWSE",
    "revenue_filter": "unknown",
    "candidate_pool": "unknown",
    "candidate_audit": "unknown",
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
    "industry_capital": "unknown",
    "investable_universe": "unknown",
    "preselection": "unknown",
    "macro_theme": "unknown",
    "exit_alerts": "unknown",
    "gemini_review": "disabled",
}
FINMIND_REQUEST_COUNT = 0
UNIVERSE_REJECTION_REASONS: dict[str, str] = {}

THEME_BY_STOCK_ID: dict[str, str] | None = None

MACRO_THEME_STOCKS: dict[str, set[str]] = {
    "energy_petrochemical": {
        "1301",
        "1303",
        "1304",
        "1305",
        "1308",
        "1309",
        "1310",
        "1312",
        "1313",
        "1314",
        "1319",
        "1326",
        "6505",
    },
    "shipping_logistics": {"2603", "2605", "2606", "2609", "2610", "2615", "2618", "2636"},
    "raw_materials": {"2002", "2027", "2014", "2015", "2023", "2025"},
}

MACRO_THEME_CONTEXT: dict[str, str] = {
    "energy_petrochemical": "Oil price, refining spread, Hormuz/geopolitical supply risk",
    "shipping_logistics": "Freight rate, route disruption, port congestion",
    "raw_materials": "Commodity price and cyclical restocking",
}


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
    "watch_rank",
    "radar_rank",
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
    "assets",
    "liabilities",
    "debt_ratio",
    "financial_period",
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
    "open",
    "high",
    "low",
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
    "price_change_5d",
    "price_change_10d",
    "price_change_20d",
    "break_20d_high",
    "break_60d_high",
    "break_120d_high",
    "long_upper_wick",
    "volume_price_divergence",
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
    "dealer_5d_sum",
    "dealer_10d_sum",
    "latest_dealer_net",
    "industry_capital_score",
    "industry_capital_grade",
    "industry_capital_reason",
    "industry_capital_ratio",
    "industry_capital_rank",
    "fundamental_pre_score",
    "fundamental_turn_score",
    "early_capital_score",
    "early_position_score",
    "composite_pre_score",
    "institutional_amount_ratio_1d",
    "institutional_amount_ratio_5d",
    "institutional_amount_ratio_10d",
    "foreign_amount_ratio_10d",
    "trust_amount_ratio_10d",
    "dealer_amount_ratio_10d",
    "distance_ma20_pct",
    "entry_timing_pass",
    "entry_timing_flags",
    "fundamental_floor_pass",
    "fundamental_floor_reason",
    "candidate_sources",
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


def request_text(url: str, params: dict[str, str] | None = None, timeout: int = 60) -> str:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    for encoding in ("utf-8-sig", "utf-8", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


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
    global FINMIND_REQUEST_COUNT
    max_requests = env_int("AI_STOCK_FINMIND_MAX_REQUESTS", 450)
    if max_requests > 0 and FINMIND_REQUEST_COUNT >= max_requests:
        raise RuntimeError(f"FinMind request budget exceeded ({FINMIND_REQUEST_COUNT}/{max_requests})")
    FINMIND_REQUEST_COUNT += 1
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
        item = grouped.setdefault(date, {"date": float(date.replace("-", "")), "foreign": 0.0, "trust": 0.0, "dealer": 0.0})
        net = clean_number(row.get("buy")) - clean_number(row.get("sell"))
        name = str(row.get("name", ""))
        if name in {"Foreign_Investor", "Foreign_Dealer_Self"}:
            item["foreign"] += net
        elif name == "Investment_Trust":
            item["trust"] += net
        elif "Dealer" in name:
            item["dealer"] += net
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
        fiscal_year = str(
            row_value_by_keywords(row, ["資料", "年度"])
            or row_value_by_keywords(row, ["年度"])
            or ""
        ).strip()
        fiscal_quarter = str(
            row_value_by_keywords(row, ["季"])
            or ""
        ).strip()
        financial_period = f"{fiscal_year}Q{fiscal_quarter}" if fiscal_year or fiscal_quarter else ""
        balance = balance_by_stock.get(stock_id, {})
        equity = clean_number(row_value_by_keywords(balance, ["權益", "總計"]))
        assets = clean_number(row_value_by_keywords(balance, ["資產", "總計"]))
        liabilities = clean_number(row_value_by_keywords(balance, ["負債", "總計"]))
        debt_ratio = round(liabilities / assets * 100, 2) if assets > 0 else 0.0
        roe = round(net_income * 4 / equity * 100, 2) if equity > 0 else 0.0
        previous = previous_quality.get(stock_id, {}) if isinstance(previous_quality, dict) else {}
        previous_period = str(previous.get("financial_period") or "") if isinstance(previous, dict) else ""
        previous_eps = (
            clean_number(previous.get("eps"))
            if isinstance(previous, dict)
            and previous_period
            and financial_period
            and previous_period != financial_period
            else 0.0
        )
        eps_change_pct = ((eps / previous_eps) - 1) * 100 if previous_eps > 0 and eps != previous_eps else 0.0
        quality[stock_id] = {
            "eps": eps,
            "previous_eps": previous_eps,
            "eps_change_pct": round(eps_change_pct, 2),
            "net_income": net_income,
            "equity": equity,
            "roe": roe,
            "assets": assets,
            "liabilities": liabilities,
            "debt_ratio": debt_ratio,
            "financial_period": financial_period,
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
    for theme, stock_ids in MACRO_THEME_STOCKS.items():
        if stock_id in stock_ids:
            return theme
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
        inst_amount = sum(float(row.get("institutional_amount_10d") or 0.0) for row in theme_rows)
        turnover_10d = sum(float(row.get("institutional_turnover_10d") or 0.0) for row in theme_rows)
        capital_ratio = inst_amount / turnover_10d if turnover_10d > 0 else 0.0
        summaries.append(
            {
                "theme": theme,
                "count": len(theme_rows),
                "price_20d": price_20d,
                "volume_ratio": volume_ratio,
                "capital_ratio": capital_ratio,
                "score": price_20d + volume_ratio * 3 + clip(capital_ratio * 100, -5, 5),
            }
        )

    summaries.sort(key=lambda item: item["score"], reverse=True)
    top = summaries[:3]
    if not top:
        DATA_SOURCE_STATUS["industry_momentum"] = "empty"
        return
    DATA_SOURCE_STATUS["industry_momentum"] = " | ".join(
        f"{item['theme']}:{item['price_20d']:.1f}%/{item['volume_ratio']:.2f}x/{item['capital_ratio'] * 100:.1f}capital"
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
    if theme == "energy_petrochemical":
        return 16.0, "energy_petrochemical_macro"
    if theme in {"shipping_logistics", "raw_materials"}:
        return 12.0, f"{theme}_macro"
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
    max_price_change = env_float("AI_STOCK_TOP_MAX_20D_PRICE_CHANGE", 25.0)

    volume_20d_avg = float(row.get("volume_20d_avg") or 0.0)
    latest_volume = float(row.get("latest_volume") or 0.0)
    turnover_20d_avg = float(row.get("turnover_20d_avg") or 0.0)
    volume_ratio = float(row.get("volume_ratio") or 0.0)
    close = float(row.get("close") or 0.0)
    price_change_1d = float(row.get("price_change_1d") or 0.0)
    price_change_20d = float(row.get("price_change_20d") or 0.0)
    total_score = float(row.get("total_score") or 0.0)
    latest_institutional_net = float(row.get("latest_institutional_net") or 0.0)

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
    if not bool(row.get("fundamental_floor_pass")):
        reasons.append("fundamental_floor")
    if not bool(row.get("entry_timing_pass")):
        reasons.append("entry_timing_risk")
    if latest_institutional_net < env_float("AI_STOCK_TOP_MIN_LATEST_INSTITUTIONAL_NET", 0.0):
        reasons.append("latest_institutional_selling")

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
        and row.get("fundamental_floor_pass")
        and row.get("entry_timing_pass")
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


def calculate_macro_catalyst_score(row: StockRow) -> tuple[float, str]:
    if not env_bool("AI_STOCK_ENABLE_MACRO_THEME_CANDIDATES", True):
        return 0.0, "macro_disabled"
    theme = str(row.get("industry_theme") or row.get("macro_theme") or "")
    if theme not in MACRO_THEME_CONTEXT:
        return 0.0, "macro_not_applicable"

    score = 1.0
    notes = [theme]
    price_change_20d = float(row.get("price_change_20d") or 0.0)
    volume_ratio = float(row.get("volume_ratio") or 0.0)
    institutional_10d = float(row.get("institutional_10d_sum") or 0.0)
    latest_institutional = float(row.get("latest_institutional_net") or 0.0)
    mom = float(row.get("mom") or 0.0)

    if institutional_10d > 0:
        score += 1.5
        notes.append("10d_institutional_buy")
    if latest_institutional > 0:
        score += 0.8
        notes.append("latest_institutional_buy")
    if 0 <= price_change_20d <= 35:
        score += 0.9
        notes.append("price_confirmed_not_overheated")
    if 0.7 <= volume_ratio <= 2.5:
        score += 0.6
        notes.append("volume_confirmed")
    if mom > 0:
        score += 0.4
        notes.append("revenue_mom_positive")

    return round(clip(score, 0, 5), 2), ",".join(notes[:5])


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
    latest_institutional_net = float(row.get("latest_institutional_net") or 0.0)

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
    if latest_institutional_net < 0:
        penalty += 4
        notes.append("latest_institutional_selling")
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
        macro_catalyst_score, macro_catalyst_note = calculate_macro_catalyst_score(row)
        risk_penalty, risk_notes = calculate_risk_penalty(row)
        total_score = (
            fundamental_weighted
            + chip_weighted
            + technical_weighted
            + valuation_score
            + industry_score
            + eps_acceleration_score
            + macro_catalyst_score
            - risk_penalty
        )
        grade = model_grade(total_score)

        row["model_version"] = "V2.3 Multi-pool Early Rotation"
        row["legacy_score"] = round(legacy_score, 2)
        row["fundamental_score"] = round(fundamental_weighted, 2)
        row["chip_score"] = round(chip_weighted, 2)
        row["technical_score"] = round(technical_weighted, 2)
        row["valuation_score"] = round(valuation_score, 2)
        row["industry_score"] = round(industry_score, 2)
        row["eps_acceleration_score"] = eps_acceleration_score
        row["eps_acceleration_note"] = eps_acceleration_note
        row["macro_catalyst_score"] = macro_catalyst_score
        row["macro_catalyst_note"] = macro_catalyst_note
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


def calculate_fundamental_pre_score(row: StockRow) -> tuple[float, float, str]:
    yoy = float(row.get("yoy") or 0.0)
    mom = float(row.get("mom") or 0.0)
    acc_yoy = float(row.get("acc_yoy") or 0.0)
    eps = float(row.get("eps") or 0.0)
    previous_eps = float(row.get("previous_eps") or 0.0)
    eps_change = float(row.get("eps_change_pct") or 0.0)
    roe = float(row.get("roe") or 0.0)
    gross_margin = float(row.get("gross_margin") or 0.0)
    operating_margin = float(row.get("operating_margin") or 0.0)
    net_margin = float(row.get("net_margin") or 0.0)
    debt_ratio = float(row.get("debt_ratio") or 0.0)
    financial_available = any(
        key in row for key in ("eps", "roe", "net_income", "equity")
    )

    revenue_score = (
        clip((yoy + 10) / 60 * 8, 0, 8)
        + clip((acc_yoy + 10) / 50 * 7, 0, 7)
        + clip((mom + 10) / 40 * 3, 0, 3)
        + (2 if yoy >= acc_yoy + 5 and yoy > 0 else 0)
    )
    quality_score = 0.0
    notes: list[str] = []
    if financial_available:
        if eps > 0:
            quality_score += 3
        if previous_eps > 0 and eps_change >= 30:
            quality_score += 4
            notes.append("eps_accelerating")
        elif previous_eps > 0 and eps_change <= -30:
            quality_score -= 2
            notes.append("eps_decelerating")
        if roe >= 15:
            quality_score += 4
        elif roe >= 10:
            quality_score += 2
        if operating_margin > 0 and net_margin > 0:
            quality_score += 2
        if row.get("triple_margin_up"):
            quality_score += 3
            notes.append("margins_improving")
        if debt_ratio:
            quality_score += 2 if debt_ratio <= 50 else 1 if debt_ratio <= 65 else -1
    else:
        quality_score += 5
        notes.append("financial_data_pending")

    if gross_margin > 0 and operating_margin > 0:
        quality_score += 1
    score = round(clip(revenue_score + quality_score, 0, 40), 2)

    turn_score = 0.0
    if yoy > acc_yoy + 8 and yoy > 0:
        turn_score += 5
    if mom > 5 and yoy > 0:
        turn_score += 3
    if previous_eps > 0 and eps_change >= 30:
        turn_score += 6
    elif previous_eps <= 0 < eps:
        turn_score += 7
    if row.get("triple_margin_up"):
        turn_score += 5
    if operating_margin > float(row.get("prev_operating_margin") or 0.0) and operating_margin > 0:
        turn_score += 3
    return score, round(clip(turn_score, 0, 20), 2), ",".join(notes) or "available"


def calculate_institutional_amount_metrics(
    records: list[dict[str, float]],
    price_records: list[StockRow],
) -> dict[str, float]:
    price_by_date = {
        normalize_market_date(row.get("date")): row
        for row in price_records
        if normalize_market_date(row.get("date"))
    }
    recent = sorted(records, key=lambda item: item["date"], reverse=True)

    def window(days: int) -> dict[str, float]:
        foreign_amount = 0.0
        trust_amount = 0.0
        dealer_amount = 0.0
        turnover = 0.0
        positive_days = 0
        used = 0
        for item in recent[:days]:
            date_key = normalize_market_date(item.get("trade_date") or item.get("date"))
            price = price_by_date.get(date_key)
            if not price:
                continue
            close = float(price.get("close") or 0.0)
            day_turnover = float(price.get("turnover") or 0.0)
            if close <= 0 or day_turnover <= 0:
                continue
            foreign_amount += float(item.get("foreign") or 0.0) * close
            trust_amount += float(item.get("trust") or 0.0) * close
            dealer_amount += float(item.get("dealer") or 0.0) * close
            turnover += day_turnover
            positive_days += int(
                float(item.get("foreign") or 0.0)
                + float(item.get("trust") or 0.0)
                + float(item.get("dealer") or 0.0)
                > 0
            )
            used += 1
        total_amount = foreign_amount + trust_amount + dealer_amount
        return {
            "foreign_amount": foreign_amount,
            "trust_amount": trust_amount,
            "dealer_amount": dealer_amount,
            "total_amount": total_amount,
            "turnover": turnover,
            "ratio": total_amount / turnover if turnover > 0 else 0.0,
            "foreign_ratio": foreign_amount / turnover if turnover > 0 else 0.0,
            "trust_ratio": trust_amount / turnover if turnover > 0 else 0.0,
            "dealer_ratio": dealer_amount / turnover if turnover > 0 else 0.0,
            "positive_days": float(positive_days),
            "used_days": float(used),
        }

    one = window(1)
    five = window(5)
    ten = window(10)
    score = (
        clip(five["ratio"] / 0.08 * 10, -6, 10)
        + clip(ten["trust_ratio"] / 0.03 * 5, -3, 5)
        + clip(ten["foreign_ratio"] / 0.05 * 4, -3, 4)
        + clip(ten["dealer_ratio"] / 0.02 * 2, -2, 2)
        + clip(five["positive_days"] / max(five["used_days"], 1) * 3, 0, 3)
        + (3 if five["ratio"] > ten["ratio"] > 0 else 0)
        + (3 if one["ratio"] > 0 else -5 if one["ratio"] <= -0.08 else 0)
    )
    return {
        "early_capital_score": round(clip(score, 0, 30), 2),
        "institutional_amount_ratio_1d": one["ratio"],
        "institutional_amount_ratio_5d": five["ratio"],
        "institutional_amount_ratio_10d": ten["ratio"],
        "foreign_amount_ratio_10d": ten["foreign_ratio"],
        "trust_amount_ratio_10d": ten["trust_ratio"],
        "dealer_amount_ratio_10d": ten["dealer_ratio"],
        "institutional_amount_10d": ten["total_amount"],
        "institutional_turnover_10d": ten["turnover"],
        "institutional_positive_days_5d": five["positive_days"],
        "institutional_amount_accelerating": five["ratio"] > ten["ratio"] > 0,
        "latest_institutional_big_sell": one["ratio"] <= -0.08,
    }


def calculate_early_position_score(row: StockRow) -> tuple[float, bool, str]:
    change_1d = float(row.get("price_change_1d") or 0.0)
    change_20d = float(row.get("price_change_20d") or 0.0)
    close = float(row.get("close") or 0.0)
    ma20 = float(row.get("ma20") or 0.0)
    ma60 = float(row.get("ma60") or 0.0)
    volume_ratio = float(row.get("volume_ratio") or 0.0)
    distance_ma20 = ((close / ma20) - 1) * 100 if ma20 > 0 else 99.0
    flags: list[str] = []
    score = 0.0

    if -5 <= change_20d < 15:
        score += 9
    elif 15 <= change_20d <= 25:
        score += 4
    elif change_20d > 25:
        flags.append("overheated_20d")
    if -3 <= distance_ma20 <= 5:
        score += 7
    elif 5 < distance_ma20 <= 12:
        score += 3
    elif distance_ma20 > 12:
        flags.append("far_above_ma20")
    if 0.7 <= volume_ratio <= 1.8:
        score += 6
    elif 1.8 < volume_ratio <= 2.5:
        score += 3
    if ma20 > 0 and close >= ma20:
        score += 3
    if ma60 > 0 and close >= ma60:
        score += 2
    if row.get("break_60d_high") and change_20d <= 15:
        score += 3
    if change_1d > 7 and volume_ratio > 2.5:
        flags.append("price_volume_overheat")
    if change_1d <= -9:
        flags.append("near_limit_down")
    if row.get("long_upper_wick"):
        flags.append("long_upper_wick")
    if row.get("volume_price_divergence"):
        flags.append("volume_price_divergence")
    if row.get("latest_institutional_big_sell"):
        flags.append("latest_institutional_big_sell")

    hard_flags = {
        "overheated_20d",
        "far_above_ma20",
        "price_volume_overheat",
        "near_limit_down",
        "long_upper_wick",
        "volume_price_divergence",
        "latest_institutional_big_sell",
    }
    timing_pass = not any(flag in hard_flags for flag in flags)
    return round(clip(score, 0, 30), 2), timing_pass, ",".join(flags)


def evaluate_fundamental_floor(row: StockRow) -> tuple[bool, str]:
    reasons: list[str] = []
    min_score = env_float("AI_STOCK_TOP_MIN_FUNDAMENTAL_PRE_SCORE", 16.0)
    yoy = float(row.get("yoy") or 0.0)
    acc_yoy = float(row.get("acc_yoy") or 0.0)
    eps = float(row.get("eps") or 0.0)
    if float(row.get("fundamental_pre_score") or 0.0) < min_score:
        reasons.append("weak_fundamental_pre_score")
    if max(yoy, acc_yoy) < env_float("AI_STOCK_TOP_MIN_REVENUE_GROWTH", -5.0):
        reasons.append("revenue_growth_below_floor")
    if eps < 0 and max(yoy, acc_yoy) < env_float("AI_STOCK_TOP_LOSS_HIGH_GROWTH_YOY", 40.0):
        reasons.append("eps_loss_without_turnaround")
    anomaly, anomaly_reason = detect_revenue_anomaly(row)
    severe_reasons = [
        item for item in anomaly_reason.split(",") if item and item != "small_current_revenue"
    ]
    if anomaly and severe_reasons:
        reasons.append("revenue_anomaly")
    return not reasons, ",".join(reasons)


def build_investable_universe(
    rows: list[StockRow],
    margins: dict[str, dict[str, float]],
    financial_quality: dict[str, dict[str, float]],
    institutional: dict[str, list[dict[str, float]]],
    event_risk_map: dict[str, StockRow],
    price_history: dict[str, list[StockRow]],
) -> list[StockRow]:
    global UNIVERSE_REJECTION_REASONS
    UNIVERSE_REJECTION_REASONS = {}
    min_latest_volume = env_float("AI_STOCK_UNIVERSE_MIN_LATEST_VOLUME_LOTS", 100.0) * 1000
    min_avg_volume = env_float("AI_STOCK_UNIVERSE_MIN_AVG_VOLUME_LOTS", 200.0) * 1000
    min_turnover = env_float("AI_STOCK_UNIVERSE_MIN_TURNOVER_TWD", 20000000.0)
    min_close = env_float("AI_STOCK_UNIVERSE_MIN_CLOSE", 10.0)
    market_dates: dict[str, set[str]] = {}
    for records in price_history.values():
        for record in records:
            market = str(record.get("market_type") or "")
            date_key = normalize_market_date(record.get("date"))
            if market and date_key:
                market_dates.setdefault(market, set()).add(date_key)
    recent_market_dates_by_type = {
        market: sorted(dates, reverse=True)[:2]
        for market, dates in market_dates.items()
    }

    rejected: Counter[str] = Counter()
    def reject(stock_id: str, reason: str) -> None:
        rejected[reason] += 1
        if stock_id:
            UNIVERSE_REJECTION_REASONS[stock_id] = reason

    investable: list[StockRow] = []
    for source_row in rows:
        stock_id = str(source_row.get("stock_id") or "")
        if not re.fullmatch(r"\d{4}", stock_id):
            reject(stock_id, "not_common_stock")
            continue
        records = price_history.get(stock_id, [])
        inst_records = institutional.get(stock_id, [])
        if not inst_records:
            reject(stock_id, "missing_institutional")
            continue
        price_dates = {
            normalize_market_date(item.get("date"))
            for item in records
            if normalize_market_date(item.get("date"))
        }
        institutional_dates = {
            normalize_market_date(item.get("trade_date") or item.get("date"))
            for item in inst_records
            if normalize_market_date(item.get("trade_date") or item.get("date"))
        }
        common_dates = price_dates & institutional_dates
        if not common_dates:
            reject(stock_id, "date_mismatch")
            continue
        aligned_date = max(common_dates)
        aligned_records = [
            item
            for item in records
            if normalize_market_date(item.get("date")) <= aligned_date
        ]
        if len(aligned_records) < 60:
            reject(stock_id, "price_history_lt_60")
            continue
        market_type = str(
            source_row.get("market_type")
            or aligned_records[-1].get("market_type")
            or ""
        )
        if aligned_date not in recent_market_dates_by_type.get(market_type, [aligned_date]):
            reject(stock_id, "stale_common_date")
            continue
        technical = calculate_technical_from_records(aligned_records)
        event = event_risk_map.get(stock_id, {})
        if event.get("event_risk_flags") or event.get("event_risk_level"):
            reject(stock_id, "event_risk")
            continue
        if float(technical.get("close") or 0.0) < min_close:
            reject(stock_id, "low_price")
            continue
        if float(technical.get("latest_volume") or 0.0) < min_latest_volume:
            reject(stock_id, "low_latest_volume")
            continue
        if float(technical.get("volume_20d_avg") or 0.0) < min_avg_volume:
            reject(stock_id, "low_avg_volume")
            continue
        if float(technical.get("turnover_20d_avg") or 0.0) < min_turnover:
            reject(stock_id, "low_turnover")
            continue

        enriched = enrich_revenue_candidate(source_row, margins)
        enriched.update(financial_quality.get(stock_id, {}))
        enriched.update(technical)
        enriched.update(event)
        enriched["industry_theme"] = classify_industry_theme(enriched)
        enriched.update(calculate_institutional_amount_metrics(inst_records, aligned_records))
        enriched["aligned_trade_date"] = aligned_date
        fundamental_score, turn_score, fundamental_note = calculate_fundamental_pre_score(enriched)
        enriched["fundamental_pre_score"] = fundamental_score
        enriched["fundamental_turn_score"] = turn_score
        enriched["fundamental_pre_note"] = fundamental_note
        position_score, timing_pass, timing_flags = calculate_early_position_score(enriched)
        enriched["early_position_score"] = position_score
        enriched["entry_timing_pass"] = timing_pass
        enriched["entry_timing_flags"] = timing_flags
        enriched["distance_ma20_pct"] = round(
            ((float(enriched["close"]) / float(enriched["ma20"])) - 1) * 100
            if float(enriched.get("ma20") or 0.0) > 0
            else 99.0,
            2,
        )
        floor_pass, floor_reason = evaluate_fundamental_floor(enriched)
        enriched["fundamental_floor_pass"] = floor_pass
        enriched["fundamental_floor_reason"] = floor_reason
        enriched["composite_pre_score"] = round(
            fundamental_score
            + float(enriched.get("early_capital_score") or 0.0)
            + position_score,
            2,
        )
        enriched["candidate_source"] = "investable_universe"
        enriched["candidate_sources"] = []
        investable.append(enriched)

    DATA_SOURCE_STATUS["investable_universe"] = (
        f"source={len(rows)} investable={len(investable)} "
        + " ".join(f"{reason}={count}" for reason, count in rejected.most_common(6))
    )
    return investable


def select_fundamental_growth_pool(rows: list[StockRow]) -> list[StockRow]:
    limit = env_int("AI_STOCK_FUNDAMENTAL_GROWTH_LIMIT", 80)
    candidates = [
        row
        for row in rows
        if max(float(row.get("yoy") or 0.0), float(row.get("acc_yoy") or 0.0)) >= 10
        and float(row.get("fundamental_pre_score") or 0.0) >= 16
    ]
    candidates.sort(
        key=lambda row: (
            float(row.get("fundamental_pre_score") or 0.0),
            float(row.get("acc_yoy") or 0.0),
            float(row.get("yoy") or 0.0),
        ),
        reverse=True,
    )
    return candidates[:limit]


def select_fundamental_turn_pool(rows: list[StockRow]) -> list[StockRow]:
    limit = env_int("AI_STOCK_FUNDAMENTAL_TURN_LIMIT", 40)
    candidates = [
        row
        for row in rows
        if float(row.get("fundamental_turn_score") or 0.0) >= 5
        and max(float(row.get("yoy") or 0.0), float(row.get("acc_yoy") or 0.0)) >= 0
    ]
    candidates.sort(
        key=lambda row: (
            float(row.get("fundamental_turn_score") or 0.0),
            float(row.get("fundamental_pre_score") or 0.0),
            float(row.get("early_position_score") or 0.0),
        ),
        reverse=True,
    )
    return candidates[:limit]


def select_early_institutional_pool(rows: list[StockRow]) -> list[StockRow]:
    limit = env_int("AI_STOCK_EARLY_INSTITUTIONAL_LIMIT", 50)
    candidates = [
        row
        for row in rows
        if float(row.get("early_capital_score") or 0.0) >= 10
        and float(row.get("institutional_amount_ratio_5d") or 0.0) > 0
        and float(row.get("price_change_20d") or 0.0) < 15
        and bool(row.get("entry_timing_pass"))
    ]
    candidates.sort(
        key=lambda row: (
            float(row.get("early_capital_score") or 0.0),
            float(row.get("institutional_amount_ratio_5d") or 0.0),
            float(row.get("fundamental_pre_score") or 0.0),
        ),
        reverse=True,
    )
    return candidates[:limit]


def is_mainline_industry(row: StockRow) -> bool:
    text = " ".join(
        [
            str(row.get("industry_theme") or ""),
            str(row.get("industry_category") or ""),
            str(row.get("industry_priority") or ""),
        ]
    ).lower()
    return any(
        keyword in text
        for keyword in (
            "ai",
            "semiconductor",
            "electronics",
            "半導體",
            "電子",
            "記憶體",
            "電腦",
            "通信",
            "光電",
        )
    )


def build_industry_capital_stats(rows: list[StockRow]) -> dict[str, dict[str, Any]]:
    market_5d = median([float(row.get("price_change_5d") or 0.0) for row in rows])
    market_10d = median([float(row.get("price_change_10d") or 0.0) for row in rows])
    market_20d = median([float(row.get("price_change_20d") or 0.0) for row in rows])
    groups: dict[str, list[StockRow]] = {}
    for row in rows:
        industry = str(row.get("industry_theme") or row.get("industry_category") or "unknown")
        groups.setdefault(industry, []).append(row)

    stats: dict[str, dict[str, Any]] = {}
    min_turnover = env_float("AI_STOCK_INDUSTRY_CAPITAL_MIN_TURNOVER_TWD", 300000000.0)
    for industry, members in groups.items():
        daily_turnover = sum(float(row.get("turnover_20d_avg") or 0.0) for row in members)
        if daily_turnover < min_turnover:
            continue
        turnover_10d = sum(float(row.get("institutional_turnover_10d") or 0.0) for row in members)
        inst_amount = sum(float(row.get("institutional_amount_10d") or 0.0) for row in members)
        foreign_amount = sum(
            float(row.get("foreign_amount_ratio_10d") or 0.0)
            * float(row.get("institutional_turnover_10d") or 0.0)
            for row in members
        )
        trust_amount = sum(
            float(row.get("trust_amount_ratio_10d") or 0.0)
            * float(row.get("institutional_turnover_10d") or 0.0)
            for row in members
        )
        capital_ratio = inst_amount / turnover_10d if turnover_10d > 0 else 0.0
        foreign_ratio = foreign_amount / turnover_10d if turnover_10d > 0 else 0.0
        trust_ratio = trust_amount / turnover_10d if turnover_10d > 0 else 0.0
        buy_count = sum(1 for row in members if float(row.get("institutional_amount_10d") or 0.0) > 0)
        buy_ratio = buy_count / len(members) if members else 0.0
        volume_ratio = median([float(row.get("volume_ratio") or 0.0) for row in members])
        price_5d = median([float(row.get("price_change_5d") or 0.0) for row in members])
        price_10d = median([float(row.get("price_change_10d") or 0.0) for row in members])
        price_20d = median([float(row.get("price_change_20d") or 0.0) for row in members])
        relative_strength = (
            (price_5d - market_5d) * 0.4
            + (price_10d - market_10d) * 0.3
            + (price_20d - market_20d) * 0.3
        )
        non_overheated = 5.0 if price_20d <= 15 else 2.0 if price_20d <= 25 else 0.0
        score = (
            clip(capital_ratio / 0.12 * 30, 0, 30)
            + clip(trust_ratio / 0.05 * 20, 0, 20)
            + clip(foreign_ratio / 0.08 * 15, 0, 15)
            + clip(buy_ratio * 10, 0, 10)
            + clip(max(volume_ratio - 0.8, 0) / 0.8 * 10, 0, 10)
            + clip((relative_strength + 5) / 15 * 10, 0, 10)
            + non_overheated
        )
        stats[industry] = {
            "industry": industry,
            "score": round(score, 1),
            "grade": industry_capital_grade(score),
            "count": len(members),
            "buy_count": buy_count,
            "capital_ratio": capital_ratio,
            "relative_strength": relative_strength,
            "members": members,
        }
    return stats


def select_industry_breakout_pool(
    rows: list[StockRow],
    industry_stats: dict[str, dict[str, Any]],
) -> list[StockRow]:
    if not env_bool("AI_STOCK_ENABLE_INDUSTRY_CAPITAL_CANDIDATES", True):
        DATA_SOURCE_STATUS["industry_capital"] = "disabled"
        return []
    limit = env_int("AI_STOCK_INDUSTRY_CAPITAL_CANDIDATE_LIMIT", 30)
    min_score = env_float("AI_STOCK_INDUSTRY_CAPITAL_MIN_SCORE", 60.0)
    selected: list[StockRow] = []
    for stat in sorted(industry_stats.values(), key=lambda item: item["score"], reverse=True):
        if stat["score"] < min_score or stat["grade"] == "C" or stat["buy_count"] < 2:
            continue
        per_industry_limit = {"S": 5, "A": 3, "B": 2}.get(str(stat["grade"]), 0)
        members = sorted(
            stat["members"],
            key=lambda row: (
                float(row.get("early_capital_score") or 0.0),
                float(row.get("fundamental_pre_score") or 0.0),
                float(row.get("early_position_score") or 0.0),
            ),
            reverse=True,
        )
        picked = 0
        for row in members:
            if picked >= per_industry_limit or len(selected) >= limit:
                break
            if not row.get("entry_timing_pass"):
                continue
            if float(row.get("institutional_amount_ratio_5d") or 0.0) <= 0:
                continue
            if float(row.get("fundamental_pre_score") or 0.0) < 12:
                continue
            row["industry_capital_score"] = stat["score"]
            row["industry_capital_grade"] = stat["grade"]
            row["industry_capital_ratio"] = stat["capital_ratio"]
            row["industry_capital_reason"] = (
                f"{stat['grade']} capital={stat['capital_ratio'] * 100:.1f}% "
                f"buy={stat['buy_count']}/{stat['count']} rs={stat['relative_strength']:.1f}"
            )
            selected.append(row)
            picked += 1

    top_industries = [
        stat
        for stat in sorted(industry_stats.values(), key=lambda item: item["score"], reverse=True)
        if stat["score"] >= min_score and stat["grade"] != "C" and stat["buy_count"] >= 2
    ][:3]
    DATA_SOURCE_STATUS["industry_capital"] = (
        " | ".join(
            f"{item['industry']}:{item['grade']}{item['score']:.1f}/"
            f"{item['capital_ratio'] * 100:.1f}%/{item['buy_count']}"
            for item in top_industries
        )
        if top_industries
        else "empty"
    )
    return selected[:limit]


def select_non_mainstream_breakout_pool(
    rows: list[StockRow],
    industry_stats: dict[str, dict[str, Any]],
) -> list[StockRow]:
    limit = env_int("AI_STOCK_NON_MAINSTREAM_BREAKOUT_LIMIT", 20)
    candidates: list[StockRow] = []
    industry_counts: Counter[str] = Counter()
    for row in sorted(
        rows,
        key=lambda item: (
            float(
                industry_stats.get(
                    str(item.get("industry_theme") or item.get("industry_category") or "unknown"),
                    {},
                ).get("score", 0.0)
            ),
            float(item.get("composite_pre_score") or 0.0),
        ),
        reverse=True,
    ):
        if is_mainline_industry(row) or not row.get("entry_timing_pass"):
            continue
        industry = str(row.get("industry_theme") or row.get("industry_category") or "unknown")
        stat = industry_stats.get(industry, {})
        if float(stat.get("score") or 0.0) < 60 or str(stat.get("grade") or "C") == "C":
            continue
        if float(row.get("fundamental_pre_score") or 0.0) < 16:
            continue
        if float(row.get("early_capital_score") or 0.0) < 8:
            continue
        if float(row.get("price_change_20d") or 0.0) >= 20:
            continue
        if industry_counts[industry] >= 2:
            continue
        candidates.append(row)
        industry_counts[industry] += 1
        if len(candidates) >= limit:
            break
    DATA_SOURCE_STATUS["macro_theme"] = (
        f"dynamic_non_mainstream candidates={len(candidates)} themes="
        + ",".join(industry_counts.keys())
    )
    return candidates


def merge_preselection_pools(
    universe: list[StockRow],
    growth: list[StockRow],
    turns: list[StockRow],
    institutional: list[StockRow],
    industry: list[StockRow],
    non_mainstream: list[StockRow],
) -> list[StockRow]:
    target = env_int("AI_STOCK_DEEP_ANALYSIS_LIMIT", 220)
    selected: dict[str, StockRow] = {}
    for pool, source in (
        (growth, "fundamental_growth"),
        (turns, "fundamental_turn"),
        (institutional, "early_institutional"),
        (industry, "industry_breakout"),
        (non_mainstream, "non_mainstream_breakout"),
    ):
        for row in pool:
            stock_id = str(row["stock_id"])
            if stock_id not in selected:
                selected[stock_id] = row
            sources = selected[stock_id].setdefault("candidate_sources", [])
            if source not in sources:
                sources.append(source)

    if len(selected) < target:
        fallback = sorted(
            (row for row in universe if str(row["stock_id"]) not in selected),
            key=lambda row: (
                float(row.get("composite_pre_score") or 0.0),
                float(row.get("fundamental_pre_score") or 0.0),
                float(row.get("early_capital_score") or 0.0),
                -max(float(row.get("price_change_20d") or 0.0), 0.0),
            ),
            reverse=True,
        )
        for row in fallback:
            row["candidate_sources"] = ["composite_fill"]
            selected[str(row["stock_id"])] = row
            if len(selected) >= target:
                break

    merged = sorted(
        selected.values(),
        key=lambda row: (
            float(row.get("composite_pre_score") or 0.0),
            float(row.get("fundamental_pre_score") or 0.0),
            float(row.get("early_capital_score") or 0.0),
        ),
        reverse=True,
    )[:target]
    for row in merged:
        row["candidate_source"] = ",".join(row.get("candidate_sources") or ["composite_fill"])
    DATA_SOURCE_STATUS["candidate_pool"] = (
        f"growth={len(growth)} turn={len(turns)} institutional={len(institutional)} "
        f"industry={len(industry)} non_mainstream={len(non_mainstream)} merged={len(merged)}"
    )
    DATA_SOURCE_STATUS["preselection"] = (
        f"universe={len(universe)} deep={len(merged)} target={target}"
    )
    return merged


AUDIT_REJECTION_LABELS = {
    "not_common_stock": "不屬於上市櫃普通股",
    "missing_institutional": "法人資料缺漏",
    "date_mismatch": "股價與法人日期無共同交易日",
    "price_history_lt_60": "價量歷史不足60日",
    "stale_common_date": "共同交易日過舊",
    "event_risk": "注意或處置等事件風險",
    "low_price": "股價低於母體門檻",
    "low_latest_volume": "當日成交量低於母體門檻",
    "low_avg_volume": "20日均量低於母體門檻",
    "low_turnover": "成交金額低於母體門檻",
}


def candidate_pool_hash(stock_ids: list[str]) -> str:
    canonical = ",".join(sorted(set(stock_ids)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def removed_candidate_reason(
    stock_id: str,
    universe_by_id: dict[str, StockRow],
    cutoff_score: float,
) -> str:
    rejected_reason = UNIVERSE_REJECTION_REASONS.get(stock_id)
    if rejected_reason:
        return AUDIT_REJECTION_LABELS.get(rejected_reason, rejected_reason)
    row = universe_by_id.get(stock_id)
    if not row:
        return "未進入今日可投資母體"
    timing_flags = str(row.get("entry_timing_flags") or "")
    if not row.get("entry_timing_pass") and timing_flags:
        return f"進場時機風險：{timing_flags}"
    floor_reason = str(row.get("fundamental_floor_reason") or "")
    if not row.get("fundamental_floor_pass") and floor_reason:
        return f"基本面底線轉弱：{floor_reason}"
    score = float(row.get("composite_pre_score") or 0.0)
    return f"多路候選排名下降；綜合預分{score:.1f}，截止分{cutoff_score:.1f}"


def audit_candidate_pool(candidates: list[StockRow], universe: list[StockRow]) -> dict[str, Any]:
    today = env_str("AI_STOCK_AUDIT_DATE_OVERRIDE", datetime.now(TAIPEI_TZ).date().isoformat())
    target = env_int("AI_STOCK_DEEP_ANALYSIS_LIMIT", 220)
    current_ids = [str(row.get("stock_id") or "") for row in candidates if row.get("stock_id")]
    current_hash = candidate_pool_hash(current_ids)
    common_dates = sorted(
        {
            normalize_market_date(row.get("aligned_trade_date") or row.get("latest_trade_date"))
            for row in candidates
            if normalize_market_date(row.get("aligned_trade_date") or row.get("latest_trade_date"))
        }
    )
    common_date_text = ",".join(common_dates)
    universe_by_id = {str(row.get("stock_id") or ""): row for row in universe}
    cutoff_score = min(
        (float(row.get("composite_pre_score") or 0.0) for row in candidates),
        default=0.0,
    )

    cached = read_cache("candidate_pool_audit_history") or {}
    history = cached.get("history", []) if isinstance(cached, dict) else []
    history = [item for item in history if isinstance(item, dict)]
    prior_days = sorted(
        (item for item in history if str(item.get("audit_date") or "") < today),
        key=lambda item: str(item.get("audit_date") or ""),
    )
    previous = prior_days[-1] if prior_days else None
    previous_entries = {
        str(item.get("stock_id") or ""): item
        for item in (previous.get("candidates", []) if previous else [])
        if isinstance(item, dict) and item.get("stock_id")
    }
    previous_ids = set(previous_entries)
    current_id_set = set(current_ids)
    added_ids = [stock_id for stock_id in current_ids if stock_id not in previous_ids]
    removed_ids = sorted(previous_ids - current_id_set)
    overlap_count = len(current_id_set & previous_ids)
    overlap_ratio = overlap_count / len(current_id_set) if current_id_set else 0.0
    identical_streak = (
        int(previous.get("identical_streak") or 1) + 1
        if previous and str(previous.get("pool_hash") or "") == current_hash
        else 1
    )
    stale_trade_date_streak = (
        int(previous.get("stale_trade_date_streak") or 1) + 1
        if previous
        and common_date_text
        and str(previous.get("common_trade_dates") or "") == common_date_text
        else 1
    )

    current_entries = []
    for rank, row in enumerate(candidates, start=1):
        sources = row.get("candidate_sources") or [row.get("candidate_source") or "composite_fill"]
        current_entries.append(
            {
                "rank": rank,
                "stock_id": str(row.get("stock_id") or ""),
                "stock_name": str(row.get("stock_name") or ""),
                "candidate_sources": [str(item) for item in sources],
                "composite_pre_score": round(float(row.get("composite_pre_score") or 0.0), 2),
                "fundamental_pre_score": round(float(row.get("fundamental_pre_score") or 0.0), 2),
                "early_capital_score": round(float(row.get("early_capital_score") or 0.0), 2),
                "early_position_score": round(float(row.get("early_position_score") or 0.0), 2),
                "aligned_trade_date": str(row.get("aligned_trade_date") or ""),
            }
        )
    current_entry_by_id = {item["stock_id"]: item for item in current_entries}
    added = [
        {
            **current_entry_by_id[stock_id],
            "reason": "進入候選來源：" + ",".join(current_entry_by_id[stock_id]["candidate_sources"]),
        }
        for stock_id in added_ids
    ]
    removed = [
        {
            "stock_id": stock_id,
            "stock_name": str(previous_entries[stock_id].get("stock_name") or ""),
            "previous_rank": previous_entries[stock_id].get("rank"),
            "previous_composite_pre_score": previous_entries[stock_id].get("composite_pre_score"),
            "reason": removed_candidate_reason(stock_id, universe_by_id, cutoff_score),
        }
        for stock_id in removed_ids
    ]

    warnings: list[str] = []
    if len(current_ids) < min(target, len(universe)):
        warnings.append("pool_not_full")
    if previous and identical_streak >= env_int("AI_STOCK_AUDIT_IDENTICAL_WARN_DAYS", 3):
        warnings.append("pool_identical_multiple_days")
    if previous and stale_trade_date_streak >= env_int("AI_STOCK_AUDIT_STALE_DATE_WARN_DAYS", 2):
        warnings.append("common_trade_date_not_advanced")
    if not common_date_text:
        warnings.append("common_trade_date_missing")

    snapshot = {
        "schema": "candidate_pool_audit_v1",
        "audit_date": today,
        "generated_at": datetime.now(TAIPEI_TZ).isoformat(),
        "status": "warning" if warnings else "normal",
        "warnings": warnings,
        "target_count": target,
        "candidate_count": len(current_ids),
        "investable_count": len(universe),
        "added_count": len(added),
        "removed_count": len(removed),
        "overlap_count": overlap_count,
        "overlap_ratio": round(overlap_ratio, 4),
        "pool_hash": current_hash,
        "common_trade_dates": common_date_text,
        "identical_streak": identical_streak,
        "stale_trade_date_streak": stale_trade_date_streak,
        "cutoff_composite_pre_score": round(cutoff_score, 2),
        "added": added,
        "removed": removed,
        "candidates": current_entries,
    }
    history = [item for item in history if str(item.get("audit_date") or "") != today]
    history.append(snapshot)
    history.sort(key=lambda item: str(item.get("audit_date") or ""))
    write_cache(
        "candidate_pool_audit_history",
        {"schema": "candidate_pool_audit_history_v1", "history": history[-30:]},
    )
    write_cache("candidate_pool_audit_latest", snapshot)
    DATA_SOURCE_STATUS["candidate_audit"] = (
        f"status={snapshot['status']} added={len(added)} removed={len(removed)} "
        f"overlap={overlap_ratio * 100:.1f}% hash={current_hash[:12]} "
        f"trade_date={common_date_text or 'missing'} identical={identical_streak} "
        f"stale={stale_trade_date_streak} warnings={','.join(warnings) or 'none'}"
    )
    if warnings:
        print(
            "AI_STOCK_CANDIDATE_AUDIT_WARNING "
            f"date={today} hash={current_hash[:12]} trade_date={common_date_text or 'missing'} "
            f"warnings={','.join(warnings)}"
        )
    return snapshot


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
    dealer_10 = sum(item.get("dealer", 0.0) for item in recent_10)
    inst_20 = sum(item["foreign"] + item["trust"] + item.get("dealer", 0.0) for item in recent_20)
    foreign_days = sum(1 for item in recent_10 if item["foreign"] > 0)
    trust_days = sum(1 for item in recent_10 if item["trust"] > 0)
    prior_10 = sum(item["foreign"] + item["trust"] + item.get("dealer", 0.0) for item in latest[10:20])
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
    if dealer_10 > 0:
        score += 3
    if prior_10 < 0 and foreign_10 + trust_10 + dealer_10 > 0:
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


def select_macro_theme_candidates(
    rows: list[StockRow],
    margins: dict[str, dict[str, float]],
    institutional: dict[str, list[dict[str, float]]],
    existing: list[StockRow],
) -> list[StockRow]:
    if not env_bool("AI_STOCK_ENABLE_MACRO_THEME_CANDIDATES", True):
        DATA_SOURCE_STATUS["macro_theme"] = "disabled"
        return []

    limit = env_int("AI_STOCK_MACRO_CANDIDATE_LIMIT", 25)
    min_score = env_float("AI_STOCK_MACRO_CANDIDATE_MIN_SCORE", 12.0)
    existing_ids = {row["stock_id"] for row in existing}
    macro_ids = set().union(*MACRO_THEME_STOCKS.values())
    candidates: list[StockRow] = []

    for row in rows:
        stock_id = str(row.get("stock_id") or "")
        if stock_id in existing_ids or stock_id not in macro_ids:
            continue

        theme = next((name for name, stock_ids in MACRO_THEME_STOCKS.items() if stock_id in stock_ids), "macro")
        records = institutional.get(stock_id, [])
        institutional_score, reason = institutional_candidate_score(stock_id, records, row)
        yoy = float(row.get("yoy") or 0.0)
        mom = float(row.get("mom") or 0.0)
        acc_yoy = float(row.get("acc_yoy") or 0.0)
        monthly_revenue = float(row.get("monthly_revenue") or 0.0)

        macro_score = 10.0
        macro_score += clip(institutional_score, 0, 40) * 0.45
        if yoy > 0:
            macro_score += 4
        if mom > 0:
            macro_score += 3
        if acc_yoy > 0:
            macro_score += 3
        if monthly_revenue >= env_float("AI_STOCK_MIN_MONTHLY_REVENUE", 80000.0):
            macro_score += 3

        if macro_score < min_score:
            continue

        enriched = enrich_revenue_candidate(row, margins)
        enriched["candidate_source"] = "macro_theme"
        enriched["macro_theme"] = theme
        enriched["macro_context"] = MACRO_THEME_CONTEXT.get(theme, theme)
        enriched["macro_candidate_score"] = round(macro_score, 2)
        enriched["institutional_candidate_score"] = institutional_score
        enriched["institutional_candidate_reason"] = reason
        candidates.append(enriched)

    candidates.sort(
        key=lambda row: (
            float(row.get("macro_candidate_score") or 0.0),
            float(row.get("institutional_candidate_score") or 0.0),
            float(row.get("mom") or 0.0),
            float(row.get("yoy") or 0.0),
        ),
        reverse=True,
    )
    selected = candidates[:limit]
    DATA_SOURCE_STATUS["macro_theme"] = (
        f"enabled candidates={len(selected)} source=static_watchlist themes="
        + ",".join(sorted({str(row.get("macro_theme") or "") for row in selected if row.get("macro_theme")})[:5])
    )
    return selected


def institutional_window_sums(records: list[dict[str, float]], days: int) -> dict[str, float]:
    recent = sorted(records, key=lambda item: item["date"], reverse=True)[:days]
    foreign = sum(float(item.get("foreign") or 0.0) for item in recent)
    trust = sum(float(item.get("trust") or 0.0) for item in recent)
    dealer = sum(float(item.get("dealer") or 0.0) for item in recent)
    return {
        "foreign": foreign,
        "trust": trust,
        "dealer": dealer,
        "total": foreign + trust + dealer,
        "positive_days": sum(
            1
            for item in recent
            if float(item.get("foreign") or 0.0) + float(item.get("trust") or 0.0) + float(item.get("dealer") or 0.0) > 0
        ),
    }


def industry_capital_grade(score: float) -> str:
    if score >= 75:
        return "S"
    if score >= 65:
        return "A"
    if score >= 55:
        return "B"
    return "C"


def select_industry_capital_candidates(
    rows: list[StockRow],
    margins: dict[str, dict[str, float]],
    institutional: dict[str, list[dict[str, float]]],
    existing: list[StockRow],
) -> list[StockRow]:
    if not env_bool("AI_STOCK_ENABLE_INDUSTRY_CAPITAL_CANDIDATES", True):
        DATA_SOURCE_STATUS["industry_capital"] = "disabled"
        return []

    scan_limit = env_int("AI_STOCK_INDUSTRY_CAPITAL_SCAN_LIMIT", 220)
    candidate_limit = env_int("AI_STOCK_INDUSTRY_CAPITAL_CANDIDATE_LIMIT", 30)
    min_score = env_float("AI_STOCK_INDUSTRY_CAPITAL_MIN_SCORE", 60.0)
    min_industry_turnover = env_float("AI_STOCK_INDUSTRY_CAPITAL_MIN_TURNOVER_TWD", 300000000.0)
    min_latest_volume = env_float("AI_STOCK_INDUSTRY_CAPITAL_MIN_LATEST_VOLUME_LOTS", 300.0) * 1000
    min_avg_volume = env_float("AI_STOCK_INDUSTRY_CAPITAL_MIN_AVG_VOLUME_LOTS", 500.0) * 1000
    max_price_change_20d = env_float("AI_STOCK_INDUSTRY_CAPITAL_MAX_20D_PRICE_CHANGE", 30.0)
    min_close = env_float("AI_STOCK_INDUSTRY_CAPITAL_MIN_CLOSE", 10.0)
    min_revenue_growth = env_float("AI_STOCK_INDUSTRY_CAPITAL_MIN_REVENUE_GROWTH", -10.0)

    existing_ids = {str(row.get("stock_id") or "") for row in existing}
    preselected: list[tuple[float, StockRow]] = []
    for row in rows:
        stock_id = str(row.get("stock_id") or "")
        if not stock_id or stock_id in existing_ids:
            continue
        records = institutional.get(stock_id, [])
        if not records:
            continue
        one = institutional_window_sums(records, 1)
        five = institutional_window_sums(records, 5)
        ten = institutional_window_sums(records, 10)
        if max(one["total"], five["total"], ten["total"]) <= 0:
            continue
        preliminary = max(one["total"] * 5, five["total"] * 2, ten["total"])
        preselected.append((preliminary, row))

    preselected.sort(key=lambda item: item[0], reverse=True)
    scanned: list[StockRow] = []
    for _score, row in preselected[:scan_limit]:
        enriched = enrich_revenue_candidate(row, margins)
        enriched["candidate_source"] = "industry_capital"
        enriched["industry_theme"] = classify_industry_theme(enriched)
        try:
            enriched.update(calculate_technical_score(enriched["stock_id"]))
        except Exception:
            continue
        records = institutional.get(enriched["stock_id"], [])
        one = institutional_window_sums(records, 1)
        five = institutional_window_sums(records, 5)
        ten = institutional_window_sums(records, 10)
        close = float(enriched.get("close") or 0.0)
        turnover = float(enriched.get("turnover_20d_avg") or 0.0)
        if close <= 0 or turnover <= 0:
            continue
        turnover_10d_estimate = turnover * 10
        enriched["industry_capital_1d_amount"] = one["total"] * close
        enriched["industry_capital_5d_amount"] = five["total"] * close
        enriched["industry_capital_10d_amount"] = ten["total"] * close
        enriched["industry_foreign_10d_amount"] = ten["foreign"] * close
        enriched["industry_trust_10d_amount"] = ten["trust"] * close
        enriched["industry_dealer_10d_amount"] = ten["dealer"] * close
        enriched["industry_capital_ratio"] = ten["total"] * close / turnover_10d_estimate if turnover_10d_estimate > 0 else 0.0
        enriched["industry_capital_positive"] = ten["total"] > 0
        scanned.append(enriched)

    if not scanned:
        DATA_SOURCE_STATUS["industry_capital"] = "empty"
        return []

    market_5d = median([float(row.get("price_change_5d") or 0.0) for row in scanned])
    market_10d = median([float(row.get("price_change_10d") or 0.0) for row in scanned])
    market_20d = median([float(row.get("price_change_20d") or 0.0) for row in scanned])

    by_industry: dict[str, list[StockRow]] = {}
    for row in scanned:
        industry = str(row.get("industry_theme") or row.get("industry_category") or "unknown")
        by_industry.setdefault(industry, []).append(row)

    industry_stats: dict[str, dict[str, Any]] = {}
    for industry, industry_rows in by_industry.items():
        turnover = sum(float(row.get("turnover_20d_avg") or 0.0) for row in industry_rows)
        if turnover < min_industry_turnover:
            continue
        inst_10d_amount = sum(float(row.get("industry_capital_10d_amount") or 0.0) for row in industry_rows)
        foreign_10d_amount = sum(float(row.get("industry_foreign_10d_amount") or 0.0) for row in industry_rows)
        trust_10d_amount = sum(float(row.get("industry_trust_10d_amount") or 0.0) for row in industry_rows)
        buy_count = sum(1 for row in industry_rows if float(row.get("industry_capital_10d_amount") or 0.0) > 0)
        buy_ratio = buy_count / len(industry_rows) if industry_rows else 0.0
        volume_ratio = median([float(row.get("volume_ratio") or 0.0) for row in industry_rows])
        price_5d = median([float(row.get("price_change_5d") or 0.0) for row in industry_rows])
        price_10d = median([float(row.get("price_change_10d") or 0.0) for row in industry_rows])
        price_20d = median([float(row.get("price_change_20d") or 0.0) for row in industry_rows])
        relative_strength = (price_5d - market_5d) * 0.4 + (price_10d - market_10d) * 0.3 + (price_20d - market_20d) * 0.3
        turnover_10d_estimate = turnover * 10
        capital_ratio = inst_10d_amount / turnover_10d_estimate if turnover_10d_estimate > 0 else 0.0
        foreign_ratio = foreign_10d_amount / turnover_10d_estimate if turnover_10d_estimate > 0 else 0.0
        trust_ratio = trust_10d_amount / turnover_10d_estimate if turnover_10d_estimate > 0 else 0.0
        non_overheated = 5.0 if price_20d <= 15 else 2.0 if price_20d <= 25 else 0.0
        score = (
            clip(capital_ratio / 0.12 * 30, 0, 30)
            + clip(trust_ratio / 0.05 * 20, 0, 20)
            + clip(foreign_ratio / 0.08 * 15, 0, 15)
            + clip(buy_ratio * 10, 0, 10)
            + clip(max(volume_ratio - 0.8, 0) / 0.8 * 10, 0, 10)
            + clip((relative_strength + 5) / 15 * 10, 0, 10)
            + non_overheated
        )
        grade = industry_capital_grade(score)
        industry_stats[industry] = {
            "industry": industry,
            "score": round(score, 1),
            "grade": grade,
            "count": len(industry_rows),
            "buy_count": buy_count,
            "capital_ratio": capital_ratio,
            "foreign_ratio": foreign_ratio,
            "trust_ratio": trust_ratio,
            "volume_ratio": volume_ratio,
            "price_5d": price_5d,
            "price_10d": price_10d,
            "price_20d": price_20d,
            "relative_strength": relative_strength,
        }

    selected: list[StockRow] = []
    for industry, stat in sorted(industry_stats.items(), key=lambda item: item[1]["score"], reverse=True):
        if stat["score"] < min_score or stat["grade"] == "C" or stat["buy_count"] < 2:
            continue
        per_industry_limit = {"S": 5, "A": 3, "B": 2}.get(str(stat["grade"]), 0)
        picked = 0
        industry_rows = sorted(
            by_industry.get(industry, []),
            key=lambda row: (
                float(row.get("industry_capital_ratio") or 0.0),
                float(row.get("industry_capital_10d_amount") or 0.0),
                -max(float(row.get("price_change_20d") or 0.0), 0.0),
            ),
            reverse=True,
        )
        for row in industry_rows:
            if picked >= per_industry_limit or len(selected) >= candidate_limit:
                break
            latest_inst = institutional_window_sums(institutional.get(row["stock_id"], []), 1)["total"]
            five_inst = institutional_window_sums(institutional.get(row["stock_id"], []), 5)["total"]
            price_change_20d = float(row.get("price_change_20d") or 0.0)
            close = float(row.get("close") or 0.0)
            ma20 = float(row.get("ma20") or 0.0)
            ma60 = float(row.get("ma60") or 0.0)
            if latest_inst <= 0 and five_inst <= 0:
                continue
            if float(row.get("latest_volume") or 0.0) < min_latest_volume or float(row.get("volume_20d_avg") or 0.0) < min_avg_volume:
                continue
            if close < min_close or price_change_20d > max_price_change_20d:
                continue
            if ma20 > 0 and ma60 > 0 and close < ma20 and close < ma60 * 0.98:
                continue
            if max(float(row.get("yoy") or 0.0), float(row.get("acc_yoy") or 0.0)) < min_revenue_growth:
                continue
            stock_ratio = float(row.get("industry_capital_ratio") or 0.0)
            early_score = 20 if price_change_20d < 15 else 10 if price_change_20d < 25 else 0
            row["industry_capital_score"] = round(
                clip(stat["score"] * 0.45 + clip(stock_ratio / 0.12 * 25, 0, 25) + early_score + min(max(float(row.get("volume_ratio") or 0.0), 0), 2) * 5, 0, 100),
                1,
            )
            row["industry_capital_grade"] = stat["grade"]
            row["industry_capital_reason"] = (
                f"{stat['grade']} capital={stat['capital_ratio'] * 100:.1f}% "
                f"buy={stat['buy_count']}/{stat['count']} rs={stat['relative_strength']:.1f}"
            )
            selected.append(row)
            picked += 1

    selected.sort(key=lambda row: float(row.get("industry_capital_score") or 0.0), reverse=True)
    top_industries = [
        item
        for item in sorted(industry_stats.values(), key=lambda item: item["score"], reverse=True)
        if item["score"] >= min_score and item["grade"] != "C" and item["buy_count"] >= 2
    ][:3]
    if top_industries:
        DATA_SOURCE_STATUS["industry_capital"] = " | ".join(
            f"{item['industry']}:{item['grade']}{item['score']:.1f}/{item['capital_ratio'] * 100:.1f}%/{item['buy_count']}"
            for item in top_industries
        )
    else:
        DATA_SOURCE_STATUS["industry_capital"] = "empty"
    return selected[:candidate_limit]


def merge_candidate_pools(
    fundamental: list[StockRow],
    institutional_candidates: list[StockRow],
    industry_capital_candidates: list[StockRow] | None = None,
    macro_candidates: list[StockRow] | None = None,
) -> list[StockRow]:
    merged: list[StockRow] = []
    seen: set[str] = set()

    for pool, default_source in [
        (fundamental, "fundamental"),
        (institutional_candidates, "institutional"),
        (industry_capital_candidates or [], "industry_capital"),
        (macro_candidates or [], "macro_theme"),
    ]:
        for row in pool:
            row["candidate_source"] = str(row.get("candidate_source") or default_source)
            if row["stock_id"] in seen:
                continue
            merged.append(row)
            seen.add(row["stock_id"])

    DATA_SOURCE_STATUS["candidate_pool"] = (
        f"fundamental={len(fundamental)} institutional={len(institutional_candidates)} "
        f"industry_capital={len(industry_capital_candidates or [])} macro={len(macro_candidates or [])} merged={len(merged)}"
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
            payload = {}
        fields = payload.get("fields", []) if isinstance(payload, dict) else []
        data = payload.get("data", []) if isinstance(payload, dict) else []
        for values in data if fields else []:
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


def normalize_market_date(value: object) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 8:
        return digits
    if len(digits) == 7:
        return f"{int(digits[:3]) + 1911:04d}{digits[3:]}"
    return digits


def _table_rows(payload: Any, required_field: str) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    tables = payload.get("tables", [])
    for table in tables if isinstance(tables, list) else []:
        fields = table.get("fields", []) if isinstance(table, dict) else []
        data = table.get("data", []) if isinstance(table, dict) else []
        if required_field in fields and isinstance(data, list):
            return [dict(zip(fields, values)) for values in data if isinstance(values, list)]
    return []


def fetch_all_market_day(day: datetime.date) -> list[StockRow]:
    date_key = day.strftime("%Y%m%d")
    cached = read_cache(f"all_market_day_{date_key}")
    if isinstance(cached, list) and cached:
        return cached

    normalized: list[StockRow] = []
    try:
        payload = fetch_with_retry(
            TWSE_ALL_MARKET_DAY_URL,
            params={"response": "json", "date": date_key, "type": "ALLBUT0999"},
            tries=1,
            timeout=25,
        )
        for row in _table_rows(payload, "證券代號"):
            stock_id = str(row.get("證券代號") or "").strip()
            if not re.fullmatch(r"\d{4}", stock_id):
                continue
            close = clean_number(row.get("收盤價"))
            volume = clean_number(row.get("成交股數"))
            turnover = clean_number(row.get("成交金額"))
            if close <= 0 or volume <= 0 or turnover <= 0:
                continue
            normalized.append(
                {
                    "stock_id": stock_id,
                    "market_type": "listed",
                    "date": date_key,
                    "open": clean_number(row.get("開盤價")),
                    "high": clean_number(row.get("最高價")),
                    "low": clean_number(row.get("最低價")),
                    "close": close,
                    "volume": volume,
                    "turnover": turnover,
                }
            )
    except Exception:
        pass

    try:
        payload = fetch_with_retry(
            TPEX_ALL_MARKET_DAY_URL,
            params={"date": day.strftime("%Y/%m/%d"), "id": "", "response": "json"},
            tries=1,
            timeout=25,
        )
        for row in _table_rows(payload, "代號"):
            stock_id = str(row.get("代號") or "").strip()
            if not re.fullmatch(r"\d{4}", stock_id):
                continue
            close = clean_number(row.get("收盤"))
            volume = clean_number(row.get("成交股數"))
            turnover = clean_number(row.get("成交金額(元)"))
            if close <= 0 or volume <= 0 or turnover <= 0:
                continue
            normalized.append(
                {
                    "stock_id": stock_id,
                    "market_type": "otc",
                    "date": date_key,
                    "open": clean_number(row.get("開盤")),
                    "high": clean_number(row.get("最高")),
                    "low": clean_number(row.get("最低")),
                    "close": close,
                    "volume": volume,
                    "turnover": turnover,
                }
            )
    except Exception:
        pass

    if normalized:
        write_cache(f"all_market_day_{date_key}", normalized)
    return normalized


def fetch_all_market_price_history(target_days: int = 65) -> dict[str, list[StockRow]]:
    by_stock: dict[str, list[StockRow]] = {}
    trading_days = 0
    today = datetime.now(TAIPEI_TZ).date()
    max_calendar_days = max(target_days * 2, 120)
    for offset in range(max_calendar_days):
        day = today - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        daily_rows = fetch_all_market_day(day)
        if not daily_rows:
            continue
        trading_days += 1
        for row in daily_rows:
            by_stock.setdefault(str(row["stock_id"]), []).append(row)
        if trading_days >= target_days:
            break
        time.sleep(0.03)

    for records in by_stock.values():
        records.sort(key=lambda item: str(item.get("date") or ""))
    DATA_SOURCE_STATUS["price"] = (
        f"TWSE+TPEx bulk trading_days={trading_days} stocks={len(by_stock)}"
        if by_stock
        else "bulk_market_unavailable"
    )
    return by_stock


def moving_average(values: list[float], window: int) -> float:
    if len(values) < window:
        return 0.0
    return sum(values[-window:]) / window


def calculate_technical_from_records(records: list[StockRow]) -> dict[str, Any]:
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
            "price_change_5d": 0.0,
            "price_change_10d": 0.0,
            "price_change_20d": 0.0,
            "break_20d_high": False,
            "break_60d_high": False,
            "break_120d_high": False,
            "long_upper_wick": False,
            "volume_price_divergence": False,
        }

    close = closes[-1]
    ma5 = moving_average(closes, 5)
    ma20 = moving_average(closes, 20)
    ma60 = moving_average(closes, 60)
    volume20 = moving_average(volumes, 20)
    latest_volume = volumes[-1]
    volume_ratio = volumes[-1] / volume20 if volume20 else 0.0
    turnover_values = [float(row.get("turnover") or 0.0) for row in records[-20:]]
    turnover_20d_avg = sum(turnover_values) / len(turnover_values) if turnover_values else close * volume20
    price_change_1d = ((close / closes[-2]) - 1) * 100 if len(closes) >= 2 and closes[-2] else 0.0
    price_change_5d = ((close / closes[-6]) - 1) * 100 if len(closes) >= 6 and closes[-6] else 0.0
    price_change_10d = ((close / closes[-11]) - 1) * 100 if len(closes) >= 11 and closes[-11] else 0.0
    price_change_20d = ((close / closes[-21]) - 1) * 100 if len(closes) >= 21 and closes[-21] else 0.0
    prior_high_20 = max(closes[-21:-1]) if len(closes) >= 21 else max(closes[:-1])
    prior_high_60 = max(closes[-61:-1]) if len(closes) >= 61 else max(closes[:-1])
    prior_high_120 = max(closes[-121:-1]) if len(closes) >= 121 else max(closes[:-1])
    break_20d_high = close > prior_high_20
    break_60d_high = close > prior_high_60
    break_120d_high = close > prior_high_120
    latest = records[-1]
    latest_open = float(latest.get("open") or close)
    latest_high = float(latest.get("high") or max(latest_open, close))
    latest_low = float(latest.get("low") or min(latest_open, close))
    price_range = max(latest_high - latest_low, 0.0)
    upper_wick = max(latest_high - max(latest_open, close), 0.0)
    long_upper_wick = price_range > 0 and upper_wick / price_range >= 0.45 and volume_ratio >= 1.5
    volume_price_divergence = volume_ratio >= 2.5 and price_change_1d <= 1.0

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
        "price_change_5d": round(price_change_5d, 2),
        "price_change_10d": round(price_change_10d, 2),
        "price_change_20d": round(price_change_20d, 2),
        "break_20d_high": break_20d_high,
        "break_60d_high": break_60d_high,
        "break_120d_high": break_120d_high,
        "long_upper_wick": long_upper_wick,
        "volume_price_divergence": volume_price_divergence,
    }


def calculate_technical_score(stock_id: str) -> dict[str, Any]:
    return calculate_technical_from_records(fetch_price_history(stock_id))


def recent_market_dates(days: int = 12) -> list[str]:
    today = datetime.now(TAIPEI_TZ).date()
    dates = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        if day.weekday() < 5:
            dates.append(day.strftime("%Y%m%d"))
    return dates


def fetch_tpex_institutional_day(date_key: str) -> list[StockRow]:
    cached = read_cache(f"tpex_institutional_{date_key}")
    if isinstance(cached, list):
        return cached

    day = datetime.strptime(date_key, "%Y%m%d").date()
    roc_date = f"{day.year - 1911:03d}/{day.month:02d}/{day.day:02d}"
    try:
        text = request_text(
            TPEX_INSTITUTIONAL_CSV_URL,
            params={"l": "zh-tw", "o": "csv", "se": "EW", "t": "D", "d": roc_date},
            timeout=30,
        )
    except Exception:
        return []

    lines = [line for line in text.splitlines() if line.strip()]
    header_index = next((index for index, line in enumerate(lines) if line.startswith("代號,")), -1)
    if header_index < 0:
        return []
    title_match = re.search(r"(\d{3})年(\d{2})月(\d{2})日", lines[0])
    actual_date_key = date_key
    if title_match:
        actual_date_key = (
            f"{int(title_match.group(1)) + 1911:04d}"
            f"{title_match.group(2)}{title_match.group(3)}"
        )

    rows: list[StockRow] = []
    for row in csv.DictReader(io.StringIO("\n".join(lines[header_index:]))):
        stock_id = str(row.get("代號") or "").strip()
        if not re.fullmatch(r"\d{4}", stock_id):
            continue
        rows.append(
            {
                "stock_id": stock_id,
                "date": float(actual_date_key),
                "trade_date": actual_date_key,
                "foreign": clean_number(row.get("外資及陸資(不含外資自營商)-買賣超股數")),
                "trust": clean_number(row.get("投信-買賣超股數")),
                "dealer": clean_number(row.get("自營商-買賣超股數")),
            }
        )
    if rows:
        write_cache(f"tpex_institutional_{date_key}", rows)
    return rows


def fetch_institutional_maps() -> dict[str, list[dict[str, float]]]:
    by_stock: dict[str, list[dict[str, float]]] = {}
    listed_rows = 0
    otc_rows = 0
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
            dealer = pick_exact_number(
                row,
                [
                    "自營商買賣超股數",
                    "自營商買賣超股數(自行買賣)",
                    "自營商買賣超股數(避險)",
                ],
            )
            by_stock.setdefault(stock_id, []).append(
                {"date": float(date), "trade_date": date, "foreign": foreign, "trust": trust, "dealer": dealer}
            )
            listed_rows += 1
        for item in fetch_tpex_institutional_day(date):
            stock_id = str(item.get("stock_id") or "")
            if not stock_id:
                continue
            by_stock.setdefault(stock_id, []).append(
                {
                    "date": float(item["date"]),
                    "trade_date": str(item["trade_date"]),
                    "foreign": float(item["foreign"]),
                    "trust": float(item["trust"]),
                    "dealer": float(item["dealer"]),
                }
            )
            otc_rows += 1
        if by_stock:
            time.sleep(0.05)
    for stock_id, records in list(by_stock.items()):
        deduplicated: dict[str, dict[str, float]] = {}
        for item in records:
            date_key = normalize_market_date(item.get("trade_date") or item.get("date"))
            if date_key:
                deduplicated[date_key] = item
        by_stock[stock_id] = sorted(
            deduplicated.values(),
            key=lambda item: item["date"],
            reverse=True,
        )
    DATA_SOURCE_STATUS["institutional"] = (
        f"TWSE+TPEx listed_rows={listed_rows} otc_rows={otc_rows} stocks={len(by_stock)}"
    )
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
    records_5d = all_records[:5]
    foreign_values = [item["foreign"] for item in records]
    trust_values = [item["trust"] for item in records]
    dealer_values = [item.get("dealer", 0.0) for item in records]
    dealer_5d_values = [item.get("dealer", 0.0) for item in records_5d]
    foreign_20d_values = [item["foreign"] for item in records_20d]
    trust_20d_values = [item["trust"] for item in records_20d]
    dealer_20d_values = [item.get("dealer", 0.0) for item in records_20d]
    foreign_sum = sum(foreign_values)
    trust_sum = sum(trust_values)
    dealer_sum = sum(dealer_values)
    institutional_sum = foreign_sum + trust_sum + dealer_sum
    institutional_20d_sum = sum(foreign_20d_values) + sum(trust_20d_values) + sum(dealer_20d_values)
    latest_foreign_net = foreign_values[0] if foreign_values else 0.0
    latest_trust_net = trust_values[0] if trust_values else 0.0
    latest_dealer_net = dealer_values[0] if dealer_values else 0.0
    latest_institutional_net = latest_foreign_net + latest_trust_net + latest_dealer_net
    prior_institutional_net = (
        foreign_values[1] + trust_values[1] + dealer_values[1]
        if len(foreign_values) >= 2 and len(trust_values) >= 2 and len(dealer_values) >= 2
        else 0.0
    )
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
        "dealer_5d_sum": round(sum(dealer_5d_values), 0),
        "dealer_10d_sum": round(dealer_sum, 0),
        "latest_foreign_net": round(latest_foreign_net, 0),
        "latest_trust_net": round(latest_trust_net, 0),
        "latest_dealer_net": round(latest_dealer_net, 0),
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
    institutional = fetch_institutional_maps()
    event_risk_map = fetch_event_risk_map()
    price_history = fetch_all_market_price_history(
        env_int("AI_STOCK_UNIVERSE_HISTORY_DAYS", 65)
    )
    universe = build_investable_universe(
        revenue_rows,
        margins,
        financial_quality,
        institutional,
        event_risk_map,
        price_history,
    )
    fundamental_growth = select_fundamental_growth_pool(universe)
    fundamental_turns = select_fundamental_turn_pool(universe)
    early_institutional = select_early_institutional_pool(universe)
    industry_stats = build_industry_capital_stats(universe)
    industry_breakouts = select_industry_breakout_pool(universe, industry_stats)
    non_mainstream_breakouts = select_non_mainstream_breakout_pool(
        universe,
        industry_stats,
    )
    candidates = merge_preselection_pools(
        universe,
        fundamental_growth,
        fundamental_turns,
        early_institutional,
        industry_breakouts,
        non_mainstream_breakouts,
    )
    margin_map = fetch_margin_map()
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
        if not enriched.get("latest_trade_date"):
            records = price_history.get(enriched["stock_id"], [])
            enriched.update(
                calculate_technical_from_records(records)
                if records
                else calculate_technical_score(enriched["stock_id"])
            )
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
    summarize_industry_momentum(universe)

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
    audit_candidate_pool(candidates, universe)
    apply_recommendation_history(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks)
    return top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks, exit_alerts


def build_markdown_report(top_stocks: list[StockRow], radar_stocks: list[StockRow]) -> str:
    taipei_now = datetime.now(TAIPEI_TZ)
    top_display = top_stocks[:10]
    radar_display = radar_stocks[:5]
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
    "fundamental_floor": "基本面底線未通過",
    "entry_timing_risk": "進場時機風險",
    "weak_fundamental_pre_score": "基本面預分不足",
    "revenue_growth_below_floor": "營收成長低於底線",
    "eps_loss_without_turnaround": "虧損且未見明確轉機",
    "far_above_ma20": "股價遠離月線",
    "price_volume_overheat": "價量過熱",
    "latest_institutional_big_sell": "法人最新大幅轉賣",
    "latest_institutional_selling": "法人當日轉賣",
    "long_upper_wick": "爆量長上影",
    "volume_price_divergence": "爆量價滯",
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
    "energy_petrochemical": "能源/石化",
    "shipping_logistics": "航運/物流",
    "raw_materials": "原物料",
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
    growth = _status_value(status, "growth")
    turns = _status_value(status, "turn")
    institutional = _status_value(status, "institutional")
    industry = _status_value(status, "industry")
    non_mainstream = _status_value(status, "non_mainstream")
    merged = _status_value(status, "merged")
    if growth and turns and institutional and industry and non_mainstream and merged:
        return (
            f"成長{growth} / 轉折{turns} / 法人{institutional} / "
            f"產業{industry} / 非主流{non_mainstream} / 深算{merged}檔"
        )
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
        capital = values[2].replace("capital", "%") if len(values) > 2 else ""
        label = _readable_theme(theme)
        summary = " / ".join(
            item
            for item in [
                f"20日{price}",
                f"量比{volume}" if volume else "",
                f"法人資金占比{capital}" if capital else "",
            ]
            if item
        )
        items.append(f"{label}：{summary}")
    return "；".join(items) if items else "暫無明顯產業輪動"


def _readable_macro_theme_status(status: str) -> str:
    text = str(status or "")
    if not text or text == "unknown":
        return "未啟用"
    if text == "disabled":
        return "未啟用"
    candidates = _status_value(text, "candidates")
    themes = _status_value(text, "themes") or ""
    labels = [
        _readable_theme(theme.strip())
        for theme in str(themes).split(",")
        if theme.strip()
    ]
    if candidates is not None:
        return f"{candidates} 檔" + (f"；{ '、'.join(labels) }" if labels else "")
    return text


def _readable_industry_capital_status(status: str) -> str:
    text = str(status or "")
    if not text or text in {"unknown", "empty"}:
        return "暫無明顯法人資金突破產業"
    if text == "disabled":
        return "未啟用"
    items = []
    for part in text.split(" | "):
        if ":" not in part:
            continue
        industry, metrics = part.split(":", 1)
        values = metrics.split("/")
        grade_score = values[0] if len(values) > 0 else ""
        capital_ratio = values[1] if len(values) > 1 else ""
        buy_count = values[2] if len(values) > 2 else ""
        label = _readable_theme(industry)
        detail = " / ".join(
            item
            for item in [
                grade_score,
                f"資金占比{capital_ratio}" if capital_ratio else "",
                f"買超{buy_count}檔" if buy_count else "",
            ]
            if item
        )
        if detail:
            items.append(f"{label}：{detail}")
    return "；".join(items) if items else text


def _readable_candidate_audit(status: str) -> str:
    text = str(status or "")
    if not text or text == "unknown":
        return "尚未建立"
    state = _status_value(text, "status") or "unknown"
    added = _status_value(text, "added") or "0"
    removed = _status_value(text, "removed") or "0"
    overlap = _status_value(text, "overlap") or "0%"
    pool_hash = _status_value(text, "hash") or "missing"
    trade_date = _status_value(text, "trade_date") or "missing"
    warnings = _status_value(text, "warnings") or "none"
    warning_labels = {
        "pool_not_full": "候選池不足",
        "pool_identical_multiple_days": "候選池連續多日相同",
        "common_trade_date_not_advanced": "共同交易日未更新",
        "common_trade_date_missing": "共同交易日缺漏",
    }
    date_labels = [
        f"{value[:4]}-{value[4:6]}-{value[6:8]}"
        if len(value) == 8 and value.isdigit()
        else value
        for value in trade_date.split(",")
    ]
    readable_warnings = "、".join(
        warning_labels.get(item, item)
        for item in warnings.split(",")
        if item and item != "none"
    )
    state_label = "正常" if state == "normal" else f"警示({readable_warnings or '資料異常'})"
    return (
        f"新增{added} / 退出{removed} / 重複{overlap} / "
        f"交易日{','.join(date_labels)} / 池碼{pool_hash} / {state_label}"
    )


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
    investable = _status_value(DATA_SOURCE_STATUS.get("investable_universe", ""), "investable") or "0"
    deep = _status_value(DATA_SOURCE_STATUS.get("preselection", ""), "deep") or "0"
    return [
        f"全市場篩選：可投資 {investable} 檔 / 深度分析 {deep} 檔",
        f"候選稽核：{_readable_candidate_audit(DATA_SOURCE_STATUS.get('candidate_audit', 'unknown'))}",
        f"大盤濾網：{_readable_market_score(DATA_SOURCE_STATUS.get('market_score', 'unknown'))}",
        f"產業輪動：{_readable_industry_momentum(DATA_SOURCE_STATUS.get('industry_momentum', 'unknown'))}",
        f"法人資金產業：{_readable_industry_capital_status(DATA_SOURCE_STATUS.get('industry_capital', 'unknown'))}",
        f"非主流突破：{_readable_macro_theme_status(DATA_SOURCE_STATUS.get('macro_theme', 'unknown'))}",
        f"今日等級：A {a_count} / B {b_count} / C {c_count}",
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
    lines = ["AI質化摘要（3檔）：輔助檢查成長持續性與風險"]
    for row in rows:
        review_line = format_ai_review(row)
        if review_line:
            lines.append(f"{row.get('stock_id')} {row.get('stock_name')}｜{review_line}")
    return lines


LINE_CANDIDATE_SOURCE_LABELS = {
    "fundamental_growth": "基本面成長",
    "fundamental_turn": "獲利轉折",
    "early_institutional": "法人早期建倉",
    "industry_breakout": "產業資金突破",
    "non_mainstream_breakout": "非主流突破",
    "composite_fill": "綜合預分",
}


def _line_candidate_sources(row: StockRow, limit: int = 2) -> str:
    values = row.get("candidate_sources") or str(row.get("candidate_source") or "").split(",")
    if not isinstance(values, list):
        values = [str(values)]
    labels = []
    for value in values:
        key = str(value or "").strip()
        label = LINE_CANDIDATE_SOURCE_LABELS.get(key, key)
        if label and label not in labels:
            labels.append(label)
    return "+".join(labels[:limit]) or "綜合分析"


def _line_margin_summary(row: StockRow) -> str:
    margins = [
        float(row.get("gross_margin") or 0.0),
        float(row.get("operating_margin") or 0.0),
        float(row.get("net_margin") or 0.0),
    ]
    if not any(abs(value) >= 0.01 for value in margins):
        return "毛/營/淨待補"
    return f"毛/營/淨 {margins[0]:.1f}/{margins[1]:.1f}/{margins[2]:.1f}%"


def _line_valuation_summary(row: StockRow) -> str:
    pe = float(row.get("pe_ratio") or 0.0)
    pbr = float(row.get("pbr") or 0.0)
    roe = float(row.get("roe") or 0.0)
    values = []
    if pe > 0:
        values.append(f"PER {pe:.1f}")
    if pbr > 0:
        values.append(f"PBR {pbr:.1f}")
    if roe:
        values.append(f"ROE {roe:.1f}%")
    return "/".join(values) if values else "估值來源待補"


def _line_latest_institutional(row: StockRow) -> str:
    foreign = shares_to_lots(row.get("latest_foreign_net", 0))
    trust = shares_to_lots(row.get("latest_trust_net", 0))
    dealer = shares_to_lots(row.get("latest_dealer_net", 0))
    return f"1日外/投/自 {foreign:,.0f}/{trust:,.0f}/{dealer:,.0f}張"


def _line_score_breakdown(row: StockRow) -> str:
    return (
        f"基{float(row.get('fundamental_score') or 0):.1f} "
        f"籌{float(row.get('chip_score') or 0):.1f} "
        f"技{float(row.get('technical_score') or 0):.1f} "
        f"估{float(row.get('valuation_score') or 0):.1f} "
        f"產{float(row.get('industry_score') or 0):.1f} "
        f"EPS{float(row.get('eps_acceleration_score') or 0):+.1f} "
        f"風險-{float(row.get('risk_penalty') or 0):.1f}"
    )


def build_line_message(
    top_stocks: list[StockRow],
    opportunity_stocks: list[StockRow],
    watchlist_stocks: list[StockRow],
    radar_stocks: list[StockRow],
    exit_alerts: list[StockRow],
) -> str:
    rows_for_grade = top_stocks or opportunity_stocks or watchlist_stocks or radar_stocks
    if not rows_for_grade:
        return "AI Stock Bot V2\n====================\n今日沒有股票通過篩選。"

    investable = _status_value(DATA_SOURCE_STATUS.get("investable_universe", ""), "investable") or "0"
    deep = _status_value(DATA_SOURCE_STATUS.get("preselection", ""), "deep") or "0"
    audit_status = DATA_SOURCE_STATUS.get("candidate_audit", "unknown")
    trade_date = format_trade_date(_status_value(audit_status, "trade_date") or "")
    lines = [
        "AI Stock Bot V2 分層選股",
        f"完整財經看板：{DAILY_FINANCE_REPORT_URL}",
        "",
        _line_grade_note(rows_for_grade),
        "模型：基本面35 + 法人籌碼25 + 技術面20 + 估值10 + 產業10",
        "====================",
        f"資料日：{trade_date or '待確認'}｜三層篩選：可投資 {investable} → 深度分析 {deep} 檔",
        f"候選池：{_readable_candidate_pool(DATA_SOURCE_STATUS.get('candidate_pool', 'unknown'))}",
        f"候選稽核：{_readable_candidate_audit(audit_status)}",
        f"大盤：{_readable_market_score(DATA_SOURCE_STATUS.get('market_score', 'unknown'))}",
        f"產業資金：{_readable_industry_capital_status(DATA_SOURCE_STATUS.get('industry_capital', 'unknown'))}",
        f"產業輪動：{_readable_industry_momentum(DATA_SOURCE_STATUS.get('industry_momentum', 'unknown'))}",
        (
            f"分層結果：Top {len(top_stocks)} / 機會 {len(opportunity_stocks)} / "
            f"Watch {len(watchlist_stocks)} / 雷達 {len(radar_stocks)} / Exit {len(exit_alerts)}"
        ),
    ]
    ai_review_lines = build_ai_review_lines(
        top_stocks,
        opportunity_stocks,
        watchlist_stocks,
        radar_stocks,
    )
    if ai_review_lines:
        lines.extend(["====================", *ai_review_lines])
    lines.append("====================")
    top_display = top_stocks[: env_int("AI_STOCK_LINE_TOP_DISPLAY_LIMIT", 3)]
    opportunity_display = opportunity_stocks[: env_int("AI_STOCK_LINE_OPPORTUNITY_DISPLAY_LIMIT", 3)]
    watchlist_display = watchlist_stocks[: env_int("AI_STOCK_LINE_WATCH_DISPLAY_LIMIT", 3)]
    radar_display = radar_stocks[: env_int("AI_STOCK_LINE_RADAR_DISPLAY_LIMIT", 3)]
    exit_display = exit_alerts[: env_int("AI_STOCK_LINE_EXIT_DISPLAY_LIMIT", 3)]

    lines.append(
        "正式 Top（3檔）：品質、基本面、進場時機通過，且法人當日未轉賣"
        if top_stocks
        else "正式 Top：今日沒有完全通過品質、風險與法人當日方向的標的"
    )
    for row in top_display:
        rank = int(row["rank"])
        breakout = "創120日高" if row.get("break_120d_high") else "未創高"
        strong = " 三率三升" if row.get("triple_margin_up") else ""
        lines.extend([
            f"{rank}. {row['stock_id']} {row['stock_name']}{strong}｜{row.get('model_grade', '')}級 {row['total_score']:.1f}｜{_readable_theme(row.get('industry_theme'))}｜{_line_candidate_sources(row)}",
            f"分數｜{_line_score_breakdown(row)}",
            (
                f"基本｜營收YoY {float(row.get('yoy') or 0):+.1f}% / 累計 {float(row.get('acc_yoy') or 0):+.1f}%｜"
                f"{_line_margin_summary(row)}｜{_line_valuation_summary(row)}"
            ),
            (
                f"籌碼｜{_line_latest_institutional(row)}｜5日外/投 "
                f"{shares_to_lots(row.get('foreign_5d_sum', 0)):,.0f}/{shares_to_lots(row.get('trust_5d_sum', 0)):,.0f}張｜"
                f"1日 {float(row.get('price_change_1d') or 0):+.1f}% / 20日 {float(row.get('price_change_20d') or 0):+.1f}% / 量比 {float(row.get('volume_ratio') or 0):.2f}x｜{breakout}"
            ),
        ])

    if len(top_stocks) > len(top_display):
        lines.append(f"另有 {len(top_stocks) - len(top_display)} 檔正式 Top 未顯示")

    if opportunity_display:
        lines.extend(["====================", "機會股（3檔）：早期資金與基本面具潛力，尚未通過正式Top全部門檻"])
        for row in opportunity_display:
            lines.extend([
                f"O{row['opportunity_rank']} {row['stock_id']} {row['stock_name']}｜機會 {row.get('opportunity_score', 0):.1f} / 總分 {row['total_score']:.1f}｜{_readable_theme(row.get('industry_theme'))}｜{_line_candidate_sources(row)}",
                f"理由｜{_readable_reason_text(row.get('opportunity_reason'), limit=3)}",
                (
                    f"數據｜營收YoY {float(row.get('yoy') or 0):+.1f}%｜{_line_latest_institutional(row)}｜"
                    f"10日外/投 {shares_to_lots(row.get('foreign_10d_sum', 0)):,.0f}/{shares_to_lots(row.get('trust_10d_sum', 0)):,.0f}張｜"
                    f"20日 {float(row.get('price_change_20d') or 0):+.1f}% / 量比 {float(row.get('volume_ratio') or 0):.2f}x"
                ),
            ])

        if len(opportunity_stocks) > len(opportunity_display):
            lines.append(f"另有 {len(opportunity_stocks) - len(opportunity_display)} 檔機會股未顯示")

    if watchlist_display:
        lines.extend(["====================", "Watchlist（3檔）：分數具潛力，但有明確未通過條件"])
        for row in watchlist_display:
            reason_values = [
                row.get("top_quality_reason"),
                row.get("risk_notes", []),
                row.get("event_risk_reason"),
            ]
            reason = "；".join(_readable_reason_text(item) for item in reason_values if item)
            lines.extend([
                f"W{row['watch_rank']} {row['stock_id']} {row['stock_name']}｜總 {row['total_score']:.1f}｜{_readable_theme(row.get('industry_theme'))}",
                f"未通過｜{reason or '觀察'}",
                (
                    f"數據｜營收YoY {float(row.get('yoy') or 0):+.1f}%｜{_line_latest_institutional(row)}｜"
                    f"5日外/投 {shares_to_lots(row.get('foreign_5d_sum', 0)):,.0f}/{shares_to_lots(row.get('trust_5d_sum', 0)):,.0f}張｜"
                    f"20日 {float(row.get('price_change_20d') or 0):+.1f}% / 量比 {float(row.get('volume_ratio') or 0):.2f}x"
                ),
            ])

    if radar_display:
        lines.extend([
            "====================",
            "法人建倉雷達（3檔）：Top與機會股之外的早期籌碼觀察",
        ])
        for row in radar_display:
            quiet = "｜低調建倉" if row.get("quiet_accumulation") else "｜籌碼觀察"
            lines.extend([
                f"R{row['radar_rank']} {row['stock_id']} {row['stock_name']}｜雷達 {row['accumulation_score']:.1f}{quiet}",
                (
                    f"籌碼｜{_line_latest_institutional(row)}｜近2日法人 {shares_to_lots(row.get('recent_2d_institutional_net', 0)):,.0f}張｜"
                    f"10日外/投 {shares_to_lots(row.get('foreign_10d_sum', 0)):,.0f}/{shares_to_lots(row.get('trust_10d_sum', 0)):,.0f}張"
                ),
                (
                    f"基本｜營收YoY {float(row.get('yoy') or 0):+.1f}% / 累計 {float(row.get('acc_yoy') or 0):+.1f}%｜"
                    f"20日 {float(row.get('price_change_20d') or 0):+.1f}% / 量比 {float(row.get('volume_ratio') or 0):.2f}x"
                ),
            ])
        if len(radar_stocks) > len(radar_display):
            lines.append(f"另有 {len(radar_stocks) - len(radar_display)} 檔法人建倉雷達未顯示")
    if exit_display:
        lines.extend([
            "====================",
            "Exit Alert 持股風險追蹤（3檔）：曾入選且今日風險轉弱",
        ])
        for row in exit_display:
            reasons = _readable_reason_text(row.get("exit_alert_reasons"), limit=3)
            source_label = {
                "top": "正式Top",
                "opportunity": "機會股",
                "watchlist": "Watchlist",
                "radar": "法人雷達",
            }.get(str(row.get("exit_source") or ""), str(row.get("exit_source") or "歷史追蹤"))
            lines.extend([
                f"E{row.get('exit_alert_rank')} {row.get('stock_id')} {row.get('stock_name')}｜來源 {source_label}｜{reasons}",
                (
                    f"數據｜1日 {float(row.get('price_change_1d') or 0):+.1f}% / 20日 {float(row.get('price_change_20d') or 0):+.1f}%｜"
                    f"{_line_latest_institutional(row)}"
                ),
            ])
    return "\n".join(lines)


FLEX_BUCKET_STYLES = {
    "top": {"label": "正式 Top", "color": "#166534", "soft": "#DCFCE7"},
    "opportunity": {"label": "機會股", "color": "#1D4ED8", "soft": "#DBEAFE"},
    "watchlist": {"label": "Watchlist", "color": "#475569", "soft": "#E2E8F0"},
    "radar": {"label": "法人雷達", "color": "#B45309", "soft": "#FEF3C7"},
}
FLEX_GRADE_COLORS = {
    "A": "#166534",
    "B": "#1D4ED8",
    "C": "#B45309",
    "D": "#64748B",
}


def _flex_text(
    text: Any,
    *,
    size: str = "sm",
    color: str = "#334155",
    weight: str | None = None,
    wrap: bool = True,
    flex: int | None = None,
    align: str | None = None,
    max_lines: int | None = None,
) -> dict[str, Any]:
    component: dict[str, Any] = {
        "type": "text",
        "text": str(text or ""),
        "size": size,
        "color": color,
        "wrap": wrap,
    }
    if weight:
        component["weight"] = weight
    if flex is not None:
        component["flex"] = flex
    if align:
        component["align"] = align
    if max_lines is not None:
        component["maxLines"] = max_lines
    return component


def _flex_stat(label: str, value: str) -> dict[str, Any]:
    return {
        "type": "box",
        "layout": "vertical",
        "flex": 1,
        "spacing": "xs",
        "contents": [
            _flex_text(label, size="xxs", color="#64748B", align="center", wrap=False),
            _flex_text(value, size="sm", color="#0F172A", weight="bold", align="center", wrap=False),
        ],
    }


def _flex_signed_lots(value: Any) -> str:
    lots = shares_to_lots(float(value or 0.0))
    return f"{lots:+,.0f}張"


def _flex_compact_reason(value: Any, default: str, limit: int = 2) -> str:
    readable = _readable_reason_text(value, limit=limit)
    return default if readable == "觀察" else readable


def _flex_selection_reason(row: StockRow, bucket: str) -> str:
    if bucket == "opportunity":
        return _flex_compact_reason(row.get("opportunity_reason"), "法人與基本面同步改善")
    if bucket == "watchlist":
        return "分數具潛力，等待品質條件確認"
    if bucket == "radar":
        if row.get("quiet_accumulation"):
            return "法人溫和布局，股價尚未過熱"
        return "法人資金轉強，列入早期觀察"

    reasons = []
    if float(row.get("fundamental_score") or 0.0) >= 25:
        reasons.append("基本面分數穩健")
    institutional_5d = float(row.get("foreign_5d_sum") or 0.0) + float(row.get("trust_5d_sum") or 0.0)
    if institutional_5d > 0:
        reasons.append("法人5日淨買超")
    if float(row.get("price_change_20d") or 0.0) < 15:
        reasons.append("20日漲幅未過熱")
    industry_grade = str(row.get("industry_capital_grade") or "")
    if industry_grade in {"S", "A"}:
        reasons.append(f"產業資金{industry_grade}級")
    return "、".join(reasons[:2]) or "通過正式品質與風險門檻"


def _flex_risk_reason(row: StockRow, bucket: str) -> str:
    risk_values = [
        row.get("event_risk_reason"),
        row.get("risk_notes"),
        row.get("top_quality_reason") if bucket == "watchlist" else "",
    ]
    labels = []
    for value in risk_values:
        if not value:
            continue
        readable = _readable_reason_text(value, limit=2)
        if readable != "觀察":
            labels.extend(part for part in readable.split("、") if part not in labels)
    if float(row.get("price_change_20d") or 0.0) > 20 and "20日漲幅偏高" not in labels:
        labels.append("20日漲幅偏高")
    if float(row.get("volume_ratio") or 0.0) < 0.8 and "量能仍待確認" not in labels:
        labels.append("量能仍待確認")
    latest_institutional = float(row.get("latest_foreign_net") or 0.0) + float(row.get("latest_trust_net") or 0.0)
    if latest_institutional < 0 and "法人當日轉賣" not in labels:
        labels.append("法人當日轉賣")
    return "、".join(labels[:2]) or "留意進場節奏與後續量價"


def _flex_stock_rank(row: StockRow, bucket: str) -> str:
    rank_key = {
        "top": "rank",
        "opportunity": "opportunity_rank",
        "watchlist": "watch_rank",
        "radar": "radar_rank",
    }[bucket]
    prefix = {"top": "", "opportunity": "O", "watchlist": "W", "radar": "R"}[bucket]
    rank = int(float(row.get(rank_key) or 0))
    return f"{prefix}{rank}" if rank else prefix or "-"


def _select_flex_stock_rows(
    top_stocks: list[StockRow],
    opportunity_stocks: list[StockRow],
    watchlist_stocks: list[StockRow],
    radar_stocks: list[StockRow],
) -> list[tuple[str, StockRow]]:
    limit = max(1, min(env_int("AI_STOCK_LINE_FLEX_CARD_LIMIT", 9), LINE_FLEX_CAROUSEL_MAX_BUBBLES - 1))
    sources = {
        "top": top_stocks,
        "opportunity": opportunity_stocks,
        "watchlist": watchlist_stocks,
        "radar": radar_stocks,
    }
    quotas = {"top": 5, "opportunity": 2, "watchlist": 1, "radar": 1}
    selected: list[tuple[str, StockRow]] = []
    selected_ids: set[str] = set()

    def add(bucket: str, row: StockRow) -> None:
        stock_id = str(row.get("stock_id") or "")
        if not stock_id or stock_id in selected_ids or len(selected) >= limit:
            return
        selected.append((bucket, row))
        selected_ids.add(stock_id)

    for bucket in ("top", "opportunity", "watchlist", "radar"):
        for row in sources[bucket][: quotas[bucket]]:
            add(bucket, row)
    if len(selected) < limit:
        for bucket in ("top", "opportunity", "watchlist", "radar"):
            for row in sources[bucket]:
                add(bucket, row)
    return selected


def _flex_summary_bubble(
    top_stocks: list[StockRow],
    opportunity_stocks: list[StockRow],
    watchlist_stocks: list[StockRow],
    radar_stocks: list[StockRow],
) -> dict[str, Any]:
    investable = _status_value(DATA_SOURCE_STATUS.get("investable_universe", ""), "investable") or "0"
    deep = _status_value(DATA_SOURCE_STATUS.get("preselection", ""), "deep") or "0"
    audit = DATA_SOURCE_STATUS.get("candidate_audit", "unknown")
    trade_date = format_trade_date(_status_value(audit, "trade_date") or "")
    audit_state = _status_value(audit, "status") or "unknown"
    audit_label = "正常" if audit_state == "normal" else "需檢查"
    added = _status_value(audit, "added") or "0"
    removed = _status_value(audit, "removed") or "0"
    overlap = _status_value(audit, "overlap") or "0%"
    market_summary = _readable_market_score(DATA_SOURCE_STATUS.get("market_score", "unknown"))
    industry_summary = _readable_industry_capital_status(DATA_SOURCE_STATUS.get("industry_capital", "unknown"))
    if len(industry_summary) > 110:
        industry_summary = industry_summary[:107] + "..."
    count_summary = (
        f"Top {len(top_stocks)}  機會 {len(opportunity_stocks)}\n"
        f"Watch {len(watchlist_stocks)}  雷達 {len(radar_stocks)}"
    )
    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#0F172A",
            "paddingAll": "18px",
            "spacing": "sm",
            "contents": [
                _flex_text("AI Stock Bot V2", size="xl", color="#FFFFFF", weight="bold"),
                _flex_text(f"{trade_date or '今日'}｜三層候選池完成", size="sm", color="#CBD5E1"),
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "18px",
            "spacing": "md",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "sm",
                    "contents": [
                        _flex_stat("可投資母體", f"{investable}檔"),
                        _flex_stat("深度分析", f"{deep}檔"),
                        _flex_stat("候選稽核", audit_label),
                    ],
                },
                {"type": "separator", "color": "#E2E8F0"},
                _flex_text(f"大盤｜{market_summary}", size="sm", color="#0F172A", weight="bold", max_lines=2),
                _flex_text(
                    f"候選｜新增 {added} / 退出 {removed} / 重複 {overlap}",
                    size="sm",
                    color="#334155",
                ),
                _flex_text(f"分層｜{count_summary}", size="sm", color="#334155"),
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#F8FAFC",
                    "cornerRadius": "6px",
                    "paddingAll": "10px",
                    "contents": [
                        _flex_text("法人資金產業", size="xs", color="#64748B", weight="bold"),
                        _flex_text(industry_summary, size="xs", color="#334155", max_lines=4),
                    ],
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "color": "#0F766E",
                    "action": {
                        "type": "uri",
                        "label": "開啟完整財經看板",
                        "uri": DAILY_FINANCE_REPORT_URL,
                    },
                }
            ],
        },
    }


def _flex_stock_bubble(bucket: str, row: StockRow) -> dict[str, Any]:
    style = FLEX_BUCKET_STYLES[bucket]
    grade = str(row.get("model_grade") or "D")
    score = (
        float(row.get("opportunity_score") or 0.0)
        if bucket == "opportunity"
        else float(row.get("accumulation_score") or 0.0)
        if bucket == "radar"
        else float(row.get("total_score") or 0.0)
    )
    score_label = "機會分" if bucket == "opportunity" else "雷達分" if bucket == "radar" else "總分"
    institutional_days = 10 if bucket in {"opportunity", "radar"} else 5
    institutional_net = (
        float(row.get(f"foreign_{institutional_days}d_sum") or 0.0)
        + float(row.get(f"trust_{institutional_days}d_sum") or 0.0)
    )
    theme = _readable_theme(row.get("industry_theme") or row.get("industry_category"))
    industry_grade = str(row.get("industry_capital_grade") or "")
    industry_label = f"{theme}｜產業資金 {industry_grade}" if industry_grade else theme
    rank = _flex_stock_rank(row, bucket)
    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": style["color"],
            "paddingAll": "16px",
            "spacing": "sm",
            "contents": [
                {
                    "type": "box",
                    "layout": "horizontal",
                    "alignItems": "center",
                    "contents": [
                        _flex_text(f"{rank}｜{row.get('stock_id')} {row.get('stock_name')}", size="lg", color="#FFFFFF", weight="bold", flex=1, max_lines=1),
                        {
                            "type": "box",
                            "layout": "vertical",
                            "backgroundColor": "#FFFFFF",
                            "cornerRadius": "4px",
                            "paddingAll": "5px",
                            "contents": [
                                _flex_text(
                                    f"{grade}級",
                                    size="xs",
                                    color=FLEX_GRADE_COLORS.get(grade, "#475569"),
                                    weight="bold",
                                    align="center",
                                    wrap=False,
                                )
                            ],
                        },
                    ],
                },
                _flex_text(f"{style['label']}｜{score_label} {score:.1f}", size="sm", color="#F8FAFC"),
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "spacing": "md",
            "contents": [
                _flex_text(industry_label, size="sm", color="#0F172A", weight="bold", max_lines=1),
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "sm",
                    "contents": [
                        _flex_stat("20日", f"{float(row.get('price_change_20d') or 0.0):+.1f}%"),
                        _flex_stat("量比", f"{float(row.get('volume_ratio') or 0.0):.2f}x"),
                        _flex_stat(f"法人{institutional_days}日", _flex_signed_lots(institutional_net)),
                    ],
                },
                {
                    "type": "box",
                    "layout": "horizontal",
                    "spacing": "md",
                    "contents": [
                        _flex_text(
                            f"營收年增 {float(row.get('yoy') or 0.0):+.1f}%",
                            size="xs",
                            color="#475569",
                            flex=1,
                        ),
                        _flex_text(
                            f"估值 {float(row.get('valuation_score') or 0.0):.1f}/10",
                            size="xs",
                            color="#475569",
                            flex=1,
                            align="end",
                        ),
                    ],
                },
                {"type": "separator", "color": "#E2E8F0"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": style["soft"],
                    "cornerRadius": "6px",
                    "paddingAll": "9px",
                    "spacing": "xs",
                    "contents": [
                        _flex_text("入選", size="xxs", color=style["color"], weight="bold"),
                        _flex_text(_flex_selection_reason(row, bucket), size="xs", color="#0F172A", max_lines=2),
                    ],
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "xs",
                    "contents": [
                        _flex_text("觀察風險", size="xxs", color="#B91C1C", weight="bold"),
                        _flex_text(_flex_risk_reason(row, bucket), size="xs", color="#475569", max_lines=2),
                    ],
                },
                _flex_text("資料觀察，不代表立即買進", size="xxs", color="#94A3B8", align="center"),
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "button",
                    "style": "link",
                    "height": "sm",
                    "color": style["color"],
                    "action": {
                        "type": "uri",
                        "label": "完整分析",
                        "uri": DAILY_FINANCE_REPORT_URL,
                    },
                }
            ],
        },
    }


def validate_line_flex_message(message: dict[str, Any]) -> tuple[int, int]:
    if message.get("type") != "flex":
        raise ValueError("LINE Flex message type must be 'flex'.")
    alt_text = str(message.get("altText") or "")
    if not alt_text or len(alt_text) > LINE_FLEX_ALT_TEXT_LIMIT:
        raise ValueError(f"LINE Flex altText length invalid: {len(alt_text)}")
    contents = message.get("contents") or {}
    bubbles = contents.get("contents") if contents.get("type") == "carousel" else [contents]
    bubble_count = len(bubbles or [])
    if not 1 <= bubble_count <= LINE_FLEX_CAROUSEL_MAX_BUBBLES:
        raise ValueError(f"LINE Flex bubble count invalid: {bubble_count}")
    payload_bytes = len(json.dumps(contents, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if payload_bytes > LINE_FLEX_CAROUSEL_MAX_BYTES:
        raise ValueError(f"LINE Flex carousel exceeds 50 KB: {payload_bytes} bytes")
    return bubble_count, payload_bytes


def build_line_flex_message(
    top_stocks: list[StockRow],
    opportunity_stocks: list[StockRow],
    watchlist_stocks: list[StockRow],
    radar_stocks: list[StockRow],
) -> dict[str, Any]:
    stock_rows = _select_flex_stock_rows(
        top_stocks,
        opportunity_stocks,
        watchlist_stocks,
        radar_stocks,
    )
    bubbles = [
        _flex_summary_bubble(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks),
        *[_flex_stock_bubble(bucket, row) for bucket, row in stock_rows],
    ]
    trade_date = format_trade_date(
        _status_value(DATA_SOURCE_STATUS.get("candidate_audit", ""), "trade_date") or ""
    )
    alt_text = (
        f"AI Stock Bot V2｜{trade_date or '今日'}｜"
        f"Top {len(top_stocks)}、機會 {len(opportunity_stocks)}、"
        f"Watch {len(watchlist_stocks)}、雷達 {len(radar_stocks)}"
    )
    message = {
        "type": "flex",
        "altText": alt_text[:LINE_FLEX_ALT_TEXT_LIMIT],
        "contents": {"type": "carousel", "contents": bubbles},
    }
    validate_line_flex_message(message)
    return message


def assert_three_layer_candidate_pipeline_ready() -> None:
    investable = int(_status_value(DATA_SOURCE_STATUS.get("investable_universe", ""), "investable") or 0)
    deep = int(_status_value(DATA_SOURCE_STATUS.get("preselection", ""), "deep") or 0)
    target = int(_status_value(DATA_SOURCE_STATUS.get("preselection", ""), "target") or 0)
    audit = DATA_SOURCE_STATUS.get("candidate_audit", "")
    trade_date = _status_value(audit, "trade_date") or ""
    warnings = set((_status_value(audit, "warnings") or "none").split(","))
    expected_deep = min(target, investable) if target > 0 else investable
    if investable <= 0:
        raise RuntimeError("Three-layer candidate pipeline has no investable universe.")
    if deep <= 0 or (expected_deep > 0 and deep < expected_deep):
        raise RuntimeError(
            f"Three-layer candidate pipeline incomplete: investable={investable} deep={deep} target={target}"
        )
    blocking_warning_names = {
        "pool_not_full",
        "common_trade_date_missing",
    }
    if not env_bool("AI_STOCK_ALLOW_INCOMPLETE_DATA_PUSH", False):
        blocking_warning_names.add("common_trade_date_not_advanced")
    blocking_warnings = blocking_warning_names & warnings
    if blocking_warnings:
        raise RuntimeError(
            "Three-layer candidate pipeline audit failed: " + ",".join(sorted(blocking_warnings))
        )
    if not trade_date or trade_date == "missing":
        raise RuntimeError("Three-layer candidate pipeline common trade date is missing.")
    print(
        "AI_STOCK_THREE_LAYER_READY "
        f"investable={investable} deep={deep} target={target} trade_date={trade_date}"
    )


def push_line_message(message: str) -> None:
    message = ensure_finance_reference(message)
    if env_bool("LINE_TEST_PUSH") and not message.startswith("[TEST]"):
        message = f"[TEST] {message}"
    payload_text = trim_line_text(message)
    line_message: dict[str, Any] = {"type": "text", "text": payload_text}

    channel_access_token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    line_to = os.getenv("LINE_TO")
    use_broadcast = env_bool("LINE_BROADCAST", False)
    if not channel_access_token:
        raise ValueError("Missing LINE_CHANNEL_ACCESS_TOKEN.")

    if use_broadcast:
        endpoint = LINE_BROADCAST_URL
        payload_obj = {"messages": [line_message]}
    else:
        if not line_to:
            raise ValueError("Missing LINE_TO. Use a LINE userId or groupId, or set LINE_BROADCAST=true.")
        endpoint = LINE_PUSH_URL
        payload_obj = {"to": line_to, "messages": [line_message]}

    print(
        "LINE_MESSAGE_CHECK "
        f"message_type={line_message.get('type')} "
        f"request_message_objects=1 "
        f"finance_url={DAILY_FINANCE_REPORT_URL in json.dumps(line_message, ensure_ascii=False)} "
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
    margin_fields = ("gross_margin", "operating_margin", "net_margin")
    margin_values = [row.get(field) for field in margin_fields]
    margin_data_available = any(
        value is not None and abs(float(value)) > 1e-9
        for value in margin_values
    )
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
        "macro_catalyst_score": row.get("macro_catalyst_score"),
        "macro_catalyst_note": row.get("macro_catalyst_note"),
        "macro_context": row.get("macro_context"),
        "industry_capital_score": row.get("industry_capital_score"),
        "industry_capital_grade": row.get("industry_capital_grade"),
        "industry_capital_reason": row.get("industry_capital_reason"),
        "industry_capital_ratio": row.get("industry_capital_ratio"),
        "industry": row.get("industry_category"),
        "theme": row.get("industry_theme"),
        "monthly_revenue_yoy": row.get("yoy"),
        "monthly_revenue_mom": row.get("mom"),
        "accumulated_revenue_yoy": row.get("acc_yoy"),
        "eps": row.get("eps"),
        "roe": row.get("roe"),
        "margin_data_status": "available" if margin_data_available else "missing",
        "gross_margin": row.get("gross_margin") if margin_data_available else None,
        "operating_margin": row.get("operating_margin") if margin_data_available else None,
        "net_margin": row.get("net_margin") if margin_data_available else None,
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
            "你是台股候選名單覆判助理，只能根據 candidates 內提供的結構化資料與 macro_context 判斷。",
            "Gemini 不可假裝已經即時上網搜尋新聞；若 macro_context 提到油價、煉油利差、荷姆茲或航運，只能視為需要驗證的題材假設。",
            "請交叉檢查基本面、法人籌碼、技術面、估值、風險與宏觀題材是否一致。",
            "請避免只因熱門題材就給高信心；若基本面或法人籌碼不支持，請明確標示風險。",
            "數值為 null 或 margin_data_status=missing 代表資料未提供，不得解讀成 0、虧損或負面風險。",
            "請評估：正向催化、負面風險、成長是否可持續、題材品質、信心分數與一句摘要。",
            "請用繁體中文，避免英文代碼式文字，摘要務必短而清楚。",
            "只回傳 JSON array，每筆包含 stock_id, catalyst, risk, sustainability, theme_quality, confidence, summary。",
            "confidence 使用 0-10 數字；summary 最多 45 個中文字。",
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
    risk = str(review.get("risk") or "").strip()
    risk_text = f"｜風險 {risk[:45]}" if risk else ""
    return f"{summary[:65]}{risk_text}｜信心 {confidence_text}"


def entry_float(entry: dict[str, Any], *names: str) -> float:
    for name in names:
        value = entry.get(name)
        if value not in (None, ""):
            return clean_number(value)
    return 0.0


def lots_text(value: float) -> str:
    return f"{shares_to_lots(value):,.0f}張"


def source_label(source: Any) -> str:
    labels = {
        "top": "正式Top",
        "opportunity": "機會股",
        "watchlist": "Watchlist",
        "radar": "法人建倉雷達",
    }
    return labels.get(str(source or ""), str(source or "候選"))


def review_grade(score: float) -> str:
    if score >= 75:
        return "覆判偏多"
    if score >= 60:
        return "續觀察"
    if score >= 45:
        return "中性等待"
    return "風險升高"


def review_action(score: float, risks: list[str]) -> str:
    if "今日重跌" in risks or "法人近2日偏賣" in risks:
        return "先降權重，等待賣壓收斂後再評估。"
    if score >= 75:
        return "可列優先觀察，仍等量縮回測或盤中承接確認。"
    if score >= 60:
        return "維持觀察，避免追高，留意法人是否延續買超。"
    if score >= 45:
        return "訊號尚未完整，等待基本面或籌碼再確認。"
    return "暫不列入積極名單。"


def summarize_free_chip_review(entry: dict[str, Any]) -> dict[str, Any]:
    foreign_5d = entry_float(entry, "foreign_5d_sum")
    trust_5d = entry_float(entry, "trust_5d_sum")
    foreign_10d = entry_float(entry, "foreign_10d_sum")
    trust_10d = entry_float(entry, "trust_10d_sum")
    latest_foreign = entry_float(entry, "latest_foreign_net")
    latest_trust = entry_float(entry, "latest_trust_net")
    latest_inst = entry_float(entry, "latest_institutional_net")
    recent_2d = entry_float(entry, "recent_2d_institutional_net")
    price_1d = entry_float(entry, "entry_price_change_1d", "price_change_1d")
    price_20d = entry_float(entry, "entry_price_change_20d", "price_change_20d")
    volume_ratio = entry_float(entry, "entry_volume_ratio", "volume_ratio")
    total_score = entry_float(entry, "total_score")
    fundamental_score = entry_float(entry, "fundamental_score")
    valuation_score = entry_float(entry, "valuation_score")
    eps_acceleration_score = entry_float(entry, "eps_acceleration_score")

    score = 50.0
    positives: list[str] = []
    risks: list[str] = []

    if trust_5d > 0:
        score += 12
        positives.append("投信5日買超")
    if trust_10d > 0:
        score += 8
        positives.append("投信10日偏買")
    if latest_trust > 0:
        score += 5
        positives.append("投信今日買超")
    elif latest_trust < 0:
        score -= 8
        risks.append("投信今日賣超")

    if foreign_5d > 0:
        score += 10
        positives.append("外資5日買超")
    if foreign_10d > 0:
        score += 7
        positives.append("外資10日偏買")
    if latest_foreign > 0:
        score += 5
        positives.append("外資今日買超")
    elif latest_foreign < 0:
        score -= 8
        risks.append("外資今日賣超")

    if latest_inst < 0 and recent_2d < 0:
        score -= 15
        risks.append("法人近2日偏賣")
    if price_1d <= -6:
        score -= 18
        risks.append("今日重跌")
    elif price_1d <= -4:
        score -= 10
        risks.append("單日跌幅偏重")
    if price_20d >= 30:
        score -= 8
        risks.append("20日漲幅偏高")
    if volume_ratio >= 2.5:
        score -= 6
        risks.append("量能過熱")
    elif 0.8 <= volume_ratio <= 1.8:
        score += 5
        positives.append("量能溫和")

    risk_notes = entry.get("risk_notes")
    if isinstance(risk_notes, list):
        if "margin_risk" in risk_notes:
            score -= 8
            risks.append("融資風險")
        if "low_liquidity" in risk_notes:
            score -= 8
            risks.append("流動性不足")
        if "heavy_1d_drop" in risk_notes and "單日跌幅偏重" not in risks and "今日重跌" not in risks:
            score -= 8
            risks.append("單日跌幅偏重")

    if entry.get("top_quality_pass") is True:
        score += 8
        positives.append("通過品質門檻")
    elif entry.get("top_quality_pass") is False:
        score -= 8
        reason = str(entry.get("top_quality_reason") or "").replace(",", "、")
        risks.append(f"品質門檻未過{f'：{reason}' if reason else ''}")

    if fundamental_score >= 30:
        score += 5
        positives.append("基本面分數高")
    if valuation_score <= 5:
        risks.append("估值訊號偏保守")
    if eps_acceleration_score > 0:
        positives.append("EPS加速加分")
    macro_catalyst_score = entry_float(entry, "macro_catalyst_score")
    if macro_catalyst_score >= 3:
        score += 4
        positives.append("宏觀題材與籌碼/價量有共振")

    score = round(clip(score, 0, 100), 1)
    return {
        "stock_id": str(entry.get("stock_id") or ""),
        "stock_name": str(entry.get("stock_name") or ""),
        "source": entry.get("source"),
        "rank": entry.get("rank"),
        "grade": str(entry.get("model_grade") or "?"),
        "total_score": total_score,
        "review_score": score,
        "review_grade": review_grade(score),
        "industry": _readable_theme(entry.get("industry_theme") or entry.get("industry_category")),
        "foreign_5d": foreign_5d,
        "trust_5d": trust_5d,
        "foreign_10d": foreign_10d,
        "trust_10d": trust_10d,
        "latest_inst": latest_inst,
        "recent_2d": recent_2d,
        "price_1d": price_1d,
        "price_20d": price_20d,
        "volume_ratio": volume_ratio,
        "positives": positives[:5],
        "risks": risks[:5],
        "action": review_action(score, risks),
        "entry": entry,
    }


def compact_entry_for_gemini(item: dict[str, Any]) -> dict[str, Any]:
    entry = item.get("entry") if isinstance(item.get("entry"), dict) else {}
    return {
        "bucket": source_label(item.get("source")),
        "stock_id": item.get("stock_id"),
        "stock_name": item.get("stock_name"),
        "grade": item.get("grade"),
        "total_score": item.get("total_score"),
        "review_score": item.get("review_score"),
        "review_grade": item.get("review_grade"),
        "industry": item.get("industry"),
        "fundamental_score": entry.get("fundamental_score"),
        "technical_score": entry.get("technical_score"),
        "chip_score": entry.get("chip_score"),
        "valuation_score": entry.get("valuation_score"),
        "industry_score": entry.get("industry_score"),
        "eps_acceleration_score": entry.get("eps_acceleration_score"),
        "macro_catalyst_score": entry.get("macro_catalyst_score"),
        "macro_catalyst_note": entry.get("macro_catalyst_note"),
        "macro_context": entry.get("macro_context"),
        "monthly_revenue_yoy": entry.get("yoy"),
        "monthly_revenue_mom": entry.get("mom"),
        "accumulated_revenue_yoy": entry.get("acc_yoy"),
        "eps": entry.get("eps"),
        "roe": entry.get("roe"),
        "gross_margin": entry.get("gross_margin"),
        "operating_margin": entry.get("operating_margin"),
        "net_margin": entry.get("net_margin"),
        "pe_ratio": entry.get("pe_ratio"),
        "pbr": entry.get("pbr"),
        "price_change_1d": item.get("price_1d"),
        "price_change_20d": item.get("price_20d"),
        "volume_ratio": item.get("volume_ratio"),
        "foreign_5d_lots": shares_to_lots(float(item.get("foreign_5d") or 0.0)),
        "trust_5d_lots": shares_to_lots(float(item.get("trust_5d") or 0.0)),
        "foreign_10d_lots": shares_to_lots(float(item.get("foreign_10d") or 0.0)),
        "trust_10d_lots": shares_to_lots(float(item.get("trust_10d") or 0.0)),
        "latest_institutional_lots": shares_to_lots(float(item.get("latest_inst") or 0.0)),
        "recent_2d_institutional_lots": shares_to_lots(float(item.get("recent_2d") or 0.0)),
        "positive_factors": item.get("positives"),
        "risk_factors": item.get("risks"),
        "opportunity_reason": entry.get("opportunity_reason"),
        "top_quality_reason": entry.get("top_quality_reason"),
        "risk_notes": entry.get("risk_notes"),
    }


def attach_gemini_reviews_to_review_items(items: list[dict[str, Any]]) -> None:
    limit = env_int("AI_STOCK_GEMINI_REVIEW_LIMIT", 6)
    if limit <= 0:
        DATA_SOURCE_STATUS["gemini_review"] = "disabled limit=0"
        return
    candidates = [compact_entry_for_gemini(item) for item in items[:limit]]
    if not candidates:
        DATA_SOURCE_STATUS["gemini_review"] = "empty"
        return
    try:
        reviews = request_gemini_reviews(candidates)
    except Exception as error:
        DATA_SOURCE_STATUS["gemini_review"] = f"failed {type(error).__name__}"
        print(f"GEMINI_REVIEW_FAILED {type(error).__name__}: {error}")
        return
    by_stock = {str(item.get("stock_id") or ""): item for item in reviews}
    attached = 0
    for item in items:
        review = by_stock.get(str(item.get("stock_id") or ""))
        if not review:
            continue
        summary = str(review.get("summary") or "").strip()
        if not summary:
            continue
        item["ai_review"] = {
            "summary": summary[:90],
            "risk": str(review.get("risk") or "").strip()[:80],
            "confidence": review.get("confidence"),
        }
        attached += 1
    DATA_SOURCE_STATUS["gemini_review"] = f"enabled reviewed={len(reviews)} attached={attached}"


def today_strategy_entries() -> list[dict[str, Any]]:
    tracker = read_cache("strategy_tracker")
    history = tracker.get("history", []) if isinstance(tracker, dict) else []
    today = datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d")
    for day_record in reversed(history):
        if str(day_record.get("date") or "") == today:
            entries = day_record.get("entries", [])
            return [entry for entry in entries if isinstance(entry, dict)]
    return []


def build_branch_report_message() -> str:
    entries = today_strategy_entries()
    if not entries:
        return (
            "AI Stock Bot V2 AI覆判與籌碼風險檢查\n"
            f"完整財經看板：{DAILY_FINANCE_REPORT_URL}\n\n"
            "====================\n"
            "尚未找到今日 20:00 主推播候選名單，暫不進行第二層覆判。\n"
            "請先確認第一則主推播 workflow 是否已完成並寫入候選追蹤快取。"
        )

    limit = env_int("AI_STOCK_BRANCH_REPORT_LIMIT", 8)
    source_order = {"top": 0, "opportunity": 1, "radar": 2, "watchlist": 3}
    ranked_entries = sorted(
        entries,
        key=lambda item: (
            source_order.get(str(item.get("source") or ""), 9),
            -entry_float(item, "total_score", "opportunity_score", "accumulation_score"),
        ),
    )
    review_items = [summarize_free_chip_review(entry) for entry in ranked_entries[: max(limit, 1)]]
    review_items.sort(key=lambda item: float(item.get("review_score") or 0.0), reverse=True)
    attach_gemini_reviews_to_review_items(review_items)

    lines = [
        "AI Stock Bot V2 AI覆判與籌碼風險檢查",
        f"完整財經看板：{DAILY_FINANCE_REPORT_URL}",
        "",
        "說明：針對20:00主推播候選名單，使用既有TWSE/TPEx法人、價量、基本面、估值與風險欄位做第二層覆判；目前不使用FinMind分點資料，也不改主模型分數。",
        "====================",
        f"覆判名單：{len(review_items)} 檔｜Gemini：{DATA_SOURCE_STATUS.get('gemini_review', 'unknown')}",
    ]

    for index, item in enumerate(review_items[:limit], start=1):
        positives = "、".join(item.get("positives") or []) or "尚無明顯加分訊號"
        risks = "、".join(item.get("risks") or []) or "未見主要風險"
        ai_review = item.get("ai_review") if isinstance(item.get("ai_review"), dict) else {}
        ai_summary = str(ai_review.get("summary") or "").strip()
        confidence = ai_review.get("confidence")
        try:
            confidence_text = f"{float(confidence):.0f}/10"
        except (TypeError, ValueError):
            confidence_text = "未啟用"
        lines.extend(
            [
                f"{index}. {item['stock_id']} {item['stock_name']}｜覆判 {item['review_score']:.1f}｜{item['review_grade']}",
                f"{source_label(item.get('source'))}｜{item['grade']}級｜總分 {item['total_score']:.1f}｜{item['industry']}",
                f"籌碼：外資5日 {lots_text(item['foreign_5d'])} / 投信5日 {lots_text(item['trust_5d'])}｜今日法人 {lots_text(item['latest_inst'])}｜近2日 {lots_text(item['recent_2d'])}",
                f"價量：1日 {item['price_1d']:.1f}%｜20日 {item['price_20d']:.1f}%｜量比 {item['volume_ratio']:.2f}x",
                f"加分：{positives}",
                f"風險：{risks}",
                f"AI摘要：{ai_summary if ai_summary else 'Gemini未回傳摘要'}｜信心 {confidence_text}",
                f"結論：{item['action']}",
            ]
        )
    return "\n".join(lines)

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
            "yoy": row.get("yoy"),
            "mom": row.get("mom"),
            "acc_yoy": row.get("acc_yoy"),
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
            "macro_catalyst_score": row.get("macro_catalyst_score"),
            "macro_catalyst_note": row.get("macro_catalyst_note"),
            "macro_context": row.get("macro_context"),
            "industry_capital_score": row.get("industry_capital_score"),
            "industry_capital_grade": row.get("industry_capital_grade"),
            "industry_capital_reason": row.get("industry_capital_reason"),
            "industry_capital_ratio": row.get("industry_capital_ratio"),
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
            "gross_margin": row.get("gross_margin"),
            "operating_margin": row.get("operating_margin"),
            "net_margin": row.get("net_margin"),
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
    audit_status = DATA_SOURCE_STATUS.get("candidate_audit", "unknown")
    print(
        "AI_STOCK_RUN_METRICS "
        f"phase={phase} "
        f"started_at={started_at.isoformat()} "
        f"finished_at={finished_at.isoformat()} "
        f"elapsed_seconds={elapsed:.1f} "
        f"candidate_pool={DATA_SOURCE_STATUS.get('candidate_pool', 'unknown')} "
        f"audit_status={_status_value(audit_status, 'status') or 'unknown'} "
        f"audit_hash={_status_value(audit_status, 'hash') or 'unknown'} "
        f"audit_trade_date={_status_value(audit_status, 'trade_date') or 'unknown'} "
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
    parser.add_argument("--branch-report", action="store_true", help="Push an AI review and institutional-risk follow-up for today's candidates.")
    args = parser.parse_args()

    started_at = datetime.now(TAIPEI_TZ)
    started_perf = time.perf_counter()
    push_enabled = args.push_line or env_bool("ENABLE_LINE_PUSH")
    install_run_deadline()
    if args.branch_report:
        message = build_branch_report_message()
        print(message)
        counts = {"top": 0, "opportunity": 0, "watchlist": 0, "radar": 0, "exit": 0}
        print_run_metrics("branch_report_complete", started_at, started_perf, counts)
        empty_review = "尚未找到今日 20:00 主推播候選名單" in message
        if push_enabled and not (empty_review and env_bool("AI_STOCK_SUPPRESS_EMPTY_REVIEW_PUSH", False)):
            push_line_message(message)
        elif push_enabled and empty_review:
            print("AI_REVIEW_EMPTY_SUPPRESSED no LINE push; waiting for a later retry slot.")
        print_run_metrics("finished", started_at, started_perf, counts)
        return

    try:
        if push_enabled:
            assert_market_close_ready()
        top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks, exit_alerts = build_v2_selection()
        if push_enabled:
            assert_data_completeness_ready()
            assert_three_layer_candidate_pipeline_ready()
        write_strategy_tracker(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks, exit_alerts)
        attach_gemini_reviews(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks)
        message = build_line_message(top_stocks, opportunity_stocks, watchlist_stocks, radar_stocks, exit_alerts)
    except Exception as error:
        if not push_enabled:
            raise
        if isinstance(error, RunDeadlineExceeded):
            raise
        if is_pre_close_error(error):
            raise
        if env_bool("AI_STOCK_FAIL_ON_ANALYSIS_ERROR", False):
            print(
                "AI_STOCK_ANALYSIS_FAILED_RETRYABLE "
                f"error={type(error).__name__}:{readable_data_error(error)}"
            )
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
