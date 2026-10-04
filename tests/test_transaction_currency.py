import sys
import unittest
from pathlib import Path
from datetime import date
from decimal import Decimal
from unittest.mock import patch
import mongomock
from mongoengine import connect, disconnect
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from models.models import Transaction, Platform, Profile, Account, Currency, Stock, Activity, Asset
from schemas.schema import schema
from migrate_transaction_currencies import migrate_transaction_currencies
from remove_transaction_descriptions import remove_transaction_descriptions


class TransactionCurrencyTests(unittest.TestCase):
    def setUp(self):
        disconnect()
        with patch('mongoengine.connection.MongoClient', mongomock.MongoClient):
            connect('fx_tests', uuidRepresentation='standard')
        for model in (Transaction, Platform, Profile, Account, Currency, Stock, Activity, Asset):
            model.drop_collection()
        self.profile = Profile(name='Owner').save()
        self.cad = Currency(code='CAD').save()
        self.usd = Currency(code='USD').save()
        account = Account(code='NRSA', has_contribution_limit=False).save()
        self.platform = Platform(name='Wealthsimple', account=account, profile=self.profile, currency=self.cad).save()
        self.stock = Stock(name='ACWV', currency=self.usd, asset=Asset(name='Stock').save()).save()
        self.buy = Activity(name='Buy').save()
        self.sell = Activity(name='Sell').save()

    def tearDown(self):
        disconnect()

    def create(self, **changes):
        values = dict(platform=str(self.platform.id), stock=str(self.stock.id), activity=str(self.buy.id), transactionDate='2026-10-01', price='116.56', shares='30.7698', exchangeRate='1.390618', priceCurrency=str(self.usd.id), totalCurrency=str(self.cad.id))
        values.update(changes)
        return schema.execute('mutation($profile: ID!, $input: TransactionInput!) { createTransaction(profileId: $profile, transData: $input) { transaction { id total exchangeRate priceCurrency { code } totalCurrency { code } } warnings { code message } } }', variable_values={'profile': str(self.profile.id), 'input': values})

    def test_descriptions_are_not_saved_by_create_or_update(self):
        result = self.create(description='Unused CSV text')
        self.assertIsNone(result.errors, result.errors)
        transaction = Transaction.objects.first()
        self.assertNotIn('description', Transaction._get_collection().find_one({'_id': transaction.id}))
        Transaction._get_collection().update_one({'_id': transaction.id}, {'$set': {'description': 'Legacy text'}})
        updated = schema.execute('mutation($profile: ID!, $input: TransactionInput!) { updateTransaction(profileId: $profile, transData: $input) { trans { id } } }', variable_values={'profile': str(self.profile.id), 'input': {'id': str(transaction.id), 'description': 'Replacement'}})
        self.assertIsNone(updated.errors, updated.errors)
        self.assertNotIn('description', Transaction._get_collection().find_one({'_id': transaction.id}))

    def test_description_cleanup_is_idempotent_and_preserves_other_fields(self):
        result = self.create()
        self.assertIsNone(result.errors)
        collection = Transaction._get_collection()
        collection.update_many({}, {'$set': {'description': 'Legacy text'}})
        before = list(collection.find({}, {'description': 0}))
        self.assertEqual(remove_transaction_descriptions(), {'matched': 1, 'modified': 0, 'remaining': 1})
        self.assertEqual(remove_transaction_descriptions(True), {'matched': 1, 'modified': 1, 'remaining': 0})
        self.assertEqual(list(collection.find()), before)
        self.assertEqual(remove_transaction_descriptions(True), {'matched': 0, 'modified': 0, 'remaining': 0})

    def test_cross_currency_buy_calculates_and_preserves_manual_override(self):
        result = self.create()
        self.assertIsNone(result.errors)
        value = result.data['createTransaction']['transaction']
        self.assertEqual(float(value['total']), 4987.49)
        self.assertEqual(value['priceCurrency']['code'], 'USD')
        self.assertEqual(value['totalCurrency']['code'], 'CAD')
        self.assertEqual(value['exchangeRate'], 1.390618)
        self.assertEqual(result.data['createTransaction']['warnings'], [])
        override = self.create(total='4978.49')
        self.assertIsNone(override.errors)
        self.assertEqual(float(override.data['createTransaction']['transaction']['total']), 4978.49)
        self.assertEqual(override.data['createTransaction']['warnings'][0]['code'], 'TRANSACTION_TOTAL_DIFFERENCE')

    def test_invalid_exchange_rates_and_total_currency_reject_without_save(self):
        for values in [dict(exchangeRate=None), dict(exchangeRate='0'), dict(exchangeRate='-1'), dict(exchangeRate='NaN'), dict(totalCurrency=str(self.usd.id))]:
            with self.subTest(values=values):
                result = self.create(**values)
                self.assertTrue(result.errors)
                self.assertEqual(Transaction.objects.count(), 0)
        self.assertTrue(self.create(priceCurrency=str(self.cad.id), exchangeRate='1.1').errors)

    def test_sell_converts_proceeds_then_deducts_account_currency_fees(self):
        self.assertIsNone(self.create().errors)
        result = self.create(activity=str(self.sell.id), shares='1', price='120', exchangeRate='1.4', fee='2')
        self.assertIsNone(result.errors)
        self.assertEqual(float(result.data['createTransaction']['transaction']['total']), 166)

    def test_legacy_currency_fallbacks_and_metadata_migration_preserve_amounts(self):
        tx = Transaction(platform=self.platform, stock=self.stock, activity=self.buy, account=self.platform.account, shares=1, price=100, total=101, fee=1, transaction_date=date(2025, 1, 1)).save()
        result = schema.execute('query($profile: ID!, $stock: ID!) { transactionsByStock(profileId: $profile, stock: $stock) { priceCurrency { code } totalCurrency { code } exchangeRate } }', variable_values={'profile': str(self.profile.id), 'stock': str(self.stock.id)})
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['transactionsByStock'][0], {'priceCurrency': {'code': 'CAD'}, 'totalCurrency': {'code': 'CAD'}, 'exchangeRate': 1})
        self.assertEqual(migrate_transaction_currencies()['fields_to_update'], 3)
        self.assertIsNone(tx.reload().price_currency)
        with patch('migrate_transaction_currencies.invalidate_transactions'):
            self.assertEqual(migrate_transaction_currencies(True)['fields_updated'], 3)
            self.assertEqual(migrate_transaction_currencies(True)['fields_updated'], 0)
        self.assertEqual(tx.reload().total, Decimal('101'))
        self.assertEqual(tx.price_currency.id, self.cad.id)
        self.assertEqual(tx.exchange_rate, Decimal(1))
        self.assertIsNone(self.create().errors)
        with patch('migrate_transaction_currencies.invalidate_transactions'):
            self.assertEqual(migrate_transaction_currencies(True)['fields_updated'], 0)
        self.assertEqual(Transaction.objects(stock=self.stock, price_currency=self.usd).count(), 1)

    def test_share_based_funds_validate_ownership_and_reject_mixed_tracking(self):
        self.stock.asset = Asset(name='Index Fund').save()
        self.stock.save()
        self.assertIsNone(self.create().errors)
        oversold = self.create(activity=str(self.sell.id), shares='40')
        self.assertTrue(oversold.errors)
        self.assertEqual(oversold.errors[0].extensions['code'], 'INSUFFICIENT_SHARES')
        mixed = self.create(shares='0', price=None)
        self.assertTrue(mixed.errors)
        self.assertEqual(mixed.errors[0].extensions['code'], 'MIXED_FUND_TRACKING')

    def test_service_fee_seed_and_account_only_create_update(self):
        from add_service_fee_activity import add_service_fee_activity
        self.assertIsNone(add_service_fee_activity())
        service = add_service_fee_activity(True)
        self.assertEqual(add_service_fee_activity(True).id, service.id)
        invalid = self.create(activity=str(service.id))
        self.assertIn('Service Fee transactions cannot have a stock', str(invalid.errors[0]))
        result = self.create(activity=str(service.id), stock=None, price=None, shares=None, exchangeRate='1', priceCurrency=str(self.cad.id), total='12.50')
        self.assertIsNone(result.errors)
        saved = result.data['createTransaction']['transaction']
        self.assertEqual(float(saved['total']), 12.5)
        self.assertEqual(saved['totalCurrency']['code'], 'CAD')
        self.assertIsNone(Transaction.objects.get(id=saved['id']).stock)
        update = schema.execute('mutation($profile: ID!, $input: TransactionInput!) { updateTransaction(profileId: $profile, transData: $input) { trans { id } } }', variable_values={'profile': str(self.profile.id), 'input': {'id':saved['id'], 'stock':str(self.stock.id)}})
        self.assertIn('Service Fee transactions cannot have a stock', str(update.errors[0]))

    def test_sec_fee_seed_and_usd_only_account_fee_validation(self):
        from add_sec_fee_activity import add_sec_fee_activity
        self.assertIsNone(add_sec_fee_activity())
        sec = add_sec_fee_activity(True)
        self.assertEqual(add_sec_fee_activity(True).id, sec.id)
        usd_platform = Platform(name='USD Broker', account=self.platform.account, profile=self.profile, currency=self.usd).save()
        values = dict(activity=str(sec.id), platform=str(usd_platform.id), stock=None, price=None, shares=None,
                      exchangeRate='1', priceCurrency=str(self.usd.id), totalCurrency=str(self.usd.id), total='0.03')
        cad = self.create(**dict(values, platform=str(self.platform.id)))
        self.assertEqual(cad.errors[0].extensions['code'], 'INVALID_SEC_FEE_CURRENCY')
        self.assertTrue(self.create(**dict(values, stock=str(self.stock.id))).errors)
        for amount in ('0', '-1', 'NaN'):
            self.assertTrue(self.create(**dict(values, total=amount)).errors)
        self.assertEqual(Transaction.objects.count(), 0)
        saved = self.create(**values)
        self.assertIsNone(saved.errors, saved.errors)
        fee = Transaction.objects.first()
        self.assertIsNone(fee.stock)
        self.assertEqual(fee.total, Decimal('0.03'))
        update_query = 'mutation($p: ID!, $t: TransactionInput!) { updateTransaction(profileId: $p, transData: $t) { trans { id total } } }'
        bad = schema.execute(update_query, variable_values={'p': str(self.profile.id), 't': {'id': str(fee.id), 'platform': str(self.platform.id), 'total': '0.04'}})
        self.assertEqual(bad.errors[0].extensions['code'], 'INVALID_SEC_FEE_CURRENCY')
        self.assertEqual(fee.reload().platform.id, usd_platform.id)
        good = schema.execute(update_query, variable_values={'p': str(self.profile.id), 't': {'id': str(fee.id), 'total': '0.04'}})
        self.assertIsNone(good.errors, good.errors)
        self.assertEqual(fee.reload().total, Decimal('0.04'))
        bulk = schema.execute('mutation($p: ID!, $rows: [TransactionInput!]!) { bulkUpdateTransactions(profileId: $p, transactions: $rows) { results { success code } } }', variable_values={'p': str(self.profile.id), 'rows': [{'id': str(fee.id), 'platform': str(self.platform.id), 'total': '0.05'}]})
        self.assertIsNone(bulk.errors, bulk.errors)
        self.assertEqual(bulk.data['bulkUpdateTransactions']['results'], [{'success': False, 'code': 'INVALID_SEC_FEE_CURRENCY'}])
        self.assertEqual(fee.reload().total, Decimal('0.04'))

    def test_total_warning_tolerance_is_inclusive_and_symmetric_for_buy_sell_and_fx(self):
        self.assertIsNone(self.create(shares='100').errors)
        for currency, rate in ((self.cad, '1'), (self.usd, '1.4')):
            expected = Decimal('10') * Decimal(rate)
            for activity in (self.buy, self.sell):
                for difference in ('-0.11', '-0.10', '-0.09', '0.09', '0.10', '0.11'):
                    with self.subTest(currency=currency.code, activity=activity.name, difference=difference):
                        total = expected + Decimal(difference)
                        result = self.create(activity=str(activity.id), shares='1', price='10', fee='0', priceCurrency=str(currency.id), exchangeRate=rate, total=str(total))
                        self.assertIsNone(result.errors, result.errors)
                        saved = result.data['createTransaction']
                        self.assertEqual(Decimal(str(saved['transaction']['total'])), total)
                        self.assertEqual(bool(saved['warnings']), abs(Decimal(difference)) > Decimal('0.10'))
                        if saved['warnings']:
                            self.assertEqual(saved['warnings'][0]['code'], 'TRANSACTION_TOTAL_DIFFERENCE')

    def test_edit_uses_same_total_warning_tolerance_and_preserves_recorded_amount(self):
        created = self.create(price='10', shares='1', exchangeRate='1', priceCurrency=str(self.cad.id), total='10')
        transaction_id = created.data['createTransaction']['transaction']['id']
        for total, warn in (('9.90', False), ('10.10', False), ('9.89', True), ('10.11', True)):
            with self.subTest(total=total):
                result = schema.execute('mutation($profile: ID!, $input: TransactionInput!) { updateTransaction(profileId: $profile, transData: $input) { trans { total } warnings { code } } }', variable_values={'profile':str(self.profile.id), 'input':{'id':transaction_id, 'total':total}})
                self.assertIsNone(result.errors, result.errors)
                saved = result.data['updateTransaction']
                self.assertEqual(Decimal(str(saved['trans']['total'])), Decimal(total))
                self.assertEqual(bool(saved['warnings']), warn)

    def update(self, transaction_id, **changes):
        data = {'id':str(transaction_id), **changes}
        return schema.execute('mutation($p:ID!, $t:TransactionInput!) { updateTransaction(profileId:$p, transData:$t) { trans { id account { id } platform { id currency { code } } total fee exchangeRate priceCurrency { code } totalCurrency { code } } warnings { code } } }', variable_values={'p':str(self.profile.id),'t':data})

    def usd_platform(self):
        return Platform(name='Wealthsimple', account=self.platform.account, profile=self.profile, currency=self.usd).save()

    def test_edit_can_change_platform_currency_and_recalculate_settlement_metadata(self):
        created = self.create()
        transaction_id = created.data['createTransaction']['transaction']['id']
        destination = self.usd_platform()
        result = self.update(transaction_id, platform=str(destination.id), total='3586.63', fee='0')
        self.assertIsNone(result.errors, result.errors)
        saved = result.data['updateTransaction']['trans']
        self.assertEqual(saved['platform']['id'], str(destination.id))
        self.assertEqual(saved['totalCurrency']['code'], 'USD')
        self.assertEqual(saved['priceCurrency']['code'], 'USD')
        self.assertEqual(saved['exchangeRate'], 1)
        self.assertEqual(Transaction.objects.get(id=transaction_id).account.id, destination.account.id)

    def test_cross_currency_move_requires_reviewed_total_rate_and_fees(self):
        created = self.create(priceCurrency=str(self.cad.id), exchangeRate='1', price='10', shares='1', total='11', fee='1')
        transaction_id = created.data['createTransaction']['transaction']['id']
        destination = self.usd_platform()
        for changes, code in (({}, 'TOTAL_REQUIRED'), ({'total':None}, 'TOTAL_REQUIRED'), ({'total':'7.2'}, 'FEE_REQUIRED'), ({'total':'7.2','fee':'0'}, 'EXCHANGE_RATE_REQUIRED'), ({'total':'7.2','fee':'0','exchangeRate':'0.72','totalCurrency':str(self.cad.id)}, 'TOTAL_CURRENCY_MISMATCH')):
            result = self.update(transaction_id, platform=str(destination.id), **changes)
            self.assertEqual(result.errors[0].extensions['code'], code)
            self.assertEqual(Transaction.objects.get(id=transaction_id).platform.id, self.platform.id)
        result = self.update(transaction_id, platform=str(destination.id), total='7.2', fee='0', exchangeRate='0.72')
        self.assertIsNone(result.errors, result.errors)
        self.assertEqual(result.data['updateTransaction']['trans']['priceCurrency']['code'], 'CAD')
        self.assertEqual(result.data['updateTransaction']['trans']['totalCurrency']['code'], 'USD')

    def test_cash_transaction_can_move_to_another_account_and_currency(self):
        contribution = Activity(name='Contribution').save()
        created = self.create(stock=None, activity=str(contribution.id), price=None, shares=None, priceCurrency=str(self.cad.id), exchangeRate='1', total='100')
        destination = Platform(name='Other Broker', profile=self.profile, currency=self.usd, account=Account(code='RRSP').save()).save()
        result = self.update(created.data['createTransaction']['transaction']['id'], platform=str(destination.id), account=str(destination.account.id), total='75')
        self.assertIsNone(result.errors, result.errors)
        saved = result.data['updateTransaction']['trans']
        self.assertEqual(saved['account']['id'], str(destination.account.id))
        self.assertEqual(saved['priceCurrency']['code'], 'USD')
        self.assertEqual(saved['totalCurrency']['code'], 'USD')
        self.assertEqual(saved['exchangeRate'], 1)

    def test_sale_move_still_requires_holdings_in_the_destination(self):
        self.assertIsNone(self.create().errors)
        sale = self.create(activity=str(self.sell.id), price='120', shares='1', total='168', transactionDate='2026-10-02')
        transaction_id = sale.data['createTransaction']['transaction']['id']
        destination = self.usd_platform()
        result = self.update(transaction_id, platform=str(destination.id), total='120', fee='0')
        self.assertEqual(result.errors[0].extensions['code'], 'STOCK_NOT_OWNED')
        self.assertEqual(Transaction.objects.get(id=transaction_id).platform.id, self.platform.id)
        self.assertIsNone(self.create(platform=str(destination.id), totalCurrency=str(self.usd.id), exchangeRate='1', price='100', shares='2', transactionDate='2026-10-01').errors)
        result = self.update(transaction_id, platform=str(destination.id), total='120', fee='0')
        self.assertIsNone(result.errors, result.errors)
