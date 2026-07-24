import json
import os
import unittest
from unittest.mock import patch

from scripts import ai_stock_bot as bot


def stock(stock_id: str, bucket: str, rank: int, *, latest_institutional: float = 600_000) -> dict:
    row = {
        "stock_id": stock_id,
        "stock_name": f"測試公司{stock_id}",
        "model_grade": "B",
        "total_score": 78.5,
        "fundamental_score": 30.0,
        "chip_score": 18.0,
        "technical_score": 14.0,
        "valuation_score": 8.0,
        "industry_score": 8.5,
        "eps_acceleration_score": 1.0,
        "risk_penalty": 1.0,
        "opportunity_score": 82.0,
        "accumulation_score": 80.0,
        "industry_theme": "electronics",
        "candidate_sources": ["fundamental_growth", "early_institutional"],
        "price_change_1d": 1.2,
        "price_change_20d": 8.5,
        "volume_ratio": 1.15,
        "volume_20d_avg": 2_000_000,
        "latest_volume": 1_500_000,
        "turnover_20d_avg": 200_000_000,
        "close": 100.0,
        "foreign_5d_sum": 3_000_000,
        "trust_5d_sum": 1_000_000,
        "foreign_10d_sum": 5_000_000,
        "trust_10d_sum": 2_000_000,
        "latest_foreign_net": latest_institutional,
        "latest_trust_net": 0,
        "latest_dealer_net": 0,
        "latest_institutional_net": latest_institutional,
        "recent_2d_institutional_net": 1_000_000,
        "yoy": 25.0,
        "acc_yoy": 20.0,
        "gross_margin": 30.0,
        "operating_margin": 15.0,
        "net_margin": 12.0,
        "pe_ratio": 18.0,
        "pbr": 2.0,
        "roe": 15.0,
        "fundamental_floor_pass": True,
        "entry_timing_pass": True,
        "break_120d_high": False,
        "triple_margin_up": False,
        "opportunity_reason": "法人10日買超,基本面改善,股價未過熱",
        "top_quality_reason": "",
        "risk_notes": [],
        "event_risk_reason": "",
        "quiet_accumulation": True,
        "ai_review": {
            "summary": "營收與法人方向同步改善，成長仍具延續性",
            "risk": "短線量能仍需確認",
            "confidence": 8,
        },
    }
    rank_key = {
        "top": "rank",
        "opportunity": "opportunity_rank",
        "watchlist": "watch_rank",
        "radar": "radar_rank",
        "exit": "exit_alert_rank",
    }[bucket]
    row[rank_key] = rank
    if bucket == "watchlist":
        row["top_quality_reason"] = "latest_institutional_selling"
    if bucket == "exit":
        row["exit_alert_reasons"] = "below_ma20,institutional_2d_selling"
        row["exit_source"] = "top"
    return row


class DummyResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class LineTextTests(unittest.TestCase):
    def setUp(self):
        self.original_status = bot.DATA_SOURCE_STATUS.copy()
        bot.DATA_SOURCE_STATUS.update(
            {
                "investable_universe": "source=1965 investable=1200",
                "preselection": "universe=1200 deep=220 target=220",
                "candidate_pool": "growth=80 turn=40 institutional=50 industry=30 non_mainstream=20 merged=220",
                "candidate_audit": (
                    "status=normal added=40 removed=40 overlap=81.8% "
                    "hash=abc123 trade_date=20260723 identical=1 stale=1 warnings=none"
                ),
                "market_score": "score=75 regime=constructive notes=proxy_above_ma20",
                "industry_capital": "electronics:A72.0/8.1%/12 | shipping_logistics:B63.0/4.2%/5",
                "industry_momentum": "electronics:8.0%/1.2x/5capital",
            }
        )

    def tearDown(self):
        bot.DATA_SOURCE_STATUS.clear()
        bot.DATA_SOURCE_STATUS.update(self.original_status)

    def test_formal_top_rejects_latest_institutional_selling(self):
        row = stock("2330", "top", 1, latest_institutional=-1)

        passed, reason = bot.evaluate_top_liquidity_quality(row, include_model_score=False)

        self.assertFalse(passed)
        self.assertIn("latest_institutional_selling", reason)

    def test_text_message_restores_all_sections_and_stays_under_limit(self):
        top = [stock(f"10{i:02d}", "top", i) for i in range(1, 4)]
        opportunity = [stock(f"20{i:02d}", "opportunity", i) for i in range(1, 4)]
        watchlist = [stock(f"30{i:02d}", "watchlist", i, latest_institutional=-100_000) for i in range(1, 4)]
        radar = [stock(f"40{i:02d}", "radar", i) for i in range(1, 4)]
        exits = [stock(f"50{i:02d}", "exit", i, latest_institutional=-100_000) for i in range(1, 4)]

        message = bot.build_line_message(top, opportunity, watchlist, radar, exits)

        for heading in (
            "AI質化摘要（3檔）",
            "正式 Top（3檔）",
            "機會股（3檔）",
            "Watchlist（3檔）",
            "法人建倉雷達（3檔）",
            "Exit Alert 持股風險追蹤（3檔）",
        ):
            self.assertIn(heading, message)
        self.assertIn("測試公司1001", message)
        self.assertIn("分數｜基30.0 籌18.0 技14.0 估8.0 產8.5 EPS+1.0 風險-1.0", message)
        self.assertIn("法人當日轉賣", message)
        self.assertLessEqual(len(message), bot.LINE_TEXT_LIMIT)

    def test_push_uses_one_text_message_object(self):
        captured = {}

        def fake_urlopen(request, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return DummyResponse()

        with (
            patch.dict(
                os.environ,
                {
                    "LINE_CHANNEL_ACCESS_TOKEN": "test-token",
                    "LINE_TO": "U-test",
                    "LINE_BROADCAST": "false",
                    "LINE_TEST_PUSH": "true",
                },
                clear=False,
            ),
            patch.object(bot, "urlopen", side_effect=fake_urlopen),
        ):
            bot.push_line_message("文字測試")

        self.assertEqual(captured["timeout"], 60)
        self.assertEqual(len(captured["payload"]["messages"]), 1)
        self.assertEqual(captured["payload"]["messages"][0]["type"], "text")
        self.assertTrue(captured["payload"]["messages"][0]["text"].startswith("[TEST]"))

    def test_manual_latest_data_test_allows_stale_common_trade_date(self):
        bot.DATA_SOURCE_STATUS.update(
            {
                "investable_universe": "investable=922",
                "preselection": "deep=220 target=220",
                "candidate_audit": (
                    "status=warning added=220 removed=0 overlap=0.0% "
                    "hash=abc trade_date=20260723 identical=0 stale=2 "
                    "warnings=common_trade_date_not_advanced"
                ),
            }
        )

        with patch.dict(os.environ, {"AI_STOCK_ALLOW_INCOMPLETE_DATA_PUSH": "true"}, clear=False):
            bot.assert_three_layer_candidate_pipeline_ready()

    def test_gemini_payload_marks_missing_margins_as_missing_not_zero(self):
        row = stock("2330", "top", 1)
        row.update({"gross_margin": 0.0, "operating_margin": 0.0, "net_margin": 0.0})

        payload = bot.compact_for_gemini(row, "top")

        self.assertEqual(payload["margin_data_status"], "missing")
        self.assertIsNone(payload["gross_margin"])
        self.assertIsNone(payload["operating_margin"])
        self.assertIsNone(payload["net_margin"])


if __name__ == "__main__":
    unittest.main()
