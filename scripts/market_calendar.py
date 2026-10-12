"""TWSE session calendar. Missing calendars fail visibly rather than guessing weekdays."""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

TAIPEI = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]


def parse_calendar(payload: dict, year: int) -> set[str]:
    if payload.get("stat", "").lower() != "ok" or int(payload.get("queryYear", 0)) != year:
        raise ValueError("TWSE calendar year/schema mismatch")
    rows = payload.get("data", [])
    if len(rows) < 10:
        raise ValueError("TWSE calendar is incomplete")
    closed = set()
    for row in rows:
        day = date.fromisoformat(str(row[0]))
        if day.year != year:
            raise ValueError("TWSE calendar date/year mismatch")
        name = str(row[1])
        # First/last trading day rows are informational, not closures.
        if "交易日" in name and "無交易" not in name:
            continue
        closed.add(day.isoformat())
    return closed


@lru_cache(maxsize=4)
def closed_dates(year: int) -> set[str]:
    snapshot = ROOT / "data" / f"market_calendar_{year}.json"
    if snapshot.exists():
        data = json.loads(snapshot.read_text(encoding="utf-8"))
        if data.get("year") != year:
            raise ValueError("Calendar snapshot year mismatch")
        return set(data["closed_dates"])
    url = "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?" + urlencode(
        {"response": "json", "queryYear": year}
    )
    request = Request(url, headers={"User-Agent": "JT-line-stock-bot/market-calendar"})
    with urlopen(request, timeout=30) as response:
        return parse_calendar(json.loads(response.read().decode("utf-8")), year)


def is_trading_day(day: date) -> bool:
    additional = set(os.getenv("AI_STOCK_EXTRA_CLOSED_DATES", "").split(","))
    return day.weekday() < 5 and day.isoformat() not in closed_dates(day.year) | additional


def expected_trade_date(day: date) -> str:
    for offset in range(30):
        candidate = day - timedelta(days=offset)
        if is_trading_day(candidate):
            return candidate.strftime("%Y%m%d")
    raise RuntimeError("No trading session found in the past 30 days")


if __name__ == "__main__":
    today = datetime.now(TAIPEI).date()
    values = {
        "is_trading_day": str(is_trading_day(today)).lower(),
        "expected_trade_date": expected_trade_date(today),
    }
    for key, value in values.items():
        print(f"{key}={value}")
    if os.getenv("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
            for key, value in values.items():
                output.write(f"{key}={value}\n")
