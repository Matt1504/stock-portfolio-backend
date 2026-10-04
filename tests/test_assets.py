import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import mongomock
from mongoengine import connect, disconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from models.asset import Asset
from models.stock import Stock
from schemas.schema import schema
from migrate_assets import migrate_assets, ASSET_NAMES


class AssetTests(unittest.TestCase):
    def setUp(self):
        disconnect()
        with patch('mongoengine.connection.MongoClient', mongomock.MongoClient):
            connect('portfolio_asset_tests', uuidRepresentation='standard')
        Asset.drop_collection()
        Stock.drop_collection()

    def tearDown(self):
        disconnect()

    def test_migration_is_dry_by_default_and_preserves_classifications(self):
        stock = Stock(name='Example').save()
        gic = Asset(name='GIC').save()
        classified = Stock(name='Deposit', asset=gic).save()
        self.assertEqual(migrate_assets()['unclassified_stocks'], 1)
        self.assertIsNone(stock.reload().asset)
        self.assertEqual(Asset.objects.count(), 1)
        migrate_assets(apply=True)
        self.assertEqual(stock.reload().asset.name, 'Stock')
        self.assertEqual(classified.reload().asset.id, gic.id)
        self.assertEqual(set(Asset.objects.scalar('name')), set(ASSET_NAMES))
        self.assertIn('asset_id', Stock._get_collection().find_one({'_id': stock.id}))
        self.assertEqual(migrate_assets(apply=True)['unclassified_stocks'], 0)
        self.assertEqual(Asset.objects.count(), 4)

    def test_graphql_reads_creates_and_updates_asset_references(self):
        migrate_assets(apply=True)
        gic = Asset.objects.get(name='GIC')
        query = 'mutation($stock: StockInput!) { createStock(stockData: $stock) { stock { id asset { id name } } } }'
        result = schema.execute(query, variable_values={'stock': {'name': 'Deposit', 'assetId': str(gic.id)}})
        self.assertIsNone(result.errors)
        saved = result.data['createStock']['stock']
        self.assertEqual(saved['asset']['name'], 'GIC')
        stock_asset = Asset.objects.get(name='Stock')
        result = schema.execute('mutation($stock: StockInput!) { updateStock(stockData: $stock) { stock { asset { name } } } }', variable_values={'stock': {'id': saved['id'], 'assetId': str(stock_asset.id)}})
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['updateStock']['stock']['asset']['name'], 'Stock')
        result = schema.execute('{ assets { edges { node { name } } } }')
        self.assertIsNone(result.errors)
        self.assertEqual(len(result.data['assets']['edges']), 4)
        result = schema.execute(query, variable_values={'stock': {'name': 'Default'}})
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['createStock']['stock']['asset']['name'], 'Stock')

    def test_invalid_asset_does_not_create_stock(self):
        result = schema.execute('mutation($stock: StockInput!) { createStock(stockData: $stock) { stock { id } } }', variable_values={'stock': {'name': 'Invalid', 'assetId': '000000000000000000000001'}})
        self.assertTrue(result.errors)
        self.assertEqual(Stock.objects.count(), 0)

    def test_combined_fund_is_renamed_preserving_references_and_is_idempotent(self):
        legacy = Asset(name='Index/Mutual Fund').save()
        stock = Stock(name='Fund', asset=legacy).save()
        self.assertEqual(migrate_assets()['legacy_fund_stocks'], 1)
        self.assertEqual(stock.reload().asset.name, 'Index/Mutual Fund')
        migrate_assets(apply=True)
        self.assertEqual(stock.reload().asset.id, legacy.id)
        self.assertEqual(stock.asset.name, 'Index Fund')
        self.assertEqual(set(Asset.objects.scalar('name')), set(ASSET_NAMES))
        migrate_assets(apply=True)
        self.assertEqual(Asset.objects.count(), 4)

    def test_combined_fund_merges_into_existing_index_without_changing_mutual_funds(self):
        index = Asset(name='Index Fund').save()
        mutual = Asset(name='Mutual Fund').save()
        legacy = Asset(name='Index/Mutual Fund').save()
        stock = Stock(name='Old Fund', asset=legacy).save()
        preserved = Stock(name='Mutual', asset=mutual).save()
        migrate_assets(apply=True)
        self.assertEqual(stock.reload().asset.id, index.id)
        self.assertEqual(preserved.reload().asset.id, mutual.id)
        self.assertEqual(set(Asset.objects.scalar('name')), set(ASSET_NAMES))
