import unittest
from urllib.request import Request
from scripts.financial_journal import public_facts, SafeRedirect
from scripts.financial_metrics import update_period_history


class FinancialJournalTests(unittest.TestCase):
    def test_only_public_financial_fields_can_be_persisted(self):
        ledger = {'stocks': {'2330': {'2026Q2': {'eps': 8, 'financial_period': '2026Q2', 'LINE_TO': 'private', 'api_key': 'private'}}}}
        stored = public_facts(ledger)
        self.assertEqual(stored['stocks']['2330']['2026Q2'], {'eps': 8, 'financial_period': '2026Q2'})

    def test_redirect_does_not_forward_github_credentials(self):
        request = Request('https://api.github.com/repos/owner/repo/contents/file', headers={'Authorization': 'Bearer private'})
        redirected = SafeRedirect().redirect_request(request, None, 302, 'redirect', {}, 'https://raw.githubusercontent.com/owner/repo/main/file')
        self.assertIsNone(redirected.get_header('Authorization'))

    def test_partial_refresh_does_not_erase_previously_reported_fact(self):
        old = {'2330': {'financial_period': '2026Q2', 'eps': 8, 'equity': 1000}}
        refresh = {'2330': {'financial_period': '2026Q2', 'eps': 8, 'equity': None}}
        ledger = update_period_history({}, old, refresh)
        self.assertEqual(ledger['stocks']['2330']['2026Q2']['equity'], 1000)
