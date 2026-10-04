import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import mongomock
from mongoengine import connect, disconnect
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from models.models import Account, Activity, Asset, Currency, Platform, Profile, Stock, Transaction
from schemas.schema import schema

PREVIEW = '''query($profile: ID!, $platform: ID!, $text: String!) {
 previewStatementImport(profileId: $profile, platform: $platform, text: $text) { parsed plan previewHash } }'''
IMPORT = '''mutation($profile: ID!, $platform: ID!, $text: String!, $hash: String!) {
 importStatement(profileId: $profile, platform: $platform, text: $text, previewHash: $hash) { report } }'''
TEXT = '\n'.join([
    '"date","transaction","description","amount","balance","currency"',
    '"2025-10-14","BUY","XSH - Canadian ETF: Bought 2 shares at $10.00 per share","-20.00","100","CAD"',
    '"2025-10-14","BUY","ACWV - Global ETF: Bought 1.6324 shares at $119.65 per share, FX Rate: 1.4106","-275.54","100","CAD"',
    '"2025-10-15","DIV","XSH - Canadian ETF: Distribution","1.82","100","CAD"',
    '"2025-10-15","FEE","Service fees","-2.00","100","CAD"',
    '"2025-10-15","ROC","XSH: Capital adjustment","0","100","XSH"',
])


class StatementImportAPITests(unittest.TestCase):
    def setUp(self):
        disconnect()
        with patch('mongoengine.connection.MongoClient', mongomock.MongoClient):
            connect('statement_import_api', uuidRepresentation='standard')
        for model in (Account, Activity, Asset, Currency, Platform, Profile, Stock, Transaction):
            model.drop_collection()
        self.owner = Profile(name='Owner').save()
        self.other = Profile(name='Other').save()
        account = Account(name='Non-Registered', code='NRSA', has_contribution_limit=False).save()
        cad = Currency(code='CAD').save()
        Currency(code='USD').save()
        asset = Asset(name='Stock').save()
        self.platform = Platform(name='Wealthsimple', account=account, currency=cad, profile=self.owner).save()
        Stock(name='Canadian ETF', ticker='XSH', currency=cad, asset=asset).save()
        for name in ('Buy', 'Dividends', 'Contribution', 'Service Fee', 'Withholding Tax', 'ETF Rebate'):
            Activity(name=name).save()
        self.variables = {'profile': str(self.owner.id), 'platform': str(self.platform.id), 'text': TEXT}

    def tearDown(self):
        disconnect()

    def preview(self, **values):
        result = schema.execute(PREVIEW, variable_values=dict(self.variables, **values))
        self.assertIsNone(result.errors, result.errors)
        return result.data['previewStatementImport']

    def test_preview_is_read_only_and_import_creates_stock_and_transactions(self):
        preview = self.preview()
        self.assertEqual(preview['plan']['summary']['transactionsToCreate'], 4)
        self.assertEqual(len(preview['plan']['stocksToCreate']), 1)
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(Stock.objects.count(), 1)
        result = schema.execute(IMPORT, variable_values=dict(self.variables, hash=preview['previewHash']))
        self.assertIsNone(result.errors, result.errors)
        report = result.data['importStatement']['report']
        self.assertTrue(report['complete'], report)
        self.assertEqual(len(report['createdTransactions']), 4)
        self.assertEqual(Transaction.objects.count(), 4)
        self.assertEqual(Transaction._get_collection().count_documents({"description": {"$exists": True}}), 0)
        acwv = Stock.objects.get(ticker='ACWV')
        self.assertEqual(acwv.currency.code, 'USD')
        self.assertEqual(acwv.asset.name, 'Stock')
        trans = Transaction.objects.get(stock=acwv)
        self.assertEqual(float(trans.total), 275.54)
        self.assertEqual(trans.platform.id, self.platform.id)
        self.assertEqual(self.preview()['plan']['summary']['existingTransactions'], 4)

    def test_etf_rebate_import_and_edit_are_account_level_and_idempotent(self):
        text = '"date","transaction","description","amount","balance","currency"\n"2026-04-20","REIMB","ETF Rebate (executed at 2026-04-20)","1.12","726.54","CAD"'
        preview = self.preview(text=text)
        self.assertEqual(preview['plan']['stocksToCreate'], [])
        result = schema.execute(IMPORT, variable_values=dict(self.variables, text=text, hash=preview['previewHash']))
        self.assertIsNone(result.errors, result.errors)
        self.assertTrue(result.data['importStatement']['report']['complete'])
        transaction = Transaction.objects.get(activity=Activity.objects.get(name='ETF Rebate'))
        self.assertEqual(str(transaction.total), '1.12')
        self.assertIsNone(transaction.stock)
        self.assertIsNone(transaction.shares)
        self.assertIsNone(transaction.description)
        self.assertEqual(transaction.total_currency.code, 'CAD')
        self.assertEqual(self.preview(text=text)['plan']['summary']['existingTransactions'], 1)
        mutation = 'mutation($profile: ID!, $input: TransactionInput!) { updateTransaction(profileId: $profile, transData: $input) { trans { id } } }'
        variables = {'profile': str(self.owner.id), 'input': {'id': str(transaction.id), 'total': '2.24'}}
        self.assertIsNone(schema.execute(mutation, variable_values=variables).errors)
        self.assertEqual(str(transaction.reload().total), '2.24')
        variables['input']['stock'] = str(Stock.objects.first().id)
        self.assertTrue(schema.execute(mutation, variable_values=variables).errors)
        create = 'mutation($profile: ID!, $input: TransactionInput!) { createTransaction(profileId: $profile, transData: $input) { transaction { id } } }'
        variables['input'] = {'platform': str(self.platform.id), 'activity': str(transaction.activity.id), 'stock': str(Stock.objects.first().id), 'total': '1.12', 'transactionDate': '2026-04-20'}
        self.assertTrue(schema.execute(create, variable_values=variables).errors)
        self.assertEqual(Transaction.objects.count(), 1)

    def test_cont_import_uses_contribution_and_deduplicates_other_contribution_codes(self):
        text = '"date","transaction","description","amount","balance","currency"\n"2026-04-20","CONT","Contribution","100.00","726.54","CAD"'
        preview = self.preview(text=text)
        self.assertEqual(preview['plan']['stocksToCreate'], [])
        result = schema.execute(IMPORT, variable_values=dict(self.variables, text=text, hash=preview['previewHash']))
        self.assertIsNone(result.errors, result.errors)
        self.assertTrue(result.data['importStatement']['report']['complete'])
        transaction = Transaction.objects.get()
        self.assertEqual(transaction.activity.name, 'Contribution')
        self.assertEqual(str(transaction.total), '100.00')
        self.assertIsNone(transaction.stock)
        for code in ('CONT', 'EFT', 'TRFIN'):
            repeated = self.preview(text=text.replace('"CONT"', '"{}"'.format(code)))
            self.assertEqual(repeated['plan']['summary']['existingTransactions'], 1)
            self.assertEqual(repeated['plan']['summary']['transactionsToCreate'], 0)

    def test_stale_preview_and_cross_profile_cannot_write(self):
        preview = self.preview()
        wrong_owner = schema.execute(PREVIEW, variable_values=dict(self.variables, profile=str(self.other.id)))
        self.assertTrue(wrong_owner.errors)
        changed = schema.execute(IMPORT, variable_values=dict(self.variables, hash=preview['previewHash'], text=TEXT.replace('-20.00', '-21.00')))
        self.assertEqual(changed.errors[0].extensions['code'], 'IMPORT_PREVIEW_STALE')
        self.assertEqual(Transaction.objects.count(), 0)
        self.assertEqual(Stock.objects.count(), 1)

    def test_parse_errors_and_partial_failure_are_reported(self):
        bad = self.preview(text=TEXT.replace('","BUY","XSH', '","SELL","XSH'))
        self.assertTrue(bad['parsed']['errors'])
        self.assertIsNone(bad['plan'])
        text = TEXT + '\n"2025-10-16","DIV","NEW - New ETF: Distribution","3.00","100","CAD"'
        preview = self.preview(text=text)
        save = Transaction.save
        def fail_last_row(transaction, *args, **kwargs):
            if transaction.stock and transaction.stock.ticker == 'NEW':
                raise RuntimeError('Simulated transaction write failure')
            return save(transaction, *args, **kwargs)
        with patch.object(Transaction, 'save', fail_last_row):
            result = schema.execute(IMPORT, variable_values=dict(self.variables, text=text, hash=preview['previewHash']))
        self.assertIsNone(result.errors)
        report = result.data['importStatement']['report']
        self.assertFalse(report['complete'])
        self.assertEqual(len(report['createdTransactions']), 4)
        self.assertEqual(report['inFlight']['row'], 6)
        self.assertIn('Simulated transaction write failure', report['errors'][0])
        self.assertEqual(Transaction.objects.count(), 4)
        self.assertEqual(self.preview(text=text)['plan']['summary']['existingTransactions'], 4)

    def test_dividend_import_without_purchase_succeeds(self):
        text = TEXT + '\n"2025-10-16","DIV","NEW - New ETF: Distribution","3.00","100","CAD"'
        preview = self.preview(text=text)
        result = schema.execute(IMPORT, variable_values=dict(self.variables, text=text, hash=preview['previewHash']))
        self.assertIsNone(result.errors)
        self.assertTrue(result.data['importStatement']['report']['complete'])
        self.assertEqual(Transaction.objects.count(), 5)

    def test_import_requires_error_free_preview_and_limits_input(self):
        bad = schema.execute(IMPORT, variable_values=dict(self.variables, text=TEXT.replace('","BUY","XSH', '","SELL","XSH'), hash='anything'))
        self.assertTrue(bad.errors)
        huge = schema.execute(PREVIEW, variable_values=dict(self.variables, text='x'*200001))
        self.assertTrue(huge.errors)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_csv_uses_same_preview_import_and_duplicate_detection(self):
        initial = self.preview()
        result = schema.execute(IMPORT, variable_values=dict(self.variables, hash=initial['previewHash']))
        self.assertTrue(result.data['importStatement']['report']['complete'])
        csv_text = '\n'.join([
            '"date","transaction","description","amount","balance","currency"',
            '"2025-11-04","DIV","XSH - Canadian ETF: Distribution, record date 2025-10-30","21.95","100","CAD"',
            '"2025-11-04","FEE","Management fee","-21.04","100","CAD"',
            '"2025-11-04","ROC","XSH: Capital adjustment","0","100","XSH"',
        ])
        preview = self.preview(text=csv_text)
        self.assertEqual(preview['parsed']['errors'], [])
        self.assertEqual(preview['plan']['summary']['transactionsToCreate'], 2)
        self.assertEqual(preview['plan']['summary']['skippedRows'], 1)
        result = schema.execute(IMPORT, variable_values=dict(self.variables, text=csv_text, hash=preview['previewHash']))
        self.assertIsNone(result.errors)
        self.assertTrue(result.data['importStatement']['report']['complete'])
        self.assertEqual(self.preview(text=csv_text)['plan']['summary']['existingTransactions'], 2)
        self.assertEqual(Transaction.objects.count(), 6)

    def test_us_dividend_tax_link_resolves_saved_currency_and_is_idempotent(self):
        initial = self.preview()
        schema.execute(IMPORT, variable_values=dict(self.variables, hash=initial['previewHash']))
        text = '\n'.join([
            '"date","transaction","description","amount","balance","currency"',
            '"2025-11-04","DIV","ACWV - Global ETF: Cash dividend distribution","20","100","CAD"',
            '"2025-11-04","NRT","Non-resident withholding tax","-3","97","CAD"',
        ])
        preview = self.preview(text=text)
        self.assertEqual(preview['plan']['errors'], [])
        tax_preview = preview['plan']['rows'][1]
        stock = Stock.objects.get(ticker='ACWV')
        self.assertEqual(tax_preview['payload']['stock'], str(stock.id))
        self.assertEqual(tax_preview['entry']['stockCurrency'], 'USD')
        self.assertTrue(tax_preview['entry']['stockInferred'])
        result = schema.execute(IMPORT, variable_values=dict(self.variables, text=text, hash=preview['previewHash']))
        self.assertTrue(result.data['importStatement']['report']['complete'])
        tax = Transaction.objects.get(stock=stock, activity=Activity.objects.get(name='Withholding Tax'))
        self.assertEqual(float(tax.total), 3)
        self.assertEqual(self.preview(text=text)['plan']['summary']['existingTransactions'], 2)
        tax.stock = None
        tax.save()
        self.assertIn('unlinked withholding tax', self.preview(text=text)['plan']['errors'][0]['message'])
