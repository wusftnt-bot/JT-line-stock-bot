import json
import os
import unittest
from unittest.mock import patch

from scripts import ai_stock_bot as bot


def stock(stock_id: str, bucket: str, rank: int) -> dict:
    row = {
        "stock_id": stock_id,
        "stock_name": f"測試{stock_id}",
        "model_grade": "B",
        "total_score": 78.5,
        "opportunity_score": 82.0,
        "accumulation_score": 80.0,
        "industry_theme": "electronics",
        "industry_capital_grade": "A",
        "price_change_20d": 8.5,
        "volume_ratio": 1.15,
        "foreign_5d_sum": 3_000_000,
        "trust_5d_sum": 1_000_000,
        "foreign_10d_sum": 5_000_000,
        "trust_10d_sum": 2_000_000,
        "latest_foreign_net": 500_000,
        "latest_trust_net": 100_000,
        "yoy": 25.0,
        "valuation_score": 8.0,
        "fundamental_score": 30.0,
        "opportunity_reason": "外資10日買超,法人買超天數穩定",
        "top_quality_reason": "weak_volume_ratio",
        "risk_notes": [],
        "event_risk_reason": "",
        "quiet_accumulation": True,
    }
    rank_key = {
        "top": "rank",
        "opportunity": "opportunity_rank",
        "watchlist": "watch_rank",
        "radar": "radar_rank",
    }[bucket]
    row[rank_key] = rank
    return row


class DummyResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class LineFlexTests(unittest.TestCase):
    def setUp(self):
        self.original_status = bot.DATA_SOURCE_STATUS.copy()
        bot.DATA_SOURCE_STATUS.update(
            {
                "investable_universe": "source=1965 investable=1200",
                "preselection": "universe=1200 deep=220 target=220",
                "candidate_audit": (
                    "status=normal added=40 removed=40 overlap=81.8% "
                    "hash=abc123 trade_date=20260723 identical=1 stale=1 warnings=none"
                ),
                "market_score": "score=75 regime=constructive notes=proxy_above_ma20",
                "industry_capital": "electronics:A72.0/8.1%/12 | shipping_logistics:B63.0/4.2%/5",
            }
        )

    def tearDown(self):
        bot.DATA_SOURCE_STATUS.clear()
        bot.DATA_SOURCE_STATUS.update(self.original_status)

    def build_rows(self):
        top = [stock(f"10{i:02d}", "top", i) for i in range(1, 7)]
        opportunity = [stock(f"20{i:02d}", "opportunity", i) for i in range(1, 4)]
        watchlist = [stock(f"30{i:02d}", "watchlist", i) for i in range(1, 3)]
        radar = [stock(f"40{i:02d}", "radar", i) for i in range(1, 3)]
        return top, opportunity, watchlist, radar

    def test_flex_has_summary_and_nine_stock_cards(self):
        message = bot.build_line_flex_message(*self.build_rows())
        bubble_count, payload_bytes = bot.validate_line_flex_message(message)

        self.assertEqual(bubble_count, 10)
        self.assertLess(payload_bytes, bot.LINE_FLEX_CAROUSEL_MAX_BYTES)
        self.assertIn(bot.DAILY_FINANCE_REPORT_URL, json.dumps(message, ensure_ascii=False))

    def test_flex_card_mix_preserves_all_buckets(self):
        selected = bot._select_flex_stock_rows(*self.build_rows())
        bucket_counts = {bucket: sum(1 for item, _ in selected if item == bucket) for bucket in bot.FLEX_BUCKET_STYLES}

        self.assertEqual(len(selected), 9)
        self.assertEqual(bucket_counts, {"top": 5, "opportunity": 2, "watchlist": 1, "radar": 1})

    def test_three_layer_pipeline_accepts_complete_daily_pool(self):
        bot.assert_three_layer_candidate_pipeline_ready()

    def test_three_layer_pipeline_rejects_short_pool(self):
        bot.DATA_SOURCE_STATUS["preselection"] = "universe=1200 deep=219 target=220"
        with self.assertRaisesRegex(RuntimeError, "pipeline incomplete"):
            bot.assert_three_layer_candidate_pipeline_ready()

    def test_push_uses_one_message_object_for_full_carousel(self):
        flex_message = bot.build_line_flex_message(*self.build_rows())
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
                    "LINE_TEST_PUSH": "false",
                },
                clear=False,
            ),
            patch.object(bot, "urlopen", side_effect=fake_urlopen),
        ):
            bot.push_line_message("文字備援", flex_message)

        self.assertEqual(captured["timeout"], 60)
        self.assertEqual(len(captured["payload"]["messages"]), 1)
        self.assertEqual(captured["payload"]["messages"][0]["type"], "flex")
        self.assertEqual(
            len(captured["payload"]["messages"][0]["contents"]["contents"]),
            10,
        )


if __name__ == "__main__":
    unittest.main()
