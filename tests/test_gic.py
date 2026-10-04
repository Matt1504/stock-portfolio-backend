import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
import mongomock
from mongoengine import connect, disconnect, NotUniqueError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from models.models import Transaction, Platform, Profile, Account, Currency, Stock, Asset, Activity
from schemas.schema import schema
from add_gic_maturity_activity import add_gic_maturity_activity


class GicTests(unittest.TestCase):
    def setUp(self):
        disconnect()
        with patch('mongoengine.connection.MongoClient', mongomock.MongoClient):
            connect('gic_tests', uuidRepresentation='standard')
        for model in (Transaction, Platform, Profile, Account, Currency, Stock, Asset, Activity):
            model.drop_collection()
        self.owner = Profile(name='Owner').save()
        self.other = Profile(name='Other').save()
        self.account = Account(code='TFSA').save()
        currency = Currency(code='CAD').save()
        self.platform = Platform(name='Broker', profile=self.owner, account=self.account, currency=currency).save()
        self.second = Platform(name='Second', profile=self.owner, account=self.account, currency=currency).save()
        self.foreign = Platform(name='Foreign', profile=self.other, account=self.account, currency=currency).save()
        self.stock = Stock(name='GIC', asset=Asset(name='GIC').save()).save()
        self.activities = {name: Activity(name=name).save() for name in ('Buy', 'GIC Maturity', 'Withholding Tax', 'Interest', 'Dividends', 'Sell', 'Stock Split')}

    def tearDown(self):
        disconnect()

    def create(self, activity, **changes):
        data = {'platform': str(self.platform.id), 'stock': str(self.stock.id), 'activity': str(self.activities[activity].id), 'transactionDate': '2027-01-01', 'total': '10400'}
        data.update(changes)
        query = 'mutation($profile: ID!, $input: TransactionInput!) { createTransaction(profileId: $profile, transData: $input) { transaction { id total principalReturned interestEarned expectedMaturityTotal gicPurchase { id } } warnings { code message } } }'
        return schema.execute(query, variable_values={'profile': str(self.owner.id), 'input': data})

    def buy(self, **changes):
        data = {'transactionDate': '2026-01-01', 'maturityDate': '2027-01-01', 'rate': '4', 'total': '10000'}
        data.update(changes)
        result = self.create('Buy', **data)
        self.assertIsNone(result.errors)
        return Transaction.objects.get(id=result.data['createTransaction']['transaction']['id'])

    def update(self, record, **changes):
        query = 'mutation($profile: ID!, $input: TransactionInput!) { updateTransaction(profileId: $profile, transData: $input) { trans { id total principalReturned interestEarned } warnings { code message } } }'
        return schema.execute(query, variable_values={'profile': str(self.owner.id), 'input': {'id': str(record.id), **changes}})

    def test_maturity_closes_one_purchase_and_derives_income(self):
        purchase = self.buy()
        result = self.create('GIC Maturity', gicPurchase=str(purchase.id))
        self.assertIsNone(result.errors)
        row = result.data['createTransaction']['transaction']
        self.assertEqual(row['principalReturned'], 10000)
        self.assertEqual(row['interestEarned'], 400)
        self.assertEqual(row['gicPurchase']['id'], str(purchase.id))
        self.assertEqual(result.data['createTransaction']['warnings'], [])
        duplicate = self.create('GIC Maturity', gicPurchase=str(purchase.id))
        self.assertEqual(duplicate.errors[0].extensions['code'], 'GIC_ALREADY_MATURED')
        settled = Transaction.objects.get(id=row['id'])
        with self.assertRaises(NotUniqueError):
            Transaction(platform=self.platform, activity=self.activities['GIC Maturity'], gic_purchase=purchase).save()
        updated = self.update(settled, total='10500')
        self.assertIsNone(updated.errors)
        self.assertEqual(updated.data['updateTransaction']['trans']['interestEarned'], 500)
        self.assertEqual(updated.data['updateTransaction']['warnings'][0]['code'], 'GIC_PAYOUT_DIFFERENCE')

    def test_purchase_terms_and_non_share_fields_are_required(self):
        for changes in ({'total': '0'}, {'rate': None}, {'rate': '-1'}, {'rate': '101'}, {'maturityDate': None}, {'maturityDate': '2025-12-31'}, {'shares': '1'}, {'price': '1'}):
            with self.subTest(changes=changes):
                result = self.create('Buy', transactionDate='2026-01-01', **changes)
                self.assertTrue(result.errors)
        self.assertEqual(Transaction.objects.count(), 0)

    def test_maturity_rejects_missing_foreign_and_mismatched_purchases(self):
        purchase = self.buy()
        self.assertTrue(self.create('GIC Maturity').errors)
        self.assertTrue(self.create('GIC Maturity', gicPurchase='000000000000000000000001').errors)
        self.assertTrue(self.create('GIC Maturity', gicPurchase=str(purchase.id), platform=str(self.second.id)).errors)
        purchase.platform = self.foreign
        purchase.save()
        self.assertTrue(self.create('GIC Maturity', gicPurchase=str(purchase.id)).errors)
        purchase.platform = self.platform
        purchase.stock = Stock(name='Not GIC', asset=Asset(name='Stock').save()).save()
        purchase.save()
        self.assertTrue(self.create('GIC Maturity', gicPurchase=str(purchase.id)).errors)

    def test_dates_principal_and_early_difference_warnings(self):
        purchase = self.buy()
        self.assertTrue(self.create('GIC Maturity', gicPurchase=str(purchase.id), transactionDate='2025-12-31').errors)
        self.assertTrue(self.create('GIC Maturity', gicPurchase=str(purchase.id), total='9999').errors)
        result = self.create('GIC Maturity', gicPurchase=str(purchase.id), transactionDate='2026-06-01', total='10100')
        self.assertIsNone(result.errors)
        self.assertEqual({warning['code'] for warning in result.data['createTransaction']['warnings']}, {'EARLY_GIC_MATURITY', 'GIC_PAYOUT_DIFFERENCE'})
        self.assertEqual(result.data['createTransaction']['transaction']['interestEarned'], 100)

    def test_simple_and_compound_estimates_and_zero_rate(self):
        purchase = self.buy(maturityDate='2028-01-01', interestCalculation='annual_compound')
        result = self.create('GIC Maturity', gicPurchase=str(purchase.id), transactionDate='2028-01-01', total='10816')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['createTransaction']['warnings'], [])
        zero = self.buy(rate='0')
        result = self.create('GIC Maturity', gicPurchase=str(zero.id), total='10000')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['createTransaction']['transaction']['interestEarned'], 0)

    def test_settled_purchase_edits_and_deletion_keep_links_consistent(self):
        purchase = self.buy()
        result = self.create('GIC Maturity', gicPurchase=str(purchase.id))
        maturity = Transaction.objects.get(id=result.data['createTransaction']['transaction']['id'])
        for changes in ({'total': '9000'}, {'platform': str(self.second.id)}, {'transactionDate': '2028-01-01'}, {'activity': str(self.activities['Sell'].id)}):
            self.assertTrue(self.update(purchase, **changes).errors)
        self.assertEqual(purchase.reload().total, Decimal('10000'))
        result = self.update(purchase, rate='5')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['updateTransaction']['warnings'][0]['code'], 'GIC_PAYOUT_DIFFERENCE')
        query = 'mutation($profile: ID!, $id: ID!) { deleteTransaction(profileId: $profile, id: $id) { success } }'
        variables = {'profile': str(self.owner.id), 'id': str(purchase.id)}
        self.assertTrue(schema.execute(query, variable_values=variables).errors)
        variables['id'] = str(maturity.id)
        self.assertTrue(schema.execute(query, variable_values=variables).data['deleteTransaction']['success'])
        variables['id'] = str(purchase.id)
        self.assertTrue(schema.execute(query, variable_values=variables).data['deleteTransaction']['success'])

    def test_outstanding_purchases_are_scoped_and_include_terms(self):
        first, second = self.buy(), self.buy()
        self.create('GIC Maturity', gicPurchase=str(first.id))
        query = 'query($profile: ID!, $platform: ID!) { outstandingGicPurchases(profileId: $profile, platform: $platform) { id expectedMaturityTotal } }'
        result = schema.execute(query, variable_values={'profile': str(self.owner.id), 'platform': str(self.platform.id)})
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['outstandingGicPurchases'], [{'id': str(second.id), 'expectedMaturityTotal': 10400}])
        self.assertTrue(schema.execute(query, variable_values={'profile': str(self.other.id), 'platform': str(self.platform.id)}).errors)

    def test_gic_invalid_activities_and_post_maturity_tax(self):
        for activity in ('Sell', 'Dividends', 'Interest', 'Stock Split'):
            self.assertTrue(self.create(activity).errors)
        purchase = self.buy()
        self.create('GIC Maturity', gicPurchase=str(purchase.id))
        self.assertIsNone(self.create('Withholding Tax', total='10').errors)
        self.assertIsNone(self.create('Interest', stock=None, total='10').errors)

    def test_setup_is_non_destructive_and_idempotent(self):
        purchase = self.buy()
        self.activities['GIC Maturity'].delete()
        self.assertIsNone(add_gic_maturity_activity())
        first = add_gic_maturity_activity(True)
        self.assertEqual(add_gic_maturity_activity(True).id, first.id)
        self.assertEqual(Transaction.objects.get(id=purchase.id).total, Decimal('10000'))
