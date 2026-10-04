import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from statement_import.parser import parse_statement
from statement_import.importer import prepare_import, apply_import

TEXT = '\n'.join([
    '"date","transaction","description","amount","balance","currency"',
    '"2025-10-14","BUY","XSH - Canadian Bond ETF: Bought 2.0524 shares at $19.27 per share (executed at 2025-10-10)","-39.55","1155.46","CAD"',
    '"2025-10-14","BUY","ACWV - Global ETF: Bought 1.6324 shares at $119.65 per share (executed at 2025-10-10), FX Rate: 1.4106","-275.54","141.63","CAD"',
    '"2025-10-24","TRFIN","Money transfer","2000.00","2141.63","CAD"',
    '"2025-10-29","EFT","Deposit","1000.00","1158.07","CAD"',
    '"2025-10-31","DIV","XSH - Canadian Bond ETF: Cash distribution","1.82","165.65","CAD"',
    '"2025-10-31","FEE","Management fees","-21.04","144.61","CAD"',
    '"2025-10-31","NRT","Withholding tax on withdrawal","-3.00","141.61","CAD"',
    '"2025-10-31","ROC","XSH: Return of Capital of $-0.0803","0","141.61","XSH"',
])


def edges(records):
    return {'edges': [{'node': r} for r in records], 'pageInfo': {'hasNextPage': False}}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.stocks = [{'id': 'xsh', 'name': 'Canadian Bond ETF', 'ticker': 'XSH', 'currency': {'id': 'cad', 'code': 'CAD'}, 'asset': {'name': 'Stock'}}]
        self.history = []
        self.fail_row = None

    def execute(self, query, variables=None):
        self.calls.append((query, variables))
        if 'createStock(' in query:
            stock = variables['input']
            self.stocks.append(dict(stock, id='new-stock', currency={'id': stock['currency'], 'code': 'USD'}, asset={'name': 'Stock'}))
            return {'createStock': {'stock': {'id': 'new-stock', 'ticker': stock['ticker']}}}
        if 'createTransaction(' in query:
            data = variables['input']
            if data['activity'] == self.fail_row:
                raise RuntimeError('Ownership validation failed')
            return {'createTransaction': {'transaction': {'id': 'new-' + str(len(self.calls))}, 'warnings': [{'code': 'TRANSACTION_TOTAL_DIFFERENCE', 'message': 'Saved statement total'}]}}
        if 'transactionsByPlatform(' in query:
            return {'transactionsByPlatform': self.history}
        if 'platforms(profileId:' in query:
            return {'platforms': edges([{'id': 'platform', 'name': 'Wealthsimple', 'account': {'id': 'nrsa', 'code': 'NRSA'}, 'currency': {'id': 'cad', 'code': 'CAD'}}])}
        return {'profiles': edges([{'id': 'profile', 'name': 'Example'}]),
                'stocks': edges(self.stocks), 'assets': edges([{'id': 'asset', 'name': 'Stock'}]),
                'currencies': edges([{'id': 'cad', 'code': 'CAD'}, {'id': 'usd', 'code': 'USD'}]),
                'activities': edges([{'id': name, 'name': name} for name in ['Buy', 'Contribution', 'Dividends', 'Service Fee', 'Withholding Tax']])}


class StatementTests(unittest.TestCase):
    def test_wrapped_rows_activity_mapping_fx_posting_date_and_authoritative_total(self):
        parsed = parse_statement(TEXT)
        self.assertEqual(parsed['errors'], [])
        self.assertEqual(len(parsed['transactions']), 7)
        self.assertEqual(len(parsed['skipped']), 1)
        first, foreign = parsed['transactions'][:2]
        self.assertEqual(first['transactionDate'], '2025-10-14')
        self.assertEqual(first['shares'], '2.0524')
        self.assertEqual(first['priceCurrency'], 'CAD')
        self.assertEqual(foreign['priceCurrency'], 'USD')
        self.assertEqual(foreign['exchangeRate'], '1.4106')
        self.assertEqual(foreign['total'], '275.54')
        self.assertEqual([r['activity'] for r in parsed['transactions'][2:]], ['Contribution', 'Contribution', 'Dividends', 'Service Fee', 'Withholding Tax'])
        self.assertNotIn('ticker', parsed['transactions'][-1])
        self.assertEqual(parse_statement(TEXT.replace('\n', '\r\n'))['errors'], [])

    def test_invalid_rows_are_visible_and_block_import(self):
        cases = [('","BUY","XSH', '","SELL","XSH'), ('2025-10-14', '2025-99-14'), ('2.0524 shares', '0 shares'), ('FX Rate: 1.4106', 'FX Rate: nope'), ('-39.55', 'NaN'), ('-39.55', '0')]
        for before, after in cases:
            with self.subTest(before=before):
                parsed = parse_statement(TEXT.replace(before, after))
                self.assertTrue(parsed['errors'])
                client = FakeClient()
                with self.assertRaises(ValueError):
                    prepare_import(client, parsed, 'Example', 'Wealthsimple')
                self.assertEqual(client.calls, [])
        self.assertTrue(parse_statement('unexpected preamble\n' + TEXT)['errors'])
        self.assertTrue(parse_statement('')['errors'])

    def test_nrt_with_stock_and_too_precise_values(self):
        parsed = parse_statement('"date","transaction","description","amount","balance","currency"\n"2025-10-31","NRT","XSH - Canadian Bond ETF: Tax","-1.00","5","CAD"')
        self.assertEqual(parsed['transactions'][0]['ticker'], 'XSH')
        self.assertTrue(parse_statement(TEXT.replace('2.0524 shares', '2.123456789 shares'))['errors'])

    def test_plan_and_apply_resolve_stocks_preserve_totals_and_warnings(self):
        client = FakeClient()
        plan = prepare_import(client, parse_statement(TEXT), 'Example', 'Wealthsimple')
        self.assertEqual(plan['errors'], [])
        self.assertEqual(len(plan['stocksToCreate']), 1)
        self.assertFalse(any('mutation' in q for q, _ in client.calls))
        progress = []
        report = apply_import(client, plan, lambda r: progress.append(copy.deepcopy(r)))
        self.assertTrue(report['complete'])
        self.assertEqual(len(report['createdTransactions']), 7)
        self.assertEqual(len(report['createdStocks']), 1)
        writes = [v['input'] for q, v in client.calls if 'createTransaction(' in q]
        self.assertEqual(writes[1]['stock'], 'new-stock')
        self.assertEqual(writes[1]['priceCurrency'], 'usd')
        self.assertEqual(writes[1]['total'], '275.54')
        self.assertNotIn('stock', writes[-1])
        self.assertTrue(report['createdTransactions'][1]['warnings'])
        self.assertTrue(any(r.get('inFlight') for r in progress))

    def test_duplicate_counts_and_conflicting_fx(self):
        client = FakeClient()
        client.history = [{'id': 'existing', 'transactionDate': '2025-10-14', 'activity': {'name': 'Buy'}, 'stock': {'ticker': 'XSH'}, 'price': 19.27, 'shares': 2.0524, 'fee': None, 'total': 39.55, 'exchangeRate': 1, 'priceCurrency': {'code': 'CAD'}, 'totalCurrency': {'code': 'CAD'}}]
        parsed = parse_statement(TEXT)
        parsed['transactions'] = [parsed['transactions'][0], parsed['transactions'][0]]
        plan = prepare_import(client, parsed, 'Example', 'Wealthsimple')
        self.assertEqual(plan['errors'], [])
        self.assertEqual([r['status'] for r in plan['rows']], ['existing', 'pending'])
        client.history[0]['priceCurrency'] = {'code': 'USD'}
        self.assertTrue(prepare_import(client, parsed, 'Example', 'Wealthsimple')['errors'])

    def test_ambiguous_currency_stock_profile_and_fund_preflight(self):
        parsed = parse_statement(TEXT)
        client = FakeClient()
        client.stocks.append(dict(client.stocks[0], id='duplicate'))
        self.assertTrue(prepare_import(client, parsed, 'Example', 'Wealthsimple')['errors'])
        client = FakeClient()
        client.stocks[0]['currency']['code'] = 'USD'
        self.assertTrue(prepare_import(client, parsed, 'Example', 'Wealthsimple')['errors'])
        with self.assertRaises(ValueError):
            prepare_import(FakeClient(), parsed, 'Missing', 'Wealthsimple')
        client = FakeClient()
        client.stocks[0]['asset']['name'] = 'Index Fund'
        client.history = [{'id': 'old', 'transactionDate': '2025-01-01', 'activity': {'name': 'Buy'}, 'stock': {'ticker': 'XSH'}, 'total': 10, 'shares': 0}]
        self.assertTrue(prepare_import(client, parsed, 'Example', 'Wealthsimple')['errors'])

    def test_partial_failure_stops_without_retry_and_keeps_audit(self):
        client = FakeClient()
        client.fail_row = 'Dividends'
        plan = prepare_import(client, parse_statement(TEXT), 'Example', 'Wealthsimple')
        report = apply_import(client, plan)
        self.assertFalse(report['complete'])
        self.assertEqual(len(report['createdTransactions']), 4)
        self.assertEqual(report['inFlight']['row'], 5)
        self.assertEqual(len(report['errors']), 1)
        writes = [v['input'] for q, v in client.calls if 'createTransaction(' in q]
        self.assertFalse(any(r['activity'] == 'Service Fee' for r in writes))

    def test_graphql_documents_match_application_schema(self):
        from graphql import parse, validate
        from schemas.schema import schema
        client = FakeClient()
        plan = prepare_import(client, parse_statement(TEXT), 'Example', 'Wealthsimple')
        apply_import(client, plan)
        for query, variables in client.calls:
            self.assertEqual(validate(schema, parse(query)), [], query)

    def test_cli_offline_report_does_not_access_api(self):
        import io
        import json
        import tempfile
        from unittest.mock import patch
        from import_statement import main
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'preview.json'
            with patch('sys.argv', ['import_statement.py', '-', '--report', str(report)]), patch('sys.stdin', io.StringIO(TEXT)), patch('sys.stdout', io.StringIO()), patch('statement_import.importer.GraphQLClient.execute', side_effect=AssertionError('Unexpected API call')):
                self.assertEqual(main(), 0)
            saved = json.loads(report.read_text())
            self.assertEqual(len(saved['transactions']), 7)
            self.assertEqual(saved['errors'], [])

    def test_rerun_skips_all_previously_saved_rows(self):
        client = FakeClient()
        parsed = parse_statement(TEXT)
        for entry in parsed['transactions']:
            client.history.append(dict(entry, id=str(entry['row']), activity={'name':entry['activity']}, stock={'ticker':entry.get('ticker')}, priceCurrency={'code':entry.get('priceCurrency', 'CAD')}, totalCurrency={'code':'CAD'}))
        plan = prepare_import(client, parsed, 'Example', 'Wealthsimple')
        self.assertEqual(plan['errors'], [])
        self.assertEqual(plan['summary']['existingTransactions'], 7)
        report = apply_import(client, plan)
        self.assertTrue(report['complete'])
        self.assertEqual(report['createdTransactions'], [])
        self.assertFalse(any('mutation' in q for q, _ in client.calls))
