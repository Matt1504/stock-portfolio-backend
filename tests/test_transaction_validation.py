import sys
import unittest
from datetime import date
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
import mongomock
from mongoengine import connect, disconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from models.models import Transaction, Platform, Profile, Account, Currency, Activity, Stock, ContributionLimit, Asset
from schemas.schema import schema


class TransactionValidationTests(unittest.TestCase):
    def setUp(self):
        disconnect()
        with patch('mongoengine.connection.MongoClient', mongomock.MongoClient):
            connect('transaction_validation_tests', uuidRepresentation='standard')
        for model in (Transaction, Platform, Profile, Account, Currency, Activity, Stock, ContributionLimit, Asset):
            model.drop_collection()
        self.profile = Profile(name='Alice').save()
        self.other = Profile(name='Bob').save()
        self.account = Account(code='TFSA').save()
        self.currency = Currency(code='CAD').save()
        self.platform = Platform(name='Broker', profile=self.profile, account=self.account, currency=self.currency).save()
        self.second = Platform(name='Second', profile=self.profile, account=self.account, currency=self.currency).save()
        self.foreign = Platform(name='Foreign', profile=self.other, account=self.account, currency=self.currency).save()
        self.stock = Stock(name='Example', asset=Asset(name='Stock').save()).save()
        self.activities = {name: Activity(name=name).save() for name in ('Buy', 'Sell', 'Dividends', 'Withholding Tax', 'Stock Split', 'Transfer In', 'Transfer Out', 'Contribution', 'Withdrawal', 'Interest')}

    def tearDown(self):
        disconnect()

    def record(self, activity, shares=0, total=0, day='2026-01-01', platform=None, stock=True):
        platform = platform or self.platform
        return Transaction(activity=self.activities[activity], shares=shares, total=total, stock=self.stock if stock else None,
                           platform=platform, account=platform.account, transaction_date=date.fromisoformat(day)).save()

    def mutation(self, activity=None, shares=None, total=10, day='2026-01-02', platform=None, stock=True, edit=None):
        data = {'total': str(total), 'transactionDate': day}
        if activity: data['activity'] = str(self.activities[activity].id)
        if shares is not None: data['shares'] = str(shares)
        if platform or not edit: data['platform'] = str((platform or self.platform).id)
        if not edit: data['stock'] = str(self.stock.id) if stock else None
        if edit: data['id'] = str(edit.id)
        operation, result_field = ('updateTransaction', 'trans') if edit else ('createTransaction', 'transaction')
        query = 'mutation($profile: ID!, $trans: TransactionInput!) { ' + operation + '(profileId: $profile, transData: $trans) { ' + result_field + ' { id shares total } warnings { code message } } }'
        return schema.execute(query, variable_values={'profile': str(self.profile.id), 'trans': data})

    def test_protected_activities_require_ownership_on_selected_platform(self):
        self.record('Buy', 10, platform=self.second)
        self.record('Buy', 10, platform=self.foreign)
        for activity in ('Sell',):
            with self.subTest(activity=activity):
                before = Transaction.objects.count()
                result = self.mutation(activity, shares=1)
                self.assertTrue(result.errors)
                self.assertEqual(result.errors[0].extensions['code'], 'STOCK_NOT_OWNED')
                self.assertEqual(Transaction.objects.count(), before)

    def paired_platform(self):
        usd = Currency(code='USD').save()
        return Platform(name=self.platform.name, profile=self.profile, account=self.account, currency=usd).save()

    def test_dividends_can_be_deposited_in_currency_platform_without_holdings(self):
        paired = self.paired_platform()
        self.record('Buy', 2)
        result = self.mutation('Dividends', platform=paired)
        self.assertIsNone(result.errors, result.errors)
        dividend = Transaction.objects.get(id=result.data['createTransaction']['transaction']['id'])
        self.assertEqual(dividend.platform.id, paired.id)
        self.assertEqual(dividend.total_currency.code, 'USD')
        for activity in ('Sell',):
            result = self.mutation(activity, platform=paired, shares=1)
            self.assertEqual(result.errors[0].extensions['code'], 'STOCK_NOT_OWNED')
        self.record('Buy', 1, platform=paired)
        self.assertEqual(self.mutation('Sell', shares=3).errors[0].extensions['code'], 'INSUFFICIENT_SHARES')
        self.assertIsNone(self.mutation(edit=dividend, total=20).errors)
        self.assertIsNone(self.mutation(edit=dividend, day='2025-12-31').errors)

    def test_income_and_splits_do_not_require_ownership(self):
        paired = self.paired_platform()
        for platform in (self.platform, paired, self.second):
            for activity in ('Dividends', 'Withholding Tax', 'Stock Split'):
                with self.subTest(platform=platform.name, activity=activity):
                    self.assertIsNone(self.mutation(activity, platform=platform, shares=0).errors)

    def test_income_before_purchase_and_after_full_sale_is_allowed(self):
        self.record('Buy', 1)
        self.record('Sell', 1, day='2026-01-10')
        for activity in ('Dividends', 'Withholding Tax'):
            for day in ('2025-12-31', '2026-01-05', '2026-01-11'):
                self.assertIsNone(self.mutation(activity, day=day).errors)

    def test_dividends_require_stock_but_not_payment_date_ownership(self):
        for day in ('2025-12-31', '2026-01-05', '2026-01-11'):
            self.assertIsNone(self.mutation('Dividends', day=day).errors)
        self.assertEqual(self.mutation('Dividends', stock=False).errors[0].extensions['code'], 'STOCK_REQUIRED')

    def test_purchase_edits_do_not_revalidate_later_paired_dividends(self):
        paired = self.paired_platform()
        buy = self.record('Buy', 1)
        self.assertIsNone(self.mutation('Dividends', platform=paired, day='2026-01-05').errors)
        self.assertIsNone(self.mutation(edit=buy, day='2026-01-06').errors)
        self.assertIsNone(self.mutation(edit=buy, platform=self.second, day='2026-01-01').errors)
        self.assertEqual(buy.reload().platform.id, self.second.id)

    def test_stock_tax_edits_do_not_check_ownership_date(self):
        paired = self.paired_platform()
        self.record('Buy', 1)
        result = self.mutation('Withholding Tax', platform=paired)
        self.assertIsNone(result.errors, result.errors)
        tax = Transaction.objects.get(id=result.data['createTransaction']['transaction']['id'])
        self.assertEqual(tax.platform.id, paired.id)
        self.assertEqual(tax.total_currency.code, 'USD')
        self.assertIsNone(self.mutation(edit=tax, total=2).errors)
        self.assertIsNone(self.mutation(edit=tax, day='2025-12-31').errors)
        # Account-level withholding remains valid without any stock holdings.
        self.assertIsNone(self.mutation('Withholding Tax', platform=paired, stock=False, day='2025-12-31').errors)

    def test_purchase_edits_do_not_revalidate_later_paired_stock_tax(self):
        paired = self.paired_platform()
        buy = self.record('Buy', 1)
        self.assertIsNone(self.mutation('Withholding Tax', platform=paired, day='2026-01-05').errors)
        self.assertIsNone(self.mutation(edit=buy, day='2026-01-06').errors)
        self.assertIsNone(self.mutation(edit=buy, platform=self.second, day='2026-01-01').errors)

    def test_fractional_sales_cannot_exceed_holdings_and_full_sale_succeeds(self):
        self.record('Buy', '0.3')
        self.record('Sell', '0.1', day='2026-01-02')
        result = self.mutation('Sell', shares='0.20000001', day='2026-01-03')
        self.assertEqual(result.errors[0].extensions['code'], 'INSUFFICIENT_SHARES')
        result = self.mutation('Sell', shares='0.2', day='2026-01-03')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['createTransaction']['warnings'], [])
        self.assertIsNone(self.mutation('Dividends', day='2026-01-04').errors)

    def test_splits_and_share_transfers_adjust_owned_quantity(self):
        self.record('Transfer In', 2)
        self.assertIsNone(self.mutation('Stock Split', shares=2).errors)
        self.record('Transfer Out', 1, day='2026-01-03')
        self.assertTrue(self.mutation('Sell', shares=4, day='2026-01-04').errors)
        self.assertIsNone(self.mutation('Sell', shares=3, day='2026-01-04').errors)

    def test_backdated_income_uses_historical_ownership_not_current_position(self):
        self.record('Buy', 1)
        self.record('Sell', 1, day='2026-01-10')
        self.assertIsNone(self.mutation('Dividends', day='2026-01-05').errors)
        self.assertIsNone(self.mutation('Dividends', day='2025-12-31').errors)
        self.assertIsNone(self.mutation('Withholding Tax', stock=False).errors)
        self.assertIsNone(self.mutation('Interest', stock=False).errors)

    def test_historical_sale_and_buy_edits_cannot_invalidate_later_transactions(self):
        buy = self.record('Buy', 2)
        self.record('Sell', 2, day='2026-01-10')
        self.assertTrue(self.mutation('Sell', shares=1, day='2026-01-05').errors)
        result = self.mutation(shares=1, day='2026-01-01', edit=buy)
        self.assertTrue(result.errors)
        self.assertIn('Cannot save Buy', result.errors[0].message)
        self.assertIn('later Sell', result.errors[0].message)
        self.assertEqual(buy.reload().shares, Decimal(2))
        self.assertTrue(self.mutation(platform=self.second, day='2026-01-01', edit=buy).errors)
        self.assertEqual(buy.reload().platform.id, self.platform.id)

    def test_stock_linked_income_does_not_revalidate_unaffected_legacy_sales(self):
        self.record('Buy', 1)
        self.record('Sell', 2, day='2026-01-10')  # A legacy issue does not change this historical income.
        self.assertIsNone(self.mutation('Interest', day='2026-01-05').errors)
        self.assertIsNone(self.mutation('Dividends', day='2026-01-05').errors)

    def test_edit_excludes_original_sale_and_rechecks_destination(self):
        self.record('Buy', 2)
        sale = self.record('Sell', 1, day='2026-01-02')
        self.assertIsNone(self.mutation(shares=2, edit=sale).errors)
        self.assertTrue(self.mutation(shares=3, edit=sale).errors)
        self.assertEqual(sale.reload().shares, Decimal(2))
        self.assertTrue(self.mutation(platform=self.second, shares=1, edit=sale).errors)
        self.assertEqual(sale.reload().platform.id, self.platform.id)

    def test_zero_negative_sales_are_rejected_but_splits_are_user_recorded(self):
        self.record('Buy', 2)
        for shares in (0, -1):
            self.assertTrue(self.mutation('Sell', shares=shares).errors)
        self.assertIsNone(self.mutation('Stock Split', shares=-2).errors)
        self.assertIsNone(self.mutation('Stock Split', shares=-1).errors)

    def test_amount_only_assets_skip_ownership_rules(self):
        for name in ('Index Fund', 'Mutual Fund'):
            with self.subTest(asset=name):
                self.stock.asset = Asset(name=name).save()
                self.stock.save()
                for activity in ('Sell', 'Dividends', 'Withholding Tax', 'Stock Split'):
                    self.assertIsNone(self.mutation(activity).errors)

    def test_contribution_warning_uses_profile_account_lifetime_totals_and_saves(self):
        ContributionLimit(profile=self.profile, account=self.account, amount=50).save()
        ContributionLimit(profile=self.profile, account=self.account, amount=50).save()
        ContributionLimit(profile=self.other, account=self.account, amount=9000).save()
        self.record('Contribution', total=60, stock=False, platform=self.second, day='2025-01-01')
        self.record('Contribution', total=9000, stock=False, platform=self.foreign)
        self.record('Transfer In', total=9000, stock=False)
        self.record('Withdrawal', total=30, stock=False)
        exact = self.mutation('Contribution', stock=False, total=40)
        self.assertIsNone(exact.errors)
        self.assertEqual(exact.data['createTransaction']['warnings'], [])
        result = self.mutation('Contribution', stock=False, total=1)
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['createTransaction']['warnings'][0]['code'], 'CONTRIBUTION_LIMIT_EXCEEDED')
        self.assertIn('101.00', result.data['createTransaction']['warnings'][0]['message'])
        self.assertIsNotNone(Transaction.objects.get(id=result.data['createTransaction']['transaction']['id']))

    def test_contribution_edit_does_not_double_count_and_no_limit_does_not_warn(self):
        contribution = self.record('Contribution', total=80, stock=False)
        self.assertEqual(self.mutation('Contribution', stock=False, total=1).data['createTransaction']['warnings'], [])
        ContributionLimit(profile=self.profile, account=self.account, amount=100).save()
        result = self.mutation(edit=contribution, total=90, day='2026-01-01')
        self.assertEqual(result.data['updateTransaction']['warnings'], [])
        result = self.mutation(edit=contribution, total=101, day='2026-01-01')
        self.assertEqual(result.data['updateTransaction']['warnings'][0]['code'], 'CONTRIBUTION_LIMIT_EXCEEDED')
        self.assertEqual(contribution.reload().total, Decimal(101))

    def test_validation_uses_persisted_decimal_rounding(self):
        result = self.mutation('Buy', shares='0.000000005')
        self.assertIsNone(result.errors)
        self.assertEqual(Transaction.objects.get(id=result.data['createTransaction']['transaction']['id']).shares, Decimal('0.00000001'))
        ContributionLimit(profile=self.profile, account=self.account, amount=1).save()
        contribution = self.record('Contribution', total=1, stock=False)
        result = self.mutation(edit=contribution, total='1.004', day='2026-01-01')
        self.assertIsNone(result.errors)
        self.assertEqual(result.data['updateTransaction']['warnings'], [])
        result = self.mutation(edit=contribution, total='1.005', day='2026-01-01')
        self.assertIsNone(result.errors)
        self.assertIn('1.01', result.data['updateTransaction']['warnings'][0]['message'])
        self.assertEqual(contribution.reload().total, Decimal('1.01'))
