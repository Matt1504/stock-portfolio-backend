import sys
import unittest
from pathlib import Path
from datetime import date, timedelta
from contextlib import contextmanager
from unittest.mock import patch
from decimal import Decimal
import mongomock
from mongoengine import connect, disconnect
from graphql import GraphQLError
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from models.models import Platform, Profile, Currency, Account, Stock, Asset, Activity, Transaction
from schemas.account_transfer import commit_transfer, preview, ledger
from schemas.schema import schema

class FakeSession:
    """Mongomock has no sessions: snapshot rollback exercises callback failures."""
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def with_transaction(self, callback):
        snapshots = {model: list(model._get_collection().find()) for model in (Transaction, Platform)}
        try: return callback(None)
        except Exception:
            for model, rows in snapshots.items():
                model._get_collection().delete_many({})
                if rows: model._get_collection().insert_many(rows)
            raise

class AccountTransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        disconnect()
        with patch('mongoengine.connection.MongoClient', mongomock.MongoClient):
            connect('transfer_tests', uuidRepresentation='standard')
    @classmethod
    def tearDownClass(cls): disconnect()
    def setUp(self):
        for model in (Transaction, Platform, Profile, Currency, Account, Stock, Asset, Activity): model.drop_collection()
        self.day = date.today() - timedelta(days=10)
        self.profile = Profile(name='Owner').save()
        self.currency = Currency(code='CAD').save()
        self.account = Account(code='TFSA').save()
        self.source = Platform(name='Original', account=self.account, currency=self.currency, profile=self.profile).save()
        self.target = Platform(name='Destination', account=self.account, currency=self.currency, profile=self.profile).save()
        self.stock = Stock(ticker='EX', asset=Asset(name='Stock').save(), currency=self.currency).save()
        self.activities = {name: Activity(name=name).save() for name in ['Buy', 'Sell', 'Contribution', 'Transfer In', 'Transfer Out', 'Stock Split', 'Stock Spinoff', 'Dividends', 'Service Fee', 'GIC Maturity']}
        self.row('Contribution', 1000)
        self.buy = self.row('Buy', 400, shares=4, stock=self.stock)
        self.row('Sell', 150, shares=1, stock=self.stock)
        self.row('Dividends', 5, stock=self.stock)
        self.row('Service Fee', 2)
    def row(self, activity, total, **kwargs):
        return Transaction(platform=self.source, account=self.account, activity=self.activities[activity], transaction_date=self.day, total=total, **kwargs).save()
    def transfer(self, close_original_account=True):
        with patch.object(mongomock.MongoClient, 'start_session', return_value=FakeSession()):
            assets = preview(str(self.profile.id), str(self.source.id), str(self.target.id), self.day, close_original_account).assets
            values = [{'stock_id': p.stock_id, 'market_value': Decimal('500')} for p in assets]
            return commit_transfer(str(self.profile.id), str(self.source.id), str(self.target.id), self.day, close_original_account, values)
    def test_keep_open_uses_dated_balances_and_retains_later_income(self):
        later = self.row('Dividends', Decimal('0.58'), stock=self.stock)
        later.update(set__transaction_date=self.day + timedelta(days=14))
        result = preview(str(self.profile.id), str(self.source.id), str(self.target.id), self.day, False)
        self.assertEqual(result.cash, Decimal('753.00'))
        self.assertEqual(self.transfer(False), 4)
        self.assertIsNone(self.source.reload().closed_at)
        self.assertEqual(self.source.transfer_revision, 1)
        self.assertEqual(later.reload().platform.id, self.source.id)
        from query_loading import materialize_references
        self.assertEqual(ledger(materialize_references(Transaction.objects(platform=self.source))), (Decimal('0.58'), []))
        from schemas.account_transfer import validate_closed_platform
        validate_closed_platform(later)

    def test_keep_open_rejects_transfer_that_invalidates_a_later_sale(self):
        later = self.row('Sell', 100, shares=1, stock=self.stock)
        later.update(set__transaction_date=self.day + timedelta(days=1))
        count = Transaction.objects.count()
        with self.assertRaisesRegex(GraphQLError, 'negative share balance'):
            self.transfer(False)
        self.assertEqual(Transaction.objects.count(), count)
        self.assertIsNone(self.source.reload().closed_at)
        self.assertEqual(self.source.transfer_revision, 0)

    def test_graphql_accepts_keep_open_on_preview_and_transfer(self):
        self.row('Dividends', 1, stock=self.stock).update(set__transaction_date=self.day + timedelta(days=1))
        variables = {'profile': str(self.profile.id), 'source': str(self.source.id), 'target': str(self.target.id), 'day': self.day.isoformat()}
        args = 'profileId: $profile, transFrom: $source, transTo: $target, transferDate: $day, closeOriginalAccount: false'
        signature = '($profile: ID!, $source: ID!, $target: ID!, $day: Date!)'
        result = schema.execute('query' + signature + '{previewAccountTransfer(' + args + '){cash}}', variables=variables)
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(result.data['previewAccountTransfer']['cash'], '753.00')
        with patch.object(mongomock.MongoClient, 'start_session', return_value=FakeSession()):
            result = schema.execute('mutation' + signature + '{transferAccount(' + args + ', marketValues: [{stockId: "' + str(self.stock.id) + '", marketValue: "500"}]){success}}', variables=variables)
        self.assertFalse(result.errors, result.errors)
        self.assertIsNone(self.source.reload().closed_at)

    def test_preserves_history_and_transfers_exact_remaining_balances(self):
        before = list(Transaction.objects(platform=self.source).scalar('id'))
        calculated = preview(str(self.profile.id), str(self.source.id), str(self.target.id), self.day)
        self.assertEqual(calculated.cash, Decimal('753.00'))
        self.assertEqual(calculated.assets[0].shares, Decimal('3'))
        self.assertEqual(calculated.assets[0].book_cost, Decimal('300.00'))
        self.assertEqual(self.transfer(), 4)
        self.assertEqual(self.source.reload().closed_at, self.day)
        self.assertIsNone(self.target.reload().closed_at)
        self.assertEqual(self.buy.reload().platform.id, self.source.id)
        self.assertTrue(set(before).issubset(set(Transaction.objects(platform=self.source).scalar('id'))))
        pairs = Transaction.objects(transfer_pair__ne=None)
        self.assertEqual(pairs.count(), 4)
        self.assertEqual(len(set(pairs.scalar('transfer_pair'))), 2)
        from query_loading import materialize_references
        self.assertEqual(ledger(materialize_references(Transaction.objects(platform=self.source))), (Decimal('0.00'), []))
        cash, assets = ledger(materialize_references(Transaction.objects(platform=self.target)))
        self.assertEqual(cash, Decimal('753.00'))
        self.assertEqual(assets[0]['cost'], Decimal('300.00'))
        with self.assertRaises(GraphQLError): self.transfer()
    def test_graphql_preview_and_mutation_then_closed_platform_validation(self):
        variables = {'profile': str(self.profile.id), 'source': str(self.source.id), 'target': str(self.target.id), 'day': self.day.isoformat()}
        query = 'query($profile: ID!, $source: ID!, $target: ID!, $day: Date!) { previewAccountTransfer(profileId: $profile, transFrom: $source, transTo: $target, transferDate: $day) { cash assets { shares bookCost } } }'
        result = schema.execute(query, variables=variables)
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(result.data['previewAccountTransfer']['cash'], '753.00')
        mutation = 'mutation($profile: ID!, $source: ID!, $target: ID!, $day: Date!) { transferAccount(profileId: $profile, transFrom: $source, transTo: $target, transferDate: $day, marketValues: [{stockId: "' + str(self.stock.id) + '", marketValue: "500"}]) { success } }'
        with patch.object(mongomock.MongoClient, 'start_session', return_value=FakeSession()):
            result = schema.execute(mutation, variables=variables)
        self.assertFalse(result.errors, result.errors)
        self.assertTrue(result.data['transferAccount']['success'])
        data = {'platform': str(self.source.id), 'activity': str(self.activities['Contribution'].id), 'total': '10', 'transactionDate': (self.day + timedelta(days=1)).isoformat()}
        result = schema.execute('mutation($profile: ID!, $row: TransactionInput!) { createTransaction(profileId: $profile, transData: $row) { transaction { id } } }', variables={'profile': str(self.profile.id), 'row': data})
        self.assertTrue(result.errors)
        self.assertIn('closed on', result.errors[0].message)
        linked = Transaction.objects(transfer_batch__ne=None).first()
        result = schema.execute('mutation($profile: ID!, $id: ID!) { deleteTransaction(profileId: $profile, id: $id) { success } }', variables={'profile': str(self.profile.id), 'id': str(linked.id)})
        self.assertTrue(result.errors)
        self.assertIn('linked account transfer', result.errors[0].message)

    def test_atomic_failure_does_not_move_history_or_close_source(self):
        count = Transaction.objects.count()
        with patch.object(mongomock.collection.Collection, 'update_one', side_effect=RuntimeError('write failed')):
            with self.assertRaises(GraphQLError): self.transfer()
        self.assertEqual(Transaction.objects.count(), count)
        self.assertIsNone(self.source.reload().closed_at)
        self.assertEqual(self.buy.reload().platform.id, self.source.id)
    def test_unsupported_database_fails_without_writing(self):
        count = Transaction.objects.count()
        with self.assertRaises(GraphQLError): commit_transfer(str(self.profile.id), str(self.source.id), str(self.target.id), self.day, market_values=[{'stock_id': str(self.stock.id), 'market_value': 500}])
        self.assertEqual(Transaction.objects.count(), count)
        self.assertIsNone(self.source.reload().closed_at)
    def test_closed_date_guard_and_linked_rows_are_protected(self):
        self.transfer()
        from schemas.account_transfer import validate_closed_platform, protect_transfer
        old = self.buy.reload()
        validate_closed_platform(old)
        old.transaction_date = self.day + timedelta(days=1)
        with self.assertRaises(GraphQLError): validate_closed_platform(old)
        with self.assertRaises(GraphQLError): protect_transfer(Transaction.objects(transfer_pair__ne=None).first())
    def test_rejects_wrong_profile_currency_and_future_source_transactions(self):
        other = Profile(name='Other').save()
        with self.assertRaises(GraphQLError): preview(str(other.id), str(self.source.id), str(self.target.id), self.day)
        self.target.currency = Currency(code='USD').save(); self.target.save()
        with self.assertRaises(GraphQLError): self.transfer()
        self.target.currency = self.currency; self.target.save()
        self.row('Dividends', 1, stock=self.stock).update(set__transaction_date=self.day + timedelta(days=1))
        with self.assertRaises(GraphQLError): self.transfer()
    def test_split_and_spinoff_basis_is_carried(self):
        child = Stock(ticker='CHILD', asset=self.stock.asset, currency=self.currency).save()
        self.row('Stock Split', 0, stock=self.stock, shares=3)
        self.row('Stock Spinoff', 0, stock=child, shares=1, spinoff_source=self.stock, allocated_book_cost=50)
        result = preview(str(self.profile.id), str(self.source.id), str(self.target.id), self.day)
        self.assertEqual([(p.shares, p.book_cost) for p in result.assets], [(Decimal(6), Decimal('250')), (Decimal(1), Decimal('50'))])
    def test_amount_only_fund_transfers_basis_without_affecting_cash(self):
        fund = Stock(ticker='FUND', asset=Asset(name='Mutual Fund').save(), currency=self.currency).save()
        self.row('Buy', 200, stock=fund)
        self.assertEqual(self.transfer(), 6)
        from query_loading import materialize_references
        cash, positions = ledger(materialize_references(Transaction.objects(platform=self.target)))
        self.assertEqual(cash, Decimal('553'))
        self.assertEqual(next(p for p in positions if p['stock'].id == fund.id)['cost'], Decimal('200'))
    def test_open_gic_and_unreconciled_cash_block_transfer(self):
        gic = Stock(ticker='GIC', asset=Asset(name='GIC').save(), currency=self.currency).save()
        buy = self.row('Buy', 100, stock=gic)
        with self.assertRaisesRegex(GraphQLError, 'Open GIC'): self.transfer()
        buy.delete()
        self.row('Service Fee', 1000)
        with self.assertRaisesRegex(GraphQLError, 'negative'): self.transfer()

    def test_requires_exact_finite_asset_values_and_stores_both_legs(self):
        for values in (None, [], [{'stock_id': str(self.stock.id), 'market_value': -1}],
                       [{'stock_id': str(self.stock.id), 'market_value': 'NaN'}],
                       [{'stock_id': str(self.stock.id), 'market_value': 500}] * 2):
            with self.assertRaises(GraphQLError):
                commit_transfer(str(self.profile.id), str(self.source.id), str(self.target.id), self.day, market_values=values)
        self.transfer()
        assets = Transaction.objects(transfer_batch__ne=None, stock=self.stock)
        self.assertEqual(assets.count(), 2)
        for row in assets:
            self.assertEqual(row.transfer_market_value, Decimal('500'))
            self.assertEqual(row.transfer_market_value_source, 'manual')
            self.assertEqual(row.total, Decimal('300'))
        for row in Transaction.objects(transfer_batch__ne=None, stock=None):
            self.assertIsNone(row.transfer_market_value)

if __name__ == '__main__': unittest.main()
