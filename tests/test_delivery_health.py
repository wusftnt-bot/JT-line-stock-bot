import io
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scripts import ai_stock_bot as bot
from scripts import market_calendar as calendar
from scripts.line_watchdog import has_delivery_evidence


class DeliveryHealthTests(unittest.TestCase):
    def test_holidays_do_not_require_new_prices(self):
        for day in (date(2026, 9, 25), date(2026, 9, 28)):
            self.assertFalse(calendar.is_trading_day(day))
            self.assertEqual(calendar.expected_trade_date(day), '20260924')
        self.assertEqual(calendar.expected_trade_date(date(2026, 9, 29)), '20260929')
        self.assertEqual(calendar.expected_trade_date(date(2026, 10, 12)), '20261012')

    def test_calendar_first_and_last_trading_days_are_not_holidays(self):
        rows = [['2026-01-02', '國曆新年開始交易日', '']]
        rows += [[f'2026-02-{day:02}', '市場無交易，僅辦理結算交割作業', ''] for day in range(12, 22)]
        closed = calendar.parse_calendar({'stat': 'ok', 'queryYear': 2026, 'data': rows}, 2026)
        self.assertNotIn('2026-01-02', closed)
        self.assertIn('2026-02-12', closed)

    def test_skipped_green_workflow_is_not_delivery(self):
        jobs = [{'conclusion': 'success', 'steps': [
            {'name': 'Run AI stock bot and push LINE message', 'conclusion': 'skipped'},
            {'name': 'Create sent marker', 'conclusion': 'skipped'},
        ]}]
        self.assertFalse(has_delivery_evidence(jobs, 'main'))
        for step in jobs[0]['steps']:
            step['conclusion'] = 'success'
        self.assertTrue(has_delivery_evidence(jobs, 'main'))

    def test_audit_checks_expected_session_not_calendar_day_streak(self):
        row = {'stock_id': '2330', 'aligned_trade_date': '20260924'}
        previous = {'audit_date': '2026-09-24', 'common_trade_dates': '20260924', 'stale_trade_date_streak': 1}
        for day, stale in (('2026-09-25', False), ('2026-09-28', False), ('2026-09-29', True)):
            with patch.dict(os.environ, {'AI_STOCK_AUDIT_DATE_OVERRIDE': day}), patch.object(bot, 'read_cache', return_value={'history': [previous]}), patch.object(bot, 'write_cache'):
                result = bot.audit_candidate_pool([row], [row])
            self.assertEqual('common_trade_date_not_expected' in result['warnings'], stale)

    def test_retry_same_key_accepts_duplicate_without_double_push(self):
        seen = []
        error = HTTPError(bot.LINE_PUSH_URL, 409, 'duplicate', {'x-line-accepted-request-id': 'accepted'}, io.BytesIO())

        def response(request, **kwargs):
            seen.append(request.get_header('X-line-retry-key'))
            if len(seen) == 1:
                raise URLError('timeout')
            raise error

        with patch.dict(os.environ, {'LINE_CHANNEL_ACCESS_TOKEN': 'test', 'LINE_TO': 'test', 'LINE_TEST_PUSH': 'false'}), patch.object(bot, 'urlopen', side_effect=response), patch.object(bot.time, 'sleep'), patch.object(bot, 'write_run_result') as result:
            bot.push_line_message('test')
        self.assertEqual(len(set(seen)), 1)
        result.assert_called_once_with('sent', http_status=409, duplicate_accepted=True)

    def test_operational_record_does_not_contain_credentials(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bot, 'REPORT_DIR', Path(directory)), patch.dict(os.environ, {'LINE_TO': 'private-recipient', 'LINE_CHANNEL_ACCESS_TOKEN': 'private-key', 'GITHUB_OUTPUT': ''}):
            bot.write_run_result('delivery_failed', http_status=401)
            record = (Path(directory) / 'line-run-result.json').read_text(encoding='utf-8')
        self.assertNotIn('private-recipient', record)
        self.assertNotIn('private-key', record)
        self.assertEqual(json.loads(record)['http_status'], 401)

    def test_review_rejects_unconfirmed_primary_cache(self):
        today = bot.datetime.now(bot.TAIPEI_TZ).date().isoformat()
        tracker = {'history': [{'date': today, 'entries': [{'stock_id': '2330'}]}]}
        with patch.dict(os.environ, {'LINE_TEST_PUSH': 'false', 'AI_STOCK_BRANCH_ALLOW_LATEST_DATA': 'false'}), patch.object(bot, 'read_cache', side_effect=[tracker, {}]):
            self.assertEqual(bot.today_strategy_entries(), [])

    def test_review_accepts_confirmed_current_session(self):
        today = bot.datetime.now(bot.TAIPEI_TZ).date()
        tracker = {'history': [{'date': today.isoformat(), 'entries': [{'stock_id': '2330'}]}]}
        receipt = {'date': today.isoformat(), 'sent': True, 'test': False, 'trade_date': calendar.expected_trade_date(today)}
        with patch.dict(os.environ, {'LINE_TEST_PUSH': 'false', 'AI_STOCK_BRANCH_ALLOW_LATEST_DATA': 'false'}), patch.object(bot, 'read_cache', side_effect=[tracker, receipt]):
            self.assertEqual(bot.today_strategy_entries(), [{'stock_id': '2330'}])

    def test_history_does_not_request_known_closed_dates(self):
        calls = []
        rows = [{'stock_id': '2330', 'market_type': 'listed', 'date': '20260924'}]
        rows += [{'stock_id': '6488', 'market_type': 'otc', 'date': '20260924'}]
        with patch.object(bot, 'datetime') as clock, patch.object(bot, 'fetch_all_market_day', side_effect=lambda day: calls.append(day) or rows):
            clock.now.return_value.date.return_value = date(2026, 9, 28)
            bot.fetch_all_market_price_history(target_days=1)
        self.assertEqual(calls, [date(2026, 9, 24)])

    def test_formal_universe_rejects_old_individual_session(self):
        rows = [{'stock_id': '2330', 'market_type': 'listed'}]
        prices = {'2330': [{'date': '20261008', 'market_type': 'listed'}]}
        institutional = {'2330': [{'trade_date': '20261008'}]}
        with patch.dict(os.environ, {'AI_STOCK_REQUIRED_TRADE_DATE': '20261012'}):
            result = bot.build_investable_universe(rows, {}, {}, institutional, {}, prices)
        self.assertEqual(result, [])
        self.assertEqual(bot.UNIVERSE_REJECTION_REASONS['2330'], 'stale_common_date')

    def test_tpex_old_day_is_not_cached_as_requested_today(self):
        text = '115年10月8日\n代號,外資及陸資(不含外資自營商)-買賣超股數,投信-買賣超股數,自營商-買賣超股數\n6488,100,20,0\n'
        with patch.object(bot, 'read_cache', return_value=None), patch.object(bot, 'request_text', return_value=text), patch.object(bot, 'write_cache') as cache:
            rows = bot.fetch_tpex_institutional_day('20261012')
        self.assertEqual(rows, [])
        self.assertEqual(cache.call_args.args[0], 'tpex_institutional_v2_20261008')

    def test_poisoned_tpex_cache_is_refetched(self):
        bad = [{'trade_date': '20261008'}]
        text = '115年10月12日\n代號,外資及陸資(不含外資自營商)-買賣超股數,投信-買賣超股數,自營商-買賣超股數\n6488,100,20,0\n'
        with patch.object(bot, 'read_cache', return_value=bad), patch.object(bot, 'request_text', return_value=text) as request, patch.object(bot, 'write_cache'):
            rows = bot.fetch_tpex_institutional_day('20261012')
        request.assert_called_once()
        self.assertEqual(rows[0]['trade_date'], '20261012')

    def test_wrong_twse_response_date_is_not_relabelled(self):
        payload = {'date': '20261008', 'fields': ['證券代號'], 'data': [['2330']]}
        with patch.object(bot, 'recent_market_dates', return_value=['20261012']), patch.object(bot, 'fetch_with_retry', return_value=payload), patch.object(bot, 'fetch_tpex_institutional_day', return_value=[]):
            self.assertEqual(bot.fetch_institutional_maps(), {})

    def test_formal_report_requires_both_market_sessions(self):
        revenue = [{'stock_id': '2330', 'market_type': 'listed'}, {'stock_id': '6488', 'market_type': 'otc'}]
        prices = {'2330': [{'date': '20261012'}], '6488': [{'date': '20261012'}]}
        inst = {'2330': [{'trade_date': '20261012'}], '6488': [{'trade_date': '20261008'}]}
        with patch.dict(os.environ, {'AI_STOCK_REQUIRED_TRADE_DATE': '20261012'}):
            with self.assertRaisesRegex(RuntimeError, 'otc'):
                bot.assert_market_sources_current(revenue, inst, prices)
            inst['6488'] = [{'trade_date': '20261012'}]
            bot.assert_market_sources_current(revenue, inst, prices)

    def test_same_day_institutional_data_is_always_refreshed(self):
        today = bot.datetime.now(bot.TAIPEI_TZ).date()
        key = today.strftime('%Y%m%d')
        caption = f'{today.year - 1911}年{today.month}月{today.day}日'
        text = caption + '\n代號,外資及陸資(不含外資自營商)-買賣超股數,投信-買賣超股數,自營商-買賣超股數\n6488,200,20,0\n'
        with patch.object(bot, 'read_cache', return_value=[{'trade_date': key, 'foreign': 1}]), patch.object(bot, 'request_text', return_value=text) as request, patch.object(bot, 'write_cache') as cache:
            rows = bot.fetch_tpex_institutional_day(key)
        request.assert_called_once()
        cache.assert_not_called()
        self.assertEqual(rows[0]['foreign'], 200)


if __name__ == '__main__':
    unittest.main()
