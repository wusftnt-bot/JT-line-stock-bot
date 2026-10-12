"""Persist public MOPS facts independently of evictable Actions caches."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

FACT_FIELDS = {
    "financial_period", "eps", "net_income", "owner_net_income", "equity", "parent_equity",
    "assets", "liabilities", "debt_ratio", "debt_ratio_applicable", "book_value_per_share",
    "financial_basis", "balance_period_aligned", "financial_metrics_version",
}
JOURNAL_PATH = "data/financial_period_history.json"


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and urlsplit(newurl).hostname != "api.github.com":
            redirected.remove_header("Authorization")
        return redirected


def public_facts(ledger: dict) -> dict:
    stocks = {}
    for sid, periods in ledger.get("stocks", {}).items():
        if not str(sid).isdigit() or len(str(sid)) != 4 or not isinstance(periods, dict):
            continue
        stocks[sid] = {period: {key: value for key, value in record.items() if key in FACT_FIELDS}
                       for period, record in periods.items() if isinstance(record, dict)}
    return {"schema": "mops_period_history_v2", "stocks": stocks}


def sync_financial_journal(ledger: dict | None = None) -> dict:
    repo = os.getenv("GITHUB_REPOSITORY", "")
    token = os.getenv("FINANCIAL_JOURNAL_TOKEN", "")
    if not repo or not token:
        return {}
    opener = build_opener(SafeRedirect())
    url = f"https://api.github.com/repos/{repo}/contents/{JOURNAL_PATH}"

    def api(method, data=None, raw=False):
        req = Request(url, method=method, headers={
            "Authorization": f"Bearer {token}", "User-Agent": "JT-line-stock-bot/financial-journal",
            "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
            "Content-Type": "application/json",
        }, data=json.dumps(data).encode() if data is not None else None)
        with opener.open(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        try:
            stored = api("GET", raw=True)
        except HTTPError as error:
            if error.code != 404:
                raise
            stored = {}
        if ledger is None:
            print(f"FINANCIAL_JOURNAL restored_stocks={len(stored.get('stocks', {}))}")
            return stored
        facts = public_facts(ledger)
        content = json.dumps(facts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(content.encode()).hexdigest()
        if stored.get("facts_hash") == digest:
            print("FINANCIAL_JOURNAL unchanged")
            return stored
        facts["facts_hash"] = digest
        body = {
            "message": "Update public MOPS financial period journal", "branch": "main",
            "content": base64.b64encode(json.dumps(facts, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).decode(),
        }
        if stored:
            body["sha"] = api("GET")["sha"]
        api("PUT", body)
        print(f"FINANCIAL_JOURNAL saved_stocks={len(facts['stocks'])}")
        return facts
    except Exception as error:
        print(f"FINANCIAL_JOURNAL unavailable={type(error).__name__}")
        return {}
