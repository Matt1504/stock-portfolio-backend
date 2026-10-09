"""Provider/calendar checks run in the market-data-test Docker target."""
import importlib.util
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, Mock

import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from market_data.worker import exchange_state, fetch_quote


@unittest.skipUnless(importlib.util.find_spec('exchange_calendars'), 'Worker-only dependency')
class ExchangeCalendarTests(unittest.TestCase):
    def test_canadian_holiday_does_not_close_us_market(self):
        now = datetime(2026, 10, 12, 16, tzinfo=timezone.utc)
        self.assertFalse(exchange_state('XTSE', now)[0])
        self.assertTrue(exchange_state('XNYS', now)[0])

    def test_us_early_close(self):
        now = datetime(2026, 11, 27, 19, tzinfo=timezone.utc)
        self.assertFalse(exchange_state('XNYS', now)[0])
        self.assertEqual(exchange_state('XNYS', now)[1].hour, 18)
        self.assertTrue(exchange_state('XTSE', now)[0])


@unittest.skipUnless(importlib.util.find_spec('yfinance'), 'Worker-only dependency')
class QuoteProviderTests(unittest.TestCase):
    def test_closed_price_is_unadjusted_and_matches_latest_session(self):
        close = datetime(2026, 10, 8, 20, tzinfo=timezone.utc)
        ticker = Mock(fast_info={'currency': 'CAD'})
        ticker.history.return_value = pd.DataFrame({'Close': [45.5]}, index=pd.DatetimeIndex(['2026-10-08'], tz='America/Toronto'))
        with patch('yfinance.Ticker', return_value=ticker):
            quote = fetch_quote('XEQT.TO', 'CAD', False, close, close)
        self.assertEqual(quote['quote_time'], close.isoformat())
        self.assertFalse(ticker.history.call_args.kwargs['auto_adjust'])
        self.assertFalse(ticker.history.call_args.kwargs['prepost'])

    def test_old_close_is_not_marked_final_for_new_session(self):
        close = datetime(2026, 10, 8, 20, tzinfo=timezone.utc)
        ticker = Mock(fast_info={'currency': 'CAD'})
        ticker.history.return_value = pd.DataFrame({'Close': [45.5]}, index=pd.DatetimeIndex(['2026-10-07'], tz='America/Toronto'))
        with patch('yfinance.Ticker', return_value=ticker), self.assertRaisesRegex(ValueError, 'not yet available'):
            fetch_quote('XEQT.TO', 'CAD', False, close, close)

    def test_currency_mismatch_rejected(self):
        ticker = Mock(fast_info={'currency': 'USD'})
        with patch('yfinance.Ticker', return_value=ticker), self.assertRaisesRegex(ValueError, 'currency'):
            fetch_quote('XEQT.TO', 'CAD', False, None, datetime.now(timezone.utc))
