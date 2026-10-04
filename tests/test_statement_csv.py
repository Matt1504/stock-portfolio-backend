import csv
import io
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from statement_import.parser import parse_statement

SAMPLE = '''"date","transaction","description","amount","balance","currency"
"2025-11-04","DIV","ZFL - BMO Asset Management Inc. - Long Federal Bond Index ETF: Cash dividend distribution, received on 2025-11-04, record date of 2025-10-30","21.95","166.56","CAD"
"2025-11-04","ROC","ZFL: Return of Capital (Adjusted Cost Base Entry Only) of $-0.1105 (executed at 2025-11-04)","0.0","166.56","ZFL"
'''


def export(rows, headers=None):
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(headers or ['date', 'transaction', 'description', 'amount', 'balance', 'currency'])
    writer.writerows(rows)
    return stream.getvalue()


class StatementCSVTests(unittest.TestCase):
    def test_etf_rebate_is_account_level_and_rejects_negative_amounts(self):
        row = ['2026-04-20', 'REIMB', 'ETF Rebate (executed at 2026-04-20)', '1.12', '726.54', 'CAD']
        result = parse_statement(export([row]))
        self.assertEqual(result['errors'], [])
        entry = result['transactions'][0]
        self.assertEqual(entry['activity'], 'ETF Rebate')
        self.assertEqual(entry['transactionDate'], '2026-04-20')
        self.assertEqual(entry['total'], '1.12')
        self.assertEqual(entry['totalCurrency'], 'CAD')
        for field in ('ticker', 'shares', 'price', 'fee', 'exchangeRate'):
            self.assertNotIn(field, entry)
        row[3] = '-1.12'
        self.assertTrue(parse_statement(export([row]))['errors'])

    def test_actual_export_sample_dividend_and_roc_ticker_currency(self):
        parsed = parse_statement(SAMPLE)
        self.assertEqual(parsed['errors'], [])
        self.assertEqual(len(parsed['transactions']), 1)
        self.assertEqual(parsed['transactions'][0]['ticker'], 'ZFL')
        self.assertEqual(parsed['transactions'][0]['total'], '21.95')
        self.assertEqual(parsed['transactions'][0]['activity'], 'Dividends')
        self.assertEqual(parsed['skipped'][0]['row'], 2)
        self.assertEqual(parse_statement('\ufeff' + SAMPLE)['errors'], [])

    def test_signed_amounts_quoted_commas_multiline_descriptions_and_fx(self):
        text = export([
            ['2025-11-04', 'BUY', 'ACWV - Global ETF: Bought 1.6324 shares at $119.65\nper share, FX Rate: 1.4106', '-275.54', '100', 'CAD'],
            ['2025-11-05', 'FEE', 'Service fees', '-21.04', '78.96', 'CAD'],
            ['2025-11-05', 'NRT', 'Withholding on withdrawal', '-3', '75.96', 'CAD'],
            ['2025-11-06', 'TRFIN', 'Transfer', '2,000.00', '2075.96', 'CAD'],
            ['2025-11-06', 'EFT', 'Deposit', '1000', '3075.96', 'CAD'],
        ])
        parsed = parse_statement(text)
        self.assertEqual(parsed['errors'], [])
        self.assertEqual([r['total'] for r in parsed['transactions']], ['275.54', '21.04', '3', '2000.00', '1000'])
        trade = parsed['transactions'][0]
        self.assertEqual(trade['priceCurrency'], 'USD')
        self.assertEqual(trade['exchangeRate'], '1.4106')
        self.assertEqual(trade['shares'], '1.6324')
        self.assertEqual(trade['row'], 1)

    def test_legacy_debit_credit_layout_is_rejected_and_positive_outflow_amount_is_supported(self):
        row = ['2025-11-04', 'BUY', 'XSH - Bond ETF: Bought 2 shares at $10.00 per share', '20', '0', '100']
        parsed = parse_statement(export([row], ['Date', 'Transaction', 'Description', 'Debit ($)', 'Credit ($)', 'Balance ($)']))
        self.assertTrue(parsed['errors'])
        parsed = parse_statement(export([['2025-11-04', 'FEE', 'Fee', '21.04', '100', 'CAD']]))
        self.assertEqual(parsed['transactions'][0]['total'], '21.04')

    def test_unsupported_currency_dates_codes_and_bad_amounts_block_preview(self):
        row = ['2025-11-04', 'DIV', 'ZFL - Bond ETF: Distribution', '21.95', '100', 'CAD']
        for field, value in [(0, '11/04/2025'), (1, 'SELL'), (3, '-1'), (3, 'NaN'), (3, 'abc'), (3, '0'), (3, '1.001'), (5, 'USD')]:
            changed = list(row)
            changed[field] = value
            with self.subTest(field=field, value=value):
                self.assertTrue(parse_statement(export([changed]))['errors'])

    def test_bad_headers_malformed_rows_and_quotes_are_actionable(self):
        for text in ['date,type,amount\n2025-11-04,BUY,10', 'date,transaction,description,amount,balance,currency\n2025-11-04,FEE,Fee,10', 'date,transaction,description,amount,balance,currency\n"unterminated', export([])]:
            with self.subTest(text=text):
                self.assertTrue(parse_statement(text)['errors'])

    def test_pdf_statement_text_is_rejected(self):
        result = parse_statement('2025-10-14 BUY XSH - Bond ETF: Bought 2 shares at $10.00 per share $20.00 $0.00 $100.00')
        self.assertTrue(result['errors'])
        self.assertEqual(result['transactions'], [])

    def test_noncash_distribution_is_explicitly_skipped_before_currency_checks(self):
        text = export([['2025-12-30', 'NCDIS', 'ZFL: Non-cash Distribution (Adjusted Cost Base Entry Only) of $8.5661 (executed at 2025-12-30)', '0.0', '184.23', 'ZFL']])
        parsed = parse_statement(text)
        self.assertEqual(parsed['errors'], [])
        self.assertEqual(parsed['transactions'], [])
        self.assertEqual(parsed['skipped'][0]['code'], 'NCDIS')
        self.assertIn('adjusted-cost-base-only', parsed['skipped'][0]['reason'])

    def test_unnamed_tax_inherits_immediately_preceding_same_date_dividend(self):
        parsed = parse_statement(export([
            ['2025-11-04', 'DIV', 'ACWV - Global ETF: Cash dividend', '20', '100', 'CAD'],
            ['2025-11-04', 'NRT', 'Non-resident withholding tax', '-3', '97', 'CAD'],
        ]))
        self.assertEqual(parsed['errors'], [])
        tax = parsed['transactions'][1]
        self.assertEqual(tax['ticker'], 'ACWV')
        self.assertEqual(tax['stockName'], 'Global ETF')
        self.assertTrue(tax['stockInferred'])
        self.assertEqual(tax['dividendRow'], 1)
        self.assertNotIn('stockCurrency', tax)  # Resolve omitted dividend FX from saved stock.
        self.assertEqual(tax['total'], '3')

    def test_tax_does_not_inherit_through_intervening_rows_or_different_dates(self):
        dividend = ['2025-11-04', 'DIV', 'ACWV - Global ETF: Dividend', '20', '100', 'CAD']
        tax = ['2025-11-04', 'NRT', 'Account tax', '-3', '97', 'CAD']
        for middle in ([['2025-11-04', 'EFT', 'Deposit', '100', '200', 'CAD']], [['2025-11-04', 'ROC', 'Adjustment', '0', '100', 'ACWV']], [['2025-11-04', 'SELL', 'Unknown', '-10', '100', 'CAD']]):
            with self.subTest(middle=middle):
                parsed = parse_statement(export([dividend] + middle + [tax]))
                self.assertNotIn('ticker', parsed['transactions'][-1])
        changed = list(tax)
        changed[0] = '2025-11-05'
        self.assertNotIn('ticker', parse_statement(export([dividend, changed]))['transactions'][1])
        explicit = list(tax)
        explicit[2] = 'VOO - Vanguard ETF: Withholding'
        self.assertEqual(parse_statement(export([dividend, explicit]))['transactions'][1]['ticker'], 'VOO')
