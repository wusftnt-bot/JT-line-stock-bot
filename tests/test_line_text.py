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
        "margin_data_status": "available",
        "profitability_data_complete": True,
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
    status = 200
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class GeminiResponse(DummyResponse):
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


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
            "正式 Top（顯示3檔）",
            "機會股（顯示3檔）",
            "Watchlist（顯示3檔）",
            "法人建倉雷達（顯示3檔）",
            "Exit Alert 持股風險追蹤（顯示3檔）",
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
            patch.object(bot, "write_run_result"),
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
        row.update(
            {
                "gross_margin": 0.0,
                "operating_margin": 0.0,
                "net_margin": 0.0,
                "margin_data_status": "missing",
            }
        )

        payload = bot.compact_for_gemini(row, "top")

        self.assertEqual(payload["margin_data_status"], "missing")
        self.assertIsNone(payload["gross_margin"])
        self.assertIsNone(payload["operating_margin"])
        self.assertIsNone(payload["net_margin"])

    def test_income_statement_wide_rows_calculate_margins(self):
        income_row = {
            "年度": "115",
            "季別": "1",
            "公司代號": "2027",
            "公司名稱": "大成鋼",
            "營業收入": "31174326",
            "營業毛利（毛損）淨額": "8517820",
            "營業利益（損失）": "5633209",
            "本期淨利（淨損）": "3512537",
        }
        with (
            patch.object(bot, "read_cache", return_value={}),
            patch.object(bot, "write_cache"),
            patch.object(bot, "fetch_mops_csv_rows", side_effect=[[income_row], []]),
        ):
            margins = bot.parse_income_margins()

        self.assertEqual(margins["2027"]["margin_data_status"], "available")
        self.assertAlmostEqual(margins["2027"]["gross_margin"], 27.32)
        self.assertAlmostEqual(margins["2027"]["operating_margin"], 18.07)
        self.assertAlmostEqual(margins["2027"]["net_margin"], 11.27)

    def test_formal_top_rejects_missing_profitability_data(self):
        row = stock("2027", "top", 1)
        row["profitability_data_complete"] = False

        top, watch = bot.split_top_and_watchlist([row], 10)

        self.assertEqual(top, [])
        self.assertEqual([item["stock_id"] for item in watch], ["2027"])

    def test_opportunity_rejects_current_and_two_day_institutional_selling(self):
        row = stock("3706", "opportunity", 1)
        row.update(
            {
                "institutional_10d_sum": 10_000_000,
                "latest_institutional_net": -1_087_000,
                "recent_2d_institutional_net": -445_000,
                "latest_volume": 2_000_000,
                "close": 85.0,
                "margin_risk": False,
                "event_risk_flags": "",
            }
        )

        selected = bot.select_opportunity_stocks([row], [])

        self.assertEqual(selected, [])

    def test_review_risks_are_chinese_and_never_claim_no_risk(self):
        row = stock("2884", "radar", 1)
        row.update(
            {
                "top_quality_pass": False,
                "top_quality_reason": "weak_volume_ratio,fundamental_floor",
                "entry_volume_ratio": 0.59,
            }
        )

        review = bot.summarize_free_chip_review(row)
        risk_text = "、".join(review["risks"])

        self.assertIn("量比0.59x，量能偏弱", risk_text)
        self.assertIn("基本面底線未通過", risk_text)
        self.assertNotIn("weak_volume_ratio", risk_text)
        self.assertNotIn("未見主要風險", risk_text)

    def test_branch_report_has_rule_fallback_when_gemini_times_out(self):
        row = stock("2884", "radar", 1)
        row.update(
            {
                "source": "radar",
                "top_quality_pass": False,
                "top_quality_reason": "weak_volume_ratio,fundamental_floor",
                "entry_volume_ratio": 0.59,
            }
        )

        def fail_gemini(items):
            bot.DATA_SOURCE_STATUS["gemini_review"] = "failed timeout"

        with (
            patch.object(bot, "today_strategy_entries", return_value=[row]),
            patch.object(bot, "attach_gemini_reviews_to_review_items", side_effect=fail_gemini),
        ):
            message = bot.build_branch_report_message()

        self.assertIn("Gemini：逾時或暫時不可用，以下採規則覆判", message)
        self.assertIn("規則摘要：", message)
        self.assertIn("量比0.59x，量能偏弱", message)
        self.assertNotIn("weak_volume_ratio", message)
        self.assertNotIn("未見主要風險", message)
        self.assertNotIn("信心 未啟用", message)

    def test_grounded_gemini_reviews_are_batched(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append((request, timeout))
            body = json.loads(request.data.decode("utf-8"))
            batch = json.loads(body["contents"][0]["parts"][0]["text"])["candidates"]
            reviews = [
                {
                    "stock_id": item["stock_id"],
                    "summary": "已查核公開資訊",
                    "risk": "需求與法人買盤可能轉弱",
                    "confidence": 7,
                }
                for item in batch
            ]
            return GeminiResponse(
                {
                    "candidates": [
                        {
                            "content": {"parts": [{"text": json.dumps(reviews, ensure_ascii=False)}]},
                            "groundingMetadata": {"groundingChunks": [{"web": {"uri": "https://example.com"}}]},
                        }
                    ]
                }
            )

        candidates = [
            {"stock_id": "2027", "stock_name": "大成鋼"},
            {"stock_id": "2317", "stock_name": "鴻海"},
            {"stock_id": "6214", "stock_name": "精誠"},
        ]
        with (
            patch.dict(
                os.environ,
                {
                    "GEMINI_API_KEY": "secret",
                    "GEMINI_MODEL_SEQUENCE": "gemini-2.5-flash-lite",
                    "GEMINI_BATCH_SIZE": "2",
                    "GEMINI_ENABLE_GOOGLE_SEARCH": "true",
                    "GEMINI_RETRY": "1",
                },
                clear=False,
            ),
            patch.object(bot, "urlopen", side_effect=fake_urlopen),
        ):
            reviews = bot.request_gemini_reviews(candidates)

        self.assertEqual(len(reviews), 3)
        self.assertEqual(len(requests), 2)
        for request, _timeout in requests:
            body = json.loads(request.data.decode("utf-8"))
            self.assertEqual(body["tools"], [{"google_search": {}}])
            self.assertNotIn("responseMimeType", body["generationConfig"])
        self.assertIn("enabled grounded reviewed=3", bot.DATA_SOURCE_STATUS["gemini_review"])

    def test_partial_market_cache_refetches_missing_otc_rows(self):
        listed = {
            "stock_id": "2330",
            "market_type": "listed",
            "date": "20260727",
            "close": 1000,
            "volume": 1_000_000,
            "turnover": 1_000_000_000,
        }
        tpex_payload = {
            "tables": [
                {
                    "fields": ["代號", "開盤", "最高", "最低", "收盤", "成交股數", "成交金額(元)"],
                    "data": [["6488", "100", "102", "99", "101", "500000", "50500000"]],
                }
            ]
        }
        with (
            patch.object(bot, "read_cache", return_value=[listed]),
            patch.object(bot, "fetch_with_retry", return_value=tpex_payload),
            patch.object(bot, "write_cache") as write_cache,
        ):
            rows = bot.fetch_all_market_day(bot.datetime(2026, 7, 27).date())

        self.assertEqual({row["market_type"] for row in rows}, {"listed", "otc"})
        write_cache.assert_called_once()

    def test_market_history_waits_for_both_markets(self):
        calls = []

        def market_day(day):
            calls.append(day)
            date_key = day.strftime("%Y%m%d")
            rows = [
                {
                    "stock_id": "2330",
                    "market_type": "listed",
                    "date": date_key,
                    "close": 1000,
                    "volume": 1_000_000,
                }
            ]
            if len(calls) != 2:
                rows.append(
                    {
                        "stock_id": "6488",
                        "market_type": "otc",
                        "date": date_key,
                        "close": 100,
                        "volume": 500_000,
                    }
                )
            return rows

        with patch.object(bot, "fetch_all_market_day", side_effect=market_day):
            history = bot.fetch_all_market_price_history(target_days=2)

        self.assertEqual(len(calls), 3)
        self.assertGreaterEqual(len(history["6488"]), 2)
        self.assertIn("listed_days=3 otc_days=2", bot.DATA_SOURCE_STATUS["price"])

    def test_exit_alert_excludes_current_selection(self):
        current = stock("2317", "opportunity", 1)
        current.update(
            {
                "close": 90,
                "ma20": 100,
                "ma60": 110,
                "prior_institutional_net": -100,
            }
        )
        tracker = {
            "history": [
                {
                    "date": "2026-07-27",
                    "entries": [{"stock_id": "2317", "source": "opportunity", "entry_close": 95}],
                }
            ]
        }
        with patch.object(bot, "read_cache", return_value=tracker):
            alerts = bot.build_exit_alerts([current], excluded_ids={"2317"})

        self.assertEqual(alerts, [])


if __name__ == "__main__":
    unittest.main()
