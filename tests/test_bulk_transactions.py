import sys
import unittest
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import mongomock
from mongoengine import connect, disconnect
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from models.models import Transaction, Platform, Profile, Account, Currency, Stock, Activity, Asset, ContributionLimit
from schemas.schema import schema
from statement_import.parser import parse_transaction


class BulkTransactionTests(unittest.TestCase):
    def setUp(self):
        disconnect()
        with patch('mongoengine.connection.MongoClient', mongomock.MongoClient):
            connect('bulk_tests', uuidRepresentation='standard')
        for model in (Transaction, Platform, Profile, Account, Currency, Stock, Activity, Asset, ContributionLimit):
            model.drop_collection()
        self.profile = Profile(name='Owner').save()
        self.other = Profile(name='Other').save()
        self.currency = Currency(code='CAD').save()
        self.account = Account(code='TFSA').save()
        self.platform = Platform(name='Broker', profile=self.profile, account=self.account, currency=self.currency).save()
        self.stock = Stock(name='Example', currency=self.currency, asset=Asset(name='Stock').save()).save()
        self.buy = Activity(name='Buy').save()
        self.sell = Activity(name='Sell').save()
        self.contribution = Activity(name='Contribution').save()

    def tearDown(self):
        disconnect()

    def record(self, activity=None, **changes):
        values = dict(platform=self.platform, account=self.account, activity=activity or self.buy,
                      transaction_date=date(2026, 1, 1), stock=self.stock, price=10, shares=10, total=100)
        values.update(changes)
        return Transaction(**values).save()

    def bulk(self, rows):
        return schema.execute('mutation($profile: ID!, $rows: [TransactionInput!]!) { bulkUpdateTransactions(profileId: $profile, transactions: $rows) { results { id success error code warnings { code message } } } }', variable_values={'profile': str(self.profile.id), 'rows': rows})

    def test_mixed_results_save_valid_rows_and_invalidate_once(self):
        buy = self.record()
        sell = self.record(self.sell, shares=2, total=20, transaction_date=date(2026, 1, 2))
        foreign_platform = Platform(name='Foreign', profile=self.other, account=self.account, currency=self.currency).save()
        foreign = self.record(platform=foreign_platform)
        with patch('schemas.mutations.transaction.invalidate_transactions') as invalidate:
            response = self.bulk([{'id': str(buy.id), 'price': '10.123', 'total': '101.23', 'fee': None},
                                  {'id': str(sell.id), 'shares': '11'}, {'id': str(foreign.id), 'total': '99'}])
        self.assertIsNone(response.errors, response.errors)
        results = response.data['bulkUpdateTransactions']['results']
        self.assertEqual([r['success'] for r in results], [True, False, False])
        self.assertEqual(results[1]['code'], 'INSUFFICIENT_SHARES')
        self.assertEqual(buy.reload().price, Decimal('10.123'))
        self.assertEqual(sell.reload().shares, Decimal('2'))
        self.assertEqual(foreign.reload().total, Decimal('100'))
        invalidate.assert_called_once_with()

    def test_duplicate_ids_and_oversized_batches_write_nothing(self):
        row = self.record()
        update = {'id': str(row.id), 'total': '999'}
        for rows in ([], [update, update], [update] * 201, [{'total': '2'}]):
            self.assertTrue(self.bulk(rows).errors)
        self.assertEqual(row.reload().total, Decimal('100'))

    def test_all_failures_do_not_invalidate_cache(self):
        row = self.record()
        with patch('schemas.mutations.transaction.invalidate_transactions') as invalidate:
            response = self.bulk([{'id': str(row.id), 'stock': 'invalid'}])
        self.assertIsNone(response.errors, response.errors)
        self.assertFalse(response.data['bulkUpdateTransactions']['results'][0]['success'])
        invalidate.assert_not_called()

    def test_contribution_warning_is_returned_for_saved_row(self):
        ContributionLimit(profile=self.profile, account=self.account, yearEnd=date(2026, 12, 31), amount=100).save()
        row = self.record(self.contribution, stock=None, shares=None, price=None, total=50)
        response = self.bulk([{'id': str(row.id), 'total': '150'}])
        self.assertIsNone(response.errors, response.errors)
        result = response.data['bulkUpdateTransactions']['results'][0]
        self.assertTrue(result['success'])
        self.assertTrue(result['warnings'])

    def test_price_precision_is_used_before_total_rounding(self):
        response = schema.execute('mutation($profile: ID!, $input: TransactionInput!) { createTransaction(profileId: $profile, transData: $input) { transaction { id price total } } }', variable_values={'profile': str(self.profile.id), 'input': dict(platform=str(self.platform.id), stock=str(self.stock.id), activity=str(self.buy.id), transactionDate='2026-01-01', price='10.123', shares='100')})
        self.assertIsNone(response.errors, response.errors)
        self.assertEqual(Transaction.objects.first().price, Decimal('10.123'))
        self.assertEqual(Transaction.objects.first().total, Decimal('1012.30'))
        status, parsed = parse_transaction('2026-01-01', 'BUY', 'EX - Example: Bought 100 shares at $10.123 per share', Decimal('1012.30'))
        self.assertEqual(parsed['price'], '10.123')
