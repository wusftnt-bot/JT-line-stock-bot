import unittest

from scripts import ai_stock_bot as bot


class MarkdownReportTests(unittest.TestCase):
    def test_report_uses_passed_top_and_radar_rows(self):
        top = {
            "rank": 1,
            "stock_id": "2330",
            "stock_name": "台積電",
            "market_type": "listed",
            "history_status": "new",
            "triple_margin_up": False,
            "total_score": 80.0,
            "fundamental_score": 30.0,
            "technical_score": 20.0,
            "chip_score": 20.0,
            "yoy": 10.0,
            "mom": 2.0,
            "acc_yoy": 8.0,
            "gross_margin": 50.0,
            "operating_margin": 40.0,
            "net_margin": 35.0,
            "close": 1000.0,
            "volume_ratio": 1.0,
            "foreign_5d_sum": 1_000_000,
            "trust_5d_sum": 100_000,
        }
        radar = {
            "radar_rank": 1,
            "stock_id": "2317",
            "stock_name": "鴻海",
            "market_type": "listed",
            "history_status": "new",
            "accumulation_score": 70.0,
            "quiet_accumulation": True,
            "foreign_reversal": False,
            "institutional_20d_avg_volume_ratio": 0.1,
            "foreign_10d_sum": 1_000_000,
            "trust_10d_sum": 200_000,
            "foreign_10d_positive_days": 6,
            "trust_10d_positive_days": 4,
            "price_change_20d": 5.0,
            "volume_ratio": 1.1,
            "margin_buy_sell": 0,
        }

        report = bot.build_markdown_report([top], [radar])

        self.assertIn("2330 台積電", report)
        self.assertIn("2317 鴻海", report)


if __name__ == "__main__":
    unittest.main()
