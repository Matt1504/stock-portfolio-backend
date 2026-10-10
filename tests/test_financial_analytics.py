import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
import mongomock
from mongoengine import connect, disconnect
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from schemas.financial_analytics import calculate_analytics, contribution_analytics
from schemas.schema import schema
from models.models import Account, Activity, Asset, ContributionLimit, Currency, Platform, Profile, Stock, Transaction


def stock(key='EX',asset='Stock'):
    return NS(id=key,ticker=key,asset=NS(name=asset))


def row(activity,total=0,shares=0,s=None,platform='a',id='1',**kwargs):
    return NS(id=id,activity=NS(name=activity),total=total,shares=shares,stock=s,fee=kwargs.get('fee',0),price=kwargs.get('price',0),
              transaction_date=kwargs.get('day',date(2026,1,1)),account=NS(code='TFSA'),platform=NS(id=platform,name=platform,currency=NS(code='CAD')),
              spinoff_source=kwargs.get('source'),allocated_book_cost=kwargs.get('allocation'),principal_returned=kwargs.get('principal'),interest_earned=kwargs.get('interest'))


def values(result): return {s.title:s.value for s in result.statistics}


class RecordedAnalyticsTests(unittest.TestCase):
    def test_average_basis_fractional_shares_income_and_fees(self):
        s=stock()
        result=calculate_analytics([row('Buy',202,2,s,fee=2),row('Sell',60,Decimal('.5'),s,id='2',fee=1),row('Contribution',1000),row('Withdrawal',100),row('Transfer In',50),row('Transfer Out',20),row('Dividends',20,s=s),row('Withholding Tax',2,s=s),row('Withholding Tax',30),row('Service Fee',5)],'CAD')
        v=values(result)
        self.assertEqual(v['Total Book Cost'],Decimal('151.5'))
        self.assertEqual(v['Total Share(s) Owned'],Decimal('1.5'))
        self.assertEqual(v['Realized Gain/Loss'],Decimal('9.5'))
        self.assertEqual(v['Realized Profit'],Decimal('22.5'))
        self.assertEqual(v['Fees Paid'],Decimal('40'))
        self.assertEqual(v['Net Deposits'],Decimal('930'))
        self.assertEqual(v['Cash Balance'],Decimal('771'))
        self.assertEqual(result.book_cost_history[-1].value,Decimal('151.5'))
        self.assertEqual(result.book_cost_history[-1].value_1,Decimal('930'))

    def test_transfer_keeps_basis_and_profit_without_cash(self):
        s=stock()
        rows=[row('Buy',1010,10,s,fee=10),row('Transfer Out',1010,10,s,id='2'),row('Transfer In',1010,10,s,platform='b',id='3'),row('Sell',500,4,s,platform='b',id='4')]
        v=values(calculate_analytics(rows,'CAD'))
        self.assertEqual(v['Total Book Cost'],Decimal('606'))
        self.assertEqual(v['Realized Profit'],Decimal('96'))
        self.assertEqual(v['Cash Balance'],Decimal('-510'))
        self.assertEqual(v['Net Deposits'],0)
        target=values(calculate_analytics(rows[2:],'CAD'))
        self.assertEqual(target['Realized Gain/Loss'],Decimal('96'))

    def test_same_day_order_split_and_spinoff(self):
        parent,child=stock('P'),stock('C')
        rows=[row('Stock Spinoff',shares=2,s=child,id='3',source=parent,allocation=80),row('Stock Split',shares=4,s=parent,id='2'),row('Buy',400,4,parent)]
        result=calculate_analytics(rows,'CAD')
        self.assertEqual(values(result)['Total Book Cost'],400)
        self.assertEqual(values(result)['Total Share(s) Owned'],10)
        self.assertEqual(values(calculate_analytics(rows,'CAD','P'))['Book Cost'],320)
        self.assertEqual(values(calculate_analytics(rows,'CAD','C'))['Book Cost'],80)

    def test_sold_holdings_excluded_and_average_cost_per_platform(self):
        s=stock(); small=stock('SMALL')
        rows=[row('Buy',1000,10,s),row('Buy',2000,10,s,platform='b'),row('Sell',600,10,s,id='2'),row('Buy',20,1,small)]
        r=calculate_analytics(rows,'CAD');v=values(r)
        self.assertEqual(v['Realized Gain/Loss'],-400)
        self.assertEqual(v['Largest Holding'],2000)
        self.assertEqual(v['Smallest Holding'],20)
        self.assertEqual(v['Unique Share(s) Owned'],2)

    def test_missing_sale_basis_is_null_not_zero(self):
        r=calculate_analytics([row('Sell',100,2,stock())],'CAD')
        self.assertIsNone(values(r)['Realized Gain/Loss']); self.assertIsNone(values(r)['Realized Profit'])
        self.assertTrue(r.issues); self.assertEqual(r.distribution,[])

    def test_amount_only_funds_and_gic_maturity(self):
        fund=stock(asset='Index Fund')
        r=calculate_analytics([row('Buy',3105.47,s=fund)],'CAD','EX','Index Fund')
        self.assertEqual([s.title for s in r.statistics],['Book Cost','Realized Gain/Loss','Sale Proceeds','Last Buy Date'])
        self.assertEqual(values(r)['Book Cost'],Decimal('3105.47'))
        self.assertIsNone(values(calculate_analytics([row('Buy',100,s=fund),row('Sell',120,s=fund,id='2')],'CAD','EX','Index Fund'))['Realized Gain/Loss'])
        gic=stock(asset='GIC')
        r=calculate_analytics([row('Buy',1000,s=gic,fee=2),row('GIC Maturity',1100,s=gic,principal=1000,interest=100,fee=1,id='2')],'CAD','EX','GIC')
        self.assertEqual(values(r)['Book Cost'],0)
        self.assertEqual(values(r)['Interest Earned'],100)
        self.assertEqual(values(r)['Realized Profit/Loss'],97)
        self.assertEqual(r.trade_history[0].value_1,1000)

    def test_stock_scope_excludes_child_trade_income_from_parent(self):
        p,c=stock('P'),stock('C')
        rows=[row('Buy',400,4,p),row('Stock Spinoff',shares=1,s=c,id='2',source=p,allocation=80),row('Dividends',5,s=c,id='3')]
        self.assertEqual(values(calculate_analytics(rows,'CAD','P'))['Dividends/Interest Earned'],0)

    def test_chart_buy_sell_both_positive_and_tax_negative(self):
        s=stock()
        r=calculate_analytics([row('Buy',100,2,s),row('Sell',60,1,s,id='2'),row('Dividends',10,s=s),row('Withholding Tax',2,s=s)],'CAD','EX')
        self.assertEqual(r.trade_history[0].value,100);self.assertEqual(r.trade_history[0].value_1,60)
        self.assertEqual(r.trade_history[0].shares,2);self.assertEqual(r.trade_history[0].sell_shares,1)
        self.assertEqual(r.income_history[0].value,10);self.assertEqual(r.income_history[0].value_1,-2)


class AnalyticsScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        disconnect()
        with patch('mongoengine.connection.MongoClient',mongomock.MongoClient):connect('analytics_tests',uuidRepresentation='standard')
    @classmethod
    def tearDownClass(cls):disconnect()
    def setUp(self):
        for model in (Transaction,Platform,Stock,Profile,Account,Currency,Asset,Activity,ContributionLimit):model.drop_collection()
        self.owner=Profile(name='Owner').save();other=Profile(name='Other').save()
        self.account=Account(code='TFSA').save(); self.nrsa=Account(code='NRSA',has_contribution_limit=False).save()
        cad=Currency(code='CAD').save();usd=Currency(code='USD').save();a=Activity(name='Contribution').save()
        self.platform=Platform(name='A',profile=self.owner,currency=cad,account=self.account).save()
        self.other=Platform(name='B',profile=other,currency=cad,account=self.account).save()
        p_us=Platform(name='US',profile=self.owner,currency=usd,account=self.account).save()
        for p,total in [(self.platform,100),(self.other,999),(p_us,50)]:Transaction(platform=p,account=self.account,activity=a,total=total,transaction_date=date(2026,1,1)).save()
        ContributionLimit(profile=self.owner,account=self.account,amount=100,yearEnd=date(2026,12,31)).save()
    def test_graphql_profile_currency_and_request_reuse(self):
        context={}
        q='query($p: ID!, $a: ID!) { transactionsByAccount(profileId:$p,account:$a) {id} financialAnalytics(profileId:$p,account:$a) {currency statistics {title value} } }'
        r=schema.execute(q,variable_values={'p':str(self.owner.id),'a':str(self.account.id)},context_value=context)
        self.assertFalse(r.errors,r.errors);self.assertEqual(len(r.data['transactionsByAccount']),2)
        summaries={a['currency']:{s['title']:s['value'] for s in a['statistics']} for a in r.data['financialAnalytics']}
        self.assertEqual(Decimal(summaries['CAD']['Cash Balance']),100);self.assertEqual(Decimal(summaries['USD']['Cash Balance']),50)
        self.assertEqual(len(context['_financial_records']),1)
    def test_other_platform_is_rejected(self):
        r=schema.execute('query($p: ID!,$s: ID!) {financialAnalytics(profileId:$p,platform:$s){currency}}',variable_values={'p':str(self.owner.id),'s':str(self.other.id)})
        self.assertTrue(r.errors)
    def test_contributions_match_lifetime_overview_without_currency_conversion(self):
        result={r.account_id:r for r in contribution_analytics(str(self.owner.id))}
        self.assertEqual(result[str(self.account.id)].contribution,150)
        self.assertEqual(result[str(self.account.id)].percentage,Decimal('150.00'))
        self.assertEqual(result[str(self.account.id)].history[0].value,150)
        self.assertIsNone(result[str(self.nrsa.id)].limit);self.assertIsNone(result[str(self.nrsa.id)].percentage)
