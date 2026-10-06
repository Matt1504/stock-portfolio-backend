import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import mongomock
from mongoengine import connect, disconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from models.models import Account, Activity, Asset, Currency, Platform, Profile, Stock, Transaction
from query_loading import materialize_references
from schemas.schema import schema


class QueryLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        disconnect()
        with patch('mongoengine.connection.MongoClient', mongomock.MongoClient):
            connect('query_loading_tests', uuidRepresentation='standard')

    @classmethod
    def tearDownClass(cls):
        disconnect()

    def setUp(self):
        for model in (Transaction, Stock, Platform, Profile, Account, Activity, Asset, Currency):
            model.drop_collection()
        self.profile = Profile(name='Owner').save()
        self.account = Account(code='RRSP').save()
        self.currency = Currency(code='CAD').save()
        self.asset = Asset(name='Stock').save()
        self.stock = Stock(ticker='EX', asset=self.asset, currency=self.currency).save()
        self.platform = Platform(name='Broker', account=self.account, currency=self.currency, profile=self.profile).save()
        self.buy = Activity(name='Buy').save()
        for i in range(120):
            Transaction(stock=self.stock, platform=self.platform, account=self.account, activity=self.buy,
                        transaction_date=date(2026, 1, 1), price=10, shares=1, total=10).save()
        self.query = '''query($profile: ID!, $stock: ID!) {
          rows: transactionsByStock(profileId: $profile, stock: $stock) {
            id total shares activity { name } account { id code }
            stock { id ticker asset { id name } currency { id code } }
            platform { id name account { id code } currency { id code } }
            priceCurrency { id code } totalCurrency { id code } exchangeRate
          }
        }'''

    def execute(self):
        result = schema.execute(self.query, variables={'profile': str(self.profile.id), 'stock': str(self.stock.id)})
        self.assertFalse(result.errors, result.errors)
        return result.data

    def test_search_pages_are_bounded_and_exhaust_equal_date_rows(self):
        from schemas.transaction_search import search_transactions
        first = search_transactions(str(self.profile.id), stock=str(self.stock.id), first=100)
        self.assertEqual(len(first.transactions), 100)
        self.assertTrue(first.next_cursor)
        second = search_transactions(str(self.profile.id), stock=str(self.stock.id), after=first.next_cursor)
        self.assertEqual(len(second.transactions), 20)
        self.assertIsNone(second.next_cursor)
        self.assertEqual(len({row.id for row in first.transactions + second.transactions}), 120)

    def test_search_profile_filters_and_invalid_inputs(self):
        from schemas.transaction_search import search_transactions
        from graphql import GraphQLError
        other = Profile(name='Other').save()
        self.assertEqual(search_transactions(str(other.id)).transactions, [])
        self.assertEqual(search_transactions(str(self.profile.id), start_date=date(2027, 1, 1)).transactions, [])
        self.assertEqual(len(search_transactions(str(self.profile.id), account=str(self.account.id), platform=str(self.platform.id), currency=str(self.currency.id), activity=str(self.buy.id), stock=str(self.stock.id), first=10).transactions), 10)
        with self.assertRaises(GraphQLError):
            search_transactions(str(other.id), platform=str(self.platform.id))
        for arguments in ({'first': 101}, {'after': 'bad'}, {'account': 'bad'}, {'start_date': date(2027, 1, 1), 'end_date': date(2026, 1, 1)}):
            with self.assertRaises(GraphQLError):
                search_transactions(str(self.profile.id), **arguments)

    def test_large_graphql_response_matches_lazy_loading_with_bounded_reads(self):
        with patch('schemas.schema.materialize_references', lambda queryset: queryset):
            expected = self.execute()
        original_find = mongomock.collection.Collection.find
        finds = []
        def count_find(collection, *args, **kwargs):
            finds.append(collection.name)
            return original_find(collection, *args, **kwargs)
        with patch.object(mongomock.collection.Collection, 'find', count_find):
            actual = self.execute()
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual['rows']), 120)
        # Includes ownership checks, the ledger, and nested references. Lazy
        # resolution would perform hundreds of reads for the same fixtures.
        self.assertLessEqual(len(finds), 15, finds)

    def test_nested_gic_purchase_and_source_stock_are_materialized(self):
        gic_asset = Asset(name='GIC').save()
        gic = Stock(ticker='GIC', asset=gic_asset, currency=self.currency).save()
        purchase = Transaction(stock=gic, platform=self.platform, account=self.account, activity=self.buy,
                               total=1000, rate=4, transaction_date=date(2026, 1, 1), maturity_date=date(2027, 1, 1)).save()
        maturity = Transaction(stock=gic, spinoff_source=self.stock, platform=self.platform,
                               account=self.account, activity=Activity(name='GIC Maturity').save(),
                               gic_purchase=purchase, total=1040, transaction_date=date(2027, 1, 1)).save()
        rows = materialize_references(Transaction.objects(id=maturity.id))
        # Any lazy reads after materialization would hit find.
        with patch.object(mongomock.collection.Collection, 'find', side_effect=AssertionError('Unexpected lazy read')):
            self.assertEqual(rows[0].gic_purchase.stock.asset.name, 'GIC')
            self.assertEqual(rows[0].gic_purchase.platform.currency.code, 'CAD')
            self.assertEqual(rows[0].spinoff_source.asset.name, 'Stock')
            self.assertEqual(rows[0].platform.profile.name, 'Owner')

    def test_shared_references_reuse_one_instance_and_preserve_order(self):
        rows = materialize_references(Transaction.objects.order_by('-id'))
        self.assertIs(rows[0].stock, rows[1].stock)
        self.assertIs(rows[0].platform, rows[1].platform)
        self.assertEqual([row.id for row in rows], sorted((row.id for row in rows), reverse=True))
        self.assertEqual(materialize_references(Transaction.objects(id__in=[])), [])

    def test_stock_metadata_batches_references_and_preserves_pagination(self):
        for i in range(24):
            Stock(ticker='STOCK{}'.format(i), asset=self.asset, currency=self.currency).save()
        original_find = mongomock.collection.Collection.find
        finds = []
        def count_find(collection, *args, **kwargs):
            finds.append(collection.name)
            return original_find(collection, *args, **kwargs)
        query = '''{ stocks(first: 5) { edges { node { id asset { name } currency { code } } }
                    pageInfo { hasNextPage endCursor } } }'''
        with patch.object(mongomock.collection.Collection, 'find', count_find):
            result = schema.execute(query)
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(len(result.data['stocks']['edges']), 5)
        self.assertTrue(result.data['stocks']['pageInfo']['hasNextPage'])
        self.assertLessEqual(len(finds), 5, finds)

    def test_dangling_reference_is_not_silently_replaced_with_null(self):
        Stock._get_collection().delete_one({'_id': self.stock.id})
        row = materialize_references(Transaction.objects)[0]
        from mongoengine import DoesNotExist
        with self.assertRaises(DoesNotExist):
            _ = row.stock

    def test_cyclic_references_terminate_and_reuse_root_document(self):
        first, second = list(Transaction.objects.limit(2))
        Transaction._get_collection().update_one({'_id': first.id}, {'$set': {'gic_purchase': second.id}})
        Transaction._get_collection().update_one({'_id': second.id}, {'$set': {'gic_purchase': first.id}})
        root = materialize_references(Transaction.objects(id=first.id))[0]
        self.assertIs(root.gic_purchase.gic_purchase, root)


if __name__ == '__main__':
    unittest.main()
