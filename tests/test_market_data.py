import json
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import fakeredis

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from market_data.quotes import mapping, quote_key, read_quote
from market_data.worker import held_stocks, prune_quotes, refresh

NOW = datetime(2026, 10, 8, 18, tzinfo=timezone.utc)
CLOSE = datetime(2026, 10, 7, 20, tzinfo=timezone.utc)


def stock(key='one', ticker='XEQT', currency='CAD', asset='Stock'):
    return NS(id=key, ticker=ticker, currency=NS(code=currency), asset=NS(name=asset), market_symbol=None, market_exchange=None)


def transaction(s, activity, shares, platform='a', day=date(2026, 1, 1)):
    return NS(id='row', stock=s, platform=NS(id=platform), transaction_date=day, activity=NS(name=activity), shares=shares)


class MarketDataTests(unittest.TestCase):
    def setUp(self):
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.stock = stock()

    def payload(self, opened=False):
        return dict(price='45.25', currency='CAD', quote_time=CLOSE.isoformat(),
                    last_updated=NOW.isoformat(), session_close=None if opened else CLOSE.isoformat(),
                    source='yahoo-finance', price_kind='last_trade' if opened else 'close')

    def test_held_only_deduplicates_and_handles_transfers_splits_spinoffs(self):
        sold = stock('sold')
        child = stock('child', 'SOLV', 'USD')
        rows = [transaction(self.stock, 'Buy', 10), transaction(self.stock, 'Stock Split', 5),
                transaction(self.stock, 'Transfer Out', 15), transaction(self.stock, 'Transfer In', 15, 'b'),
                transaction(sold, 'Buy', 4), transaction(sold, 'Sell', 4),
                transaction(child, 'Stock Spinoff', 1), transaction(self.stock, 'Dividends', 99),
                transaction(sold, 'Buy', 1, day=date(2027, 1, 1)),
                transaction(stock('gic', asset='GIC'), 'Buy', 1)]
        self.assertEqual({s.id for s in held_stocks(rows, NOW.date())}, {'one', 'child'})

    def test_country_mapping_and_override(self):
        self.assertEqual(mapping(self.stock), ('XEQT.TO', 'XTSE', 'CAD'))
        self.assertEqual(mapping(stock(currency='USD', ticker='AAPL')), ('AAPL', 'XNYS', 'USD'))
        self.stock.market_symbol = 'CUSTOM.NE'
        self.assertEqual(mapping(self.stock)[0], 'CUSTOM.NE')
        self.assertIsNone(mapping(stock(asset='Mutual Fund')))

    def test_cleanup_removes_sold_stock_but_retains_other_platform_holding(self):
        sold = stock('sold')
        rows = [transaction(self.stock, 'Buy', 1), transaction(self.stock, 'Sell', 1),
                transaction(self.stock, 'Buy', 2, platform='another-profile-platform'),
                transaction(sold, 'Buy', 3), transaction(sold, 'Sell', 3)]
        self.redis.set(quote_key(self.stock.id), 'last-good-held-quote')
        self.redis.set(quote_key(sold.id), 'sold-quote')
        self.redis.set('unrelated-transaction-cache', 'keep-me')
        self.assertEqual(prune_quotes(held_stocks(rows, NOW.date()), self.redis), 1)
        self.assertEqual(self.redis.get(quote_key(self.stock.id)), 'last-good-held-quote')
        self.assertIsNone(self.redis.get(quote_key(sold.id)))
        self.assertEqual(self.redis.get('unrelated-transaction-cache'), 'keep-me')

    def test_cleanup_with_no_holdings_removes_only_quote_entries(self):
        self.redis.set(quote_key(self.stock.id), 'old-quote')
        self.redis.set('unrelated-reference-cache', 'keep-me')
        self.assertEqual(prune_quotes([], self.redis), 1)
        self.assertEqual(self.redis.get('unrelated-reference-cache'), 'keep-me')

    def test_missing_closed_quote_fetched_then_skipped_without_expiry(self):
        fetch = unittest.mock.Mock(return_value=self.payload())
        state = lambda *_: (False, CLOSE)
        self.assertEqual(refresh([self.stock], self.redis, NOW, state, fetch), (1, 0, 0))
        self.assertEqual(refresh([self.stock], self.redis, NOW, state, fetch), (0, 1, 0))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(self.redis.ttl(quote_key(self.stock.id)), -1)

    def test_open_refresh_and_final_close_replace_intraday_quote(self):
        refresh([self.stock], self.redis, NOW, lambda *_: (True, CLOSE), lambda *_: self.payload(True))
        fetch = unittest.mock.Mock(return_value=self.payload())
        refresh([self.stock], self.redis, NOW, lambda *_: (False, CLOSE), fetch)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(read_quote(self.stock, self.redis, NOW)['price_kind'], 'close')

    def test_provider_failure_keeps_last_good_quote(self):
        refresh([self.stock], self.redis, NOW, lambda *_: (True, CLOSE), lambda *_: self.payload(True))
        before = self.redis.get(quote_key(self.stock.id))
        with self.assertLogs('market_data.worker', level='ERROR'):
            result = refresh([self.stock], self.redis, NOW, lambda *_: (True, CLOSE), unittest.mock.Mock(side_effect=ValueError('failed')))
        self.assertEqual(result, (0, 0, 1))
        self.assertEqual(self.redis.get(quote_key(self.stock.id)), before)

    def test_mapping_changes_and_invalid_cache_are_not_returned(self):
        refresh([self.stock], self.redis, NOW, lambda *_: (True, CLOSE), lambda *_: self.payload(True))
        self.stock.market_symbol = 'NEW.TO'
        self.assertIsNone(read_quote(self.stock, self.redis, NOW))
        self.redis.set(quote_key(self.stock.id), '{}')
        self.assertIsNone(read_quote(self.stock, self.redis, NOW))

    def test_closed_quote_age_and_fetch_time_are_distinct(self):
        refresh([self.stock], self.redis, NOW, lambda *_: (False, CLOSE), lambda *_: self.payload())
        quote = read_quote(self.stock, self.redis, NOW + timedelta(days=3))
        self.assertGreater(quote['age_seconds'], 3 * 86400)
        self.assertTrue(quote['refresh_overdue'])

    def test_graphql_resolver_reads_cache_without_external_request(self):
        from type.market_quote import resolve_market_quote
        refresh([self.stock], self.redis, NOW, lambda *_: (False, CLOSE), lambda *_: self.payload())
        with patch('market_data.quotes.cache.client', self.redis):
            result = resolve_market_quote(self.stock)
        self.assertEqual(str(result.price), '45.25')
        self.assertEqual(result.quote_time, CLOSE)

    def test_fx_rate_is_cached_once_and_inverted_for_cad_to_usd(self):
        from market_data.quotes import fx_key, read_fx
        self.redis.set(fx_key(), json.dumps(dict(base='USD', target='CAD', rate='1.4',
                       quote_time=CLOSE.isoformat(), last_updated=NOW.isoformat())))
        self.assertEqual(read_fx('USD', 'CAD', self.redis, NOW)['rate'], '1.4')
        self.assertAlmostEqual(float(read_fx('CAD', 'USD', self.redis, NOW)['rate']), 1 / 1.4)
        self.assertIsNone(read_fx('EUR', 'CAD', self.redis, NOW))
