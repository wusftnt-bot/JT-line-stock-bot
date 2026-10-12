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


if __name__ == '__main__':
    unittest.main()
