import sys
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import mongomock
from mongoengine import connect, disconnect
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from schemas.market_valuation import calculate_valuation
from schemas.schema import schema
from models.models import Profile, Platform, Account, Currency, Asset, Activity, Stock, Transaction

STAMP = '2026-10-08T20:00:00+00:00'


def stock(key='one', currency='CAD', asset='Stock'):
    return NS(id=key, ticker=key.upper(), asset=NS(name=asset), currency=NS(code=currency))


def row(s, activity, shares=0, total=0, platform='a', **kwargs):
    return NS(id=kwargs.pop('id', '1'), transaction_date=date(2026, 1, 1), stock=s,
              platform=NS(id=platform, currency=NS(code='CAD')), activity=NS(name=activity),
              shares=shares, total=total, fee=kwargs.pop('fee', 0), total_currency=None,
              price=0, spinoff_source=kwargs.pop('spinoff_source', None),
              allocated_book_cost=kwargs.pop('allocated_book_cost', 0), principal_returned=kwargs.pop('principal_returned', 0))


def quote(s):
    return dict(price='120', currency=s.currency.code, quote_time=STAMP, last_updated=STAMP, refresh_overdue=False)


class ValuationTests(unittest.TestCase):
    def calc(self, rows, **kwargs):
        return calculate_valuation(rows, 'CAD', quote_reader=quote, **kwargs)

    def test_market_value_is_not_unrealized_gain_and_cash(self):
        s = stock()
        v = self.calc([row(None, 'Contribution', total=1050), row(s, 'Buy', 10, 1000)])
        self.assertEqual(v.market_value, Decimal('1200.00'))
        self.assertEqual(v.unrealized_gain, Decimal('200.00'))
        self.assertEqual(v.unrealized_return, Decimal('20.00'))
        self.assertEqual(v.cash_balance, Decimal('50.00'))
        self.assertEqual(v.total_value, Decimal('1250.00'))

    def test_valuation_exposes_annualized_return_without_changing_unrealized_metrics(self):
        from datetime import timedelta
        s = stock()
        rows = [row(None, 'Contribution', total=1000), row(s, 'Buy', 10, 1000)]
        v = self.calc(rows, valuation_date=date(2026, 1, 1) + timedelta(days=365))
        self.assertEqual(v.annualized_return, Decimal('20.00'))
        self.assertEqual(v.annualized_start_date, date(2026, 1, 1))
        self.assertEqual(v.unrealized_return, Decimal('20.00'))
        self.assertEqual(v.market_value, Decimal('1200.00'))
        missing = calculate_valuation(rows, 'CAD', quote_reader=lambda _: None)
        self.assertIsNone(missing.annualized_return)
        self.assertFalse(missing.complete)

    def test_sale_removes_average_basis_and_buy_fees_are_not_deducted_again(self):
        s = stock()
        v = self.calc([row(s, 'Buy', 10, 1010, fee=10), row(s, 'Sell', 4, 500, id='2')], stock_id='one')
        self.assertEqual(v.book_cost, Decimal('606.00'))
        self.assertEqual(v.market_value, Decimal('720.00'))
        self.assertEqual(v.unrealized_gain, Decimal('114.00'))
        self.assertIsNone(v.cash_balance)
        self.assertIsNone(v.total_value)

    def test_transfer_carries_basis_and_does_not_change_cash(self):
        s = stock()
        rows = [row(None, 'Contribution', total=1000), row(s, 'Buy', 10, 1000),
                row(s, 'Transfer Out', 10, 1000, id='2'), row(s, 'Transfer In', 10, 1000, platform='b', id='3'),
                row(s, 'Sell', 2, 250, platform='b', id='4')]
        v = self.calc(rows)
        self.assertEqual(v.book_cost, Decimal('800.00'))
        self.assertEqual(v.market_value, Decimal('960.00'))
        self.assertEqual(v.cash_balance, Decimal('250.00'))

    def test_split_and_spinoff_preserve_total_basis(self):
        parent, child = stock(), stock('child')
        v = self.calc([row(parent, 'Buy', 4, 400), row(parent, 'Stock Split', 4, id='2'),
                       row(child, 'Stock Spinoff', 2, spinoff_source=parent, allocated_book_cost=80, id='3')])
        self.assertEqual(v.book_cost, Decimal('400.00'))
        self.assertEqual(v.market_value, Decimal('1200.00'))

    def test_current_fx_converts_market_value_without_touching_book_cost(self):
        s = stock(currency='USD')
        fx = lambda *_: dict(rate='1.4', quote_time=STAMP, last_updated=STAMP, refresh_overdue=False)
        v = self.calc([row(s, 'Buy', 10, 1400)], fx_reader=fx)
        self.assertEqual(v.book_cost, Decimal('1400.00'))
        self.assertEqual(v.market_value, Decimal('1680.00'))
        self.assertEqual(v.unrealized_gain, Decimal('280.00'))

    def test_missing_price_or_fx_never_counts_as_zero_complete_total(self):
        s = stock()
        v = calculate_valuation([row(s, 'Buy', 10, 1000)], 'CAD', quote_reader=lambda _: None)
        self.assertFalse(v.complete)
        self.assertIsNone(v.market_value)
        self.assertIsNone(v.total_value)
        self.assertEqual(v.missing_tickers, ['ONE'])
        v = self.calc([row(stock(currency='USD'), 'Buy', 1, 100)], fx_reader=lambda *_: None)
        self.assertFalse(v.complete)
        self.assertIsNone(v.unrealized_gain)

    def test_unrelated_fund_disposal_does_not_block_selected_stock(self):
        s, fund = stock(), stock('fund', asset='Mutual Fund')
        v = self.calc([row(s, 'Buy', 1, 100), row(fund, 'Buy', total=1000), row(fund, 'Sell', total=1100, id='2')], stock_id='one')
        self.assertTrue(v.complete)
        self.assertEqual(v.market_value, Decimal('120.00'))

    def test_amount_only_funds_and_open_gics_are_incomplete(self):
        for asset in ('GIC', 'Index Fund', 'Mutual Fund'):
            s = stock(asset=asset)
            v = self.calc([row(s, 'Buy', total=1000)])
            self.assertFalse(v.complete)
            self.assertEqual(v.book_cost, Decimal('1000.00'))

    def test_matured_gic_and_fully_sold_stock_do_not_require_price(self):
        s, gic = stock(), stock('gic', asset='GIC')
        v = calculate_valuation([row(s, 'Buy', 1, 100), row(s, 'Sell', 1, 120, id='2'),
                                 row(gic, 'Buy', total=1000), row(gic, 'GIC Maturity', total=1100, principal_returned=1000, id='3')],
                                'CAD', quote_reader=lambda _: None)
        self.assertTrue(v.complete)
        self.assertEqual(v.market_value, Decimal('0.00'))
        self.assertEqual(v.total_value, Decimal('120.00'))


class ValuationScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        disconnect()
        with patch('mongoengine.connection.MongoClient', mongomock.MongoClient):
            connect('valuation_tests', uuidRepresentation='standard')

    @classmethod
    def tearDownClass(cls): disconnect()

    def setUp(self):
        for model in (Transaction, Platform, Stock, Profile, Account, Currency, Asset, Activity): model.drop_collection()
        self.owner = Profile(name='Owner').save()
        other = Profile(name='Other').save()
        account = Account(code='TFSA').save()
        cad = Currency(code='CAD').save()
        self.platform = Platform(name='A', profile=self.owner, currency=cad, account=account).save()
        self.other = Platform(name='B', profile=other, currency=cad, account=account).save()
        activity = Activity(name='Contribution').save()
        Transaction(platform=self.platform, account=account, activity=activity, total=100, transaction_date=date(2026, 1, 1)).save()
        Transaction(platform=self.platform, account=account, activity=activity, total=900, transaction_date=date(2099, 1, 1)).save()
        Transaction(platform=self.other, account=account, activity=activity, total=200, transaction_date=date(2026, 1, 1)).save()

    def test_graphql_scopes_to_profile_and_excludes_future_transactions(self):
        q = 'query($id: ID!) { marketValuation(profileId: $id, currency: "CAD") { cashBalance totalValue complete annualizedReturn annualizedStartDate } }'
        result = schema.execute(q, variables={'id': str(self.owner.id)})
        self.assertFalse(result.errors, result.errors)
        self.assertEqual(result.data['marketValuation']['totalValue'], '100.00')
        self.assertEqual(result.data['marketValuation']['annualizedReturn'], '0.00')
        self.assertEqual(result.data['marketValuation']['annualizedStartDate'], '2026-01-01')

    def test_other_profiles_platform_is_rejected(self):
        result = schema.execute('query($id: ID!, $platform: ID!) { marketValuation(profileId: $id, currency: "CAD", platform: $platform) { complete } }',
                                variables={'id': str(self.owner.id), 'platform': str(self.other.id)})
        self.assertTrue(result.errors)
