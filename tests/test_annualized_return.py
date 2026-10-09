import sys
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from schemas.annualized_return import xirr, calculate_annualized_return

DAY = date(2023, 1, 1)
YEAR = timedelta(days=365)
STOCK = NS(id='stock')


def row(activity, total=0, day=DAY, stock=None, platform='a', **extra):
    return NS(activity=NS(name=activity), total=Decimal(str(total)), transaction_date=day,
              stock=stock, spinoff_source=extra.pop('spinoff_source', None), shares=extra.pop('shares', None),
              platform=NS(id=platform), transfer_pair=extra.pop('transfer_pair', None),
              transfer_market_value=extra.pop('transfer_market_value', None),
              transfer_market_value_source=extra.pop('transfer_market_value_source', None), **extra)


class AnnualizedReturnTests(unittest.TestCase):
    def assert_return(self, flows, expected):
        result, note = xirr(flows)
        self.assertIsNone(note)
        self.assertAlmostEqual(float(result), expected, places=6)

    def test_compounds_instead_of_dividing_return_by_years(self):
        self.assert_return([(DAY, -1000), (DAY + 2 * YEAR, 1210)], 10)
        self.assert_return([(DAY, -1000), (DAY + YEAR, 800)], -20)
        self.assert_return([(DAY, -1000), (DAY + YEAR, 1000)], 0)

    def test_microsoft_xirr_reference_example(self):
        self.assert_return([(date(2008, 1, 1), -10000), (date(2008, 3, 1), 2750),
                            (date(2008, 10, 30), 4250), (date(2009, 2, 15), 3250),
                            (date(2009, 4, 1), 2750)], 37.3362535)

    def test_deposit_and_withdrawal_timing(self):
        self.assert_return([(DAY, -1000), (DAY + YEAR, -1000), (DAY + 2 * YEAR, 2310)], 10)
        self.assert_return([(DAY, -1000), (DAY + YEAR, 110), (DAY + 2 * YEAR, 1089)], 10)

    def test_same_day_flows_are_combined_and_ambiguous_returns_stay_blank(self):
        self.assert_return([(DAY, -500), (DAY, -500), (DAY + YEAR, 1100)], 10)
        for flows in ([(DAY, -100), (DAY + YEAR, 230), (DAY + 2 * YEAR, -132)],
                      [(DAY, -100), (DAY, 110)], [(DAY, 100)], [(DAY, -100)],
                      [(None, -100), (DAY, 110)], [(DAY, 'NaN'), (DAY + YEAR, 100)]):
            result, note = xirr(flows)
            self.assertIsNone(result)
            self.assertTrue(note)

    def test_account_income_and_trading_are_not_counted_twice(self):
        rows = [row('Contribution', 1000), row('Buy', 1000, stock=STOCK),
                row('Sell', 1050, day=DAY + YEAR, stock=STOCK),
                row('Dividends', 60, day=DAY + YEAR, stock=STOCK),
                row('Service Fee', 10, day=DAY + YEAR)]
        result, start, note = calculate_annualized_return(rows, Decimal(1100), DAY + YEAR)
        self.assertAlmostEqual(float(result), 10, places=6)
        self.assertEqual(start, DAY)
        self.assertIsNone(note)

    def test_boundary_asset_transfer_uses_market_value_not_book_cost(self):
        incoming = row('Transfer In', 1000, stock=STOCK, shares=10,
                       transfer_market_value=Decimal(1500), transfer_market_value_source='yahoo_close')
        result, _, note = calculate_annualized_return([incoming], Decimal(1650), DAY + YEAR)
        self.assertAlmostEqual(float(result), 10, places=6)
        self.assertIn('Estimated', note)
        incoming.transfer_market_value = None
        self.assertIsNone(calculate_annualized_return([incoming], Decimal(1650), DAY + YEAR)[0])

    def test_internal_asset_and_cash_transfers_cancel_without_market_values(self):
        rows = [row('Contribution', 1000)]
        for stock, shares, total in ((STOCK, 10, 1000), (None, None, 50)):
            pair = 'asset' if stock else 'cash'
            for activity, platform in (('Transfer Out', 'a'), ('Transfer In', 'b')):
                rows.append(row(activity, total, day=DAY + YEAR / 2, stock=stock, shares=shares,
                                platform=platform, transfer_pair=pair))
        result, _, note = calculate_annualized_return(rows, Decimal(1100), DAY + YEAR)
        self.assertAlmostEqual(float(result), 10, places=6)
        self.assertIsNone(note)

    def test_stock_returns_include_income_and_tax_but_not_account_cash_or_fees(self):
        rows = [row('Contribution', 9999), row('Buy', 1010, stock=STOCK),
                row('Dividends', 30, day=DAY + YEAR, stock=STOCK),
                row('Withholding Tax', 10, day=DAY + YEAR, stock=STOCK),
                row('Service Fee', 500, day=DAY + YEAR)]
        result, _, note = calculate_annualized_return(rows, Decimal(1091), DAY + YEAR, 'stock')
        self.assertAlmostEqual(float(result), 10, places=6)  # (1091 + 30 - 10) / 1010
        self.assertIsNone(note)

    def test_missing_valuation_and_spinoff_market_value_stay_blank(self):
        self.assertIsNone(calculate_annualized_return([row('Contribution', 1000)], None, DAY + YEAR)[0])
        spin = row('Stock Spinoff', stock=NS(id='child'), spinoff_source=STOCK)
        for stock_id in ('stock', 'child'):
            result, _, note = calculate_annualized_return([spin], Decimal(100), DAY + YEAR, stock_id)
            self.assertIsNone(result)
            self.assertIn('spinoff', note)
        # A spinoff moves no capital across an account boundary.
        result, _, _ = calculate_annualized_return([row('Contribution', 1000), spin], Decimal(1100), DAY + YEAR)
        self.assertAlmostEqual(float(result), 10, places=6)

    def test_fully_disposed_stock_return_uses_sale_date(self):
        rows = [row('Buy', 1000, stock=STOCK), row('Sell', 1210, day=DAY + 2 * YEAR, stock=STOCK)]
        result, _, note = calculate_annualized_return(rows, Decimal(0), DAY + 3 * YEAR, 'stock')
        self.assertAlmostEqual(float(result), 10, places=6)
        self.assertIsNone(note)

    def test_inconsistent_pairs_are_not_silently_cancelled(self):
        rows = [row('Transfer Out', 100, stock=STOCK, shares=1, transfer_pair='pair'),
                row('Transfer In', 100, stock=STOCK, shares=2, transfer_pair='pair', platform='b')]
        self.assertIn('inconsistent', calculate_annualized_return(rows, Decimal(100), DAY + YEAR)[2])
