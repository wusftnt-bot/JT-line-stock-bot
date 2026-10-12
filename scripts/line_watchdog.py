"""Check delivery evidence, not a workflow's green badge."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen

try:
    from scripts.market_calendar import is_trading_day
except ModuleNotFoundError:
    from market_calendar import is_trading_day

TAIPEI = timezone(timedelta(hours=8))


def has_delivery_evidence(jobs: list[dict], report: str) -> bool:
    push = "Run AI stock bot and push LINE message" if report == "main" else "Run AI review and push LINE message"
    marker = "Create sent marker" if report == "main" else "Create AI review sent marker"
    return any(
        job.get("conclusion") == "success"
        and {push, marker}.issubset({s["name"] for s in job.get("steps", []) if s.get("conclusion") == "success"})
        for job in jobs
    )


def main() -> None:
    now = datetime.now(TAIPEI)
    if not is_trading_day(now.date()):
        print("WATCHDOG non_trading_day")
        return
    hm = now.hour * 100 + now.minute
    if not 2010 <= hm <= 2200:
        print("WATCHDOG outside evening recovery window")
        return
    repo = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GH_TOKEN"]

    def api(method: str, path: str, body=None):
        req = Request(
            f"https://api.github.com/repos/{repo}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "Content-Type": "application/json", "User-Agent": "JT-line-stock-bot/watchdog"},
            data=json.dumps(body).encode() if body is not None else (b"" if method != "GET" else None),
            method=method,
        )
        with urlopen(req, timeout=45) as response:
            data = response.read()
            return json.loads(data) if data else {}

    start = now.replace(hour=19, minute=30, second=0, microsecond=0)
    for filename, report, cutoff in (
        ("daily-stock-line-bot.yml", "main", 2010),
        ("line-stock-branch-report.yml", "ai_review", 2115),
    ):
        if hm < cutoff:
            continue
        workflow = api("GET", f"/actions/workflows/{filename}")
        if workflow.get("state") != "active":
            api("PUT", f"/actions/workflows/{filename}/enable")
            print(f"WATCHDOG reenabled report={report}")
        runs = api("GET", f"/actions/workflows/{filename}/runs?per_page=30").get("workflow_runs", [])
        today_runs = [r for r in runs if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) >= start]
        delivered = False
        active = False
        for run in today_runs:
            if run.get("status") in {"queued", "in_progress"}:
                active = True
            if run.get("conclusion") == "success":
                jobs = api("GET", f"/actions/runs/{run['id']}/jobs").get("jobs", [])
                if has_delivery_evidence(jobs, report):
                    delivered = True
                    break
        if delivered or active:
            print(f"WATCHDOG report={report} delivered={delivered} active={active}")
            continue
        if len([r for r in today_runs if r.get("event") == "workflow_dispatch"]) >= 3:
            raise RuntimeError(f"WATCHDOG delivery unresolved report={report}; inspect job logs and external scheduler alerts")
        inputs = {"mark_sent": "true", "line_test_push": "false", "allow_latest_available_data": "false"}
        if report == "main":
            inputs["force_fresh_push"] = "false"
        api("POST", f"/actions/workflows/{filename}/dispatches", {"ref": "main", "inputs": inputs})
        print(f"WATCHDOG dispatched recovery report={report}")


if __name__ == "__main__":
    main()
