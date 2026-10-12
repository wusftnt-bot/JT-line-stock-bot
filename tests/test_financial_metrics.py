import unittest
from unittest.mock import patch

from scripts import ai_stock_bot as bot
from scripts.financial_metrics import derive_financial_metrics, update_period_history, fill_valuation_estimates, number


class FinancialMetricsTests(unittest.TestCase):
    def test_half_year_annualization_and_direct_book_value(self):
        row = derive_financial_metrics({'financial_period': '115Q2', 'eps': 13.02, 'net_income': 6842729, 'equity': 55722540, 'book_value_per_share': 106}, {})
        row['close'] = 583.1
        self.assertEqual(row['annualized_eps'], 26.04)
        self.assertAlmostEqual(row['roe'], 24.56)
        self.assertTrue(fill_valuation_estimates(row))
        self.assertEqual(row['pe_ratio'], 22.39)
        self.assertEqual(row['pbr'], 5.5)
        self.assertTrue(row['pe_ratio_estimated'])

    def test_each_ytd_quarter_uses_covered_months(self):
        for q, expected in ((1, 12), (2, 6), (3, 4), (4, 3)):
            row = derive_financial_metrics({'financial_period': f'2026Q{q}', 'eps': 3}, {})
            self.assertEqual(row['annualized_eps'], expected)

    def test_missing_prior_eps_never_means_turnaround(self):
        record = {'financial_period': '2026Q2', 'eps': 2}
        row = derive_financial_metrics(record, {'2026Q1': {'eps': -1}})
        self.assertIsNone(row['previous_eps'])
        self.assertFalse(row['eps_comparison_available'])
        self.assertFalse(row['eps_turnaround_confirmed'])
        self.assertEqual(bot.calculate_eps_acceleration_score(dict(row, yoy=100, acc_yoy=80))[0], 0)
        self.assertEqual(bot.calculate_fundamental_pre_score(dict(row, yoy=0, mom=0, acc_yoy=0))[1], 0)

    def test_repeated_same_period_retains_yoy_reference(self):
        old = {'2330': {'financial_period': '114Q2', 'eps': 5}}
        current = {'2330': {'financial_period': '115Q2', 'eps': 8}}
        ledger = update_period_history({}, old, current)
        first = derive_financial_metrics(current['2330'], ledger['stocks']['2330'])
        ledger = update_period_history(ledger, {'2330': first}, current)
        second = derive_financial_metrics(current['2330'], ledger['stocks']['2330'])
        self.assertEqual(first['eps_change_pct'], 60)
        self.assertEqual(second['eps_change_pct'], 60)
        self.assertEqual(second['eps_comparison_basis'], 'same_period_yoy')
        self.assertEqual(bot.calculate_eps_acceleration_score(second)[0], 3)

    def test_observed_loss_to_profit_is_confirmed(self):
        row = derive_financial_metrics({'financial_period': '2026Q2', 'eps': 2}, {'2025Q2': {'eps': -1}})
        self.assertTrue(row['eps_turnaround_confirmed'])
        self.assertEqual(bot.calculate_eps_acceleration_score(row)[0], 3)

    def test_ttm_bridge_requires_all_three_reports(self):
        current = {'financial_period': '2026Q2', 'eps': 13.02}
        history = {'2025Q2': {'eps': 9.13}, '2025Q4': {'eps': 19.45}}
        row = derive_financial_metrics(current, history)
        self.assertEqual(row['eps_ttm'], 23.34)
        self.assertEqual(row['eps_ttm_basis'], 'ytd_bridge_estimate')
        missing = derive_financial_metrics(current, {'2025Q4': {'eps': 19.45}})
        self.assertIsNone(missing['eps_ttm'])

    def test_roe_uses_matching_parent_income_and_equity(self):
        row = derive_financial_metrics({'financial_period': '2026Q2', 'eps': 3.1, 'net_income': 8931705, 'owner_net_income': 7700565, 'equity': 93228978, 'parent_equity': 79567303}, {})
        self.assertEqual(row['roe_scope'], 'parent')
        self.assertAlmostEqual(row['roe'], 19.36)
        self.assertTrue(row['roe_basis'].endswith('estimate'))

    def test_no_pbr_from_unrelated_pe_and_roe(self):
        row = {'close': 100, 'pe_ratio': 12, 'roe': 40}
        self.assertFalse(fill_valuation_estimates(row))
        self.assertNotIn('pbr', row)

    def test_official_negative_ttm_per_missing_does_not_use_positive_ytd(self):
        row = {'close': 100, 'annualized_eps': 4, 'official_per_missing': True, 'pbr': 2}
        self.assertFalse(fill_valuation_estimates(row))
        self.assertNotIn('pe_ratio', row)
        row['eps_ttm'] = -1
        self.assertFalse(fill_valuation_estimates(row))

    def test_official_fields_and_roc_date_alignment(self):
        twse = [{'Date': '20261008', 'Code': '2330', 'PEratio': '20.1', 'PBratio': '5'}]
        tpex = [{'Date': '1151008', 'SecuritiesCompanyCode': '6488', 'PriceEarningRatio': '30', 'PriceBookRatio': '8'}]
        with patch.object(bot, 'read_cache', return_value={}), patch.object(bot, 'write_cache'), patch.object(bot, 'fetch_with_retry', side_effect=[twse, tpex]):
            result = bot.fetch_official_valuation_map('20261008')
        self.assertEqual(result['2330']['per_source'], 'TWSE')
        self.assertEqual(result['6488']['pbr_source'], 'TPEx')
        self.assertEqual(result['6488']['valuation_trade_date'], '20261008')

    def test_stale_official_ratios_are_not_used(self):
        rows = [{'Date': '20261008', 'Code': '2330', 'PEratio': '20', 'PBratio': '5'}]
        with patch.object(bot, 'read_cache', return_value={}), patch.object(bot, 'write_cache'), patch.object(bot, 'fetch_with_retry', side_effect=[rows, []]):
            self.assertEqual(bot.fetch_official_valuation_map('20261012'), {})

    def test_unknown_period_and_missing_values_do_not_create_ratios(self):
        row = derive_financial_metrics({'financial_period': 'bad', 'eps': 3, 'net_income': None}, {})
        row['close'] = 100
        self.assertFalse(fill_valuation_estimates(row))
        self.assertIsNone(row['roe'])
        self.assertIsNone(number('--'))
        self.assertEqual(number('0'), 0)

    def test_fetch_quality_aligns_balance_and_keeps_references(self):
        state = {'financial_period_history': {'stocks': {'2330': {'2025Q2': {'eps': 5}}}}}
        summary = {'公司代號': '2330', '年度': '115', '季別': '2', '基本每股盈餘(元)': '8', '稅後淨利': '100'}
        balance = {'公司代號': '2330', '年度': '115', '季別': '2', '權益總計': '1000', '每股參考淨值': '25'}
        def get_cache(key):
            return state.get(key, {})
        def put_cache(key, value):
            state[key] = value
        with patch.object(bot, 'read_cache', side_effect=get_cache), patch.object(bot, 'write_cache', side_effect=put_cache), patch.object(bot, 'fetch_financial_sector_balances', return_value=[]), patch.object(bot, 'fetch_mops_csv_rows', side_effect=[ [summary], [], [balance], [], [summary], [], [balance], [] ]):
            first = bot.fetch_mops_financial_quality_map()['2330']
            second = bot.fetch_mops_financial_quality_map()['2330']
        self.assertEqual(first['roe'], 20)
        self.assertEqual(second['eps_change_pct'], 60)
        self.assertEqual(second['book_value_per_share'], 25)

    def test_financial_sector_supplement_is_period_aligned(self):
        summary = {'公司代號': '2855', '年度': '115', '季別': '2', '基本每股盈餘(元)': '6.96', '稅後淨利': '11566523', '產業別': '金融保險業'}
        balance = {'公司代號': '2855', '年度': '115', '季別': '2', '權益總計': '50031771', '每股參考淨值': '29.99'}
        with patch.object(bot, 'read_cache', return_value={}), patch.object(bot, 'write_cache'), patch.object(bot, 'fetch_financial_sector_balances', return_value=[balance]), patch.object(bot, 'fetch_mops_csv_rows', side_effect=[[summary], [], [], []]):
            row = bot.fetch_mops_financial_quality_map()['2855']
        self.assertEqual(row['roe'], 46.24)
        self.assertEqual(row['book_value_per_share'], 29.99)
        self.assertFalse(row['debt_ratio_applicable'])

    def test_mismatched_balance_period_cannot_supply_roe_or_book_value(self):
        summary = {'公司代號': '2330', '年度': '115', '季別': '2', '基本每股盈餘(元)': '8', '稅後淨利': '100'}
        balance = {'公司代號': '2330', '年度': '115', '季別': '1', '權益總計': '1000', '每股參考淨值': '25'}
        with patch.object(bot, 'read_cache', return_value={}), patch.object(bot, 'write_cache'), patch.object(bot, 'fetch_financial_sector_balances', return_value=[]), patch.object(bot, 'fetch_mops_csv_rows', side_effect=[[summary], [], [balance], []]):
            row = bot.fetch_mops_financial_quality_map()['2330']
        self.assertIsNone(row['roe'])
        self.assertIsNone(row['book_value_per_share'])
        self.assertFalse(row['balance_period_aligned'])

    def test_official_ratios_are_not_overwritten_by_estimates(self):
        row = {'close': 583.1, 'pe_ratio': 24.94, 'pbr': 5.49, 'annualized_eps': 26.04, 'book_value_per_share': 106, 'per_source': 'TWSE', 'pbr_source': 'TWSE'}
        self.assertFalse(fill_valuation_estimates(row))
        self.assertEqual(row['pe_ratio'], 24.94)
        self.assertEqual(row['pbr'], 5.49)

    def test_financial_sector_cache_avoids_repeated_network_requests(self):
        with patch.object(bot, 'read_cache_record', return_value={'payload': []}), patch.object(bot, 'cache_age_hours', return_value=1), patch.object(bot, 'request_json') as request:
            bot.fetch_financial_sector_balances()
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
