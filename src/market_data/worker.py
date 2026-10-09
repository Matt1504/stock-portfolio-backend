"""Hourly quote refresh. MongoDB is read-only; Redis receives quote snapshots."""
import argparse
import json
import logging
import math
import os
import signal
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from market_data.quotes import mapping, quote_key, read_quote, fx_key, read_fx

logger = logging.getLogger(__name__)


def held_stocks(records, today):
    """Count shares per platform (all profiles), then deduplicate held stocks.

    The recorded Stock Split quantity is additional shares, not a multiplier.
    Spinoff shares are recorded on the child; parent share count is unchanged.
    """
    balances, stocks = {}, {}
    for row in records:
        if not row.stock or not row.platform or not row.transaction_date or row.transaction_date > today:
            continue
        stock = row.stock
        if mapping(stock) is None:
            continue
        activity = row.activity.name if row.activity else ''
        quantity = Decimal(str(row.shares or 0))
        if not quantity.is_finite():
            logger.warning('Skipping non-finite shares on transaction %s', row.id)
            continue
        sign = 1 if activity in ('Buy', 'Transfer In', 'Stock Split', 'Stock Spinoff') else -1 if activity in ('Sell', 'Transfer Out') else 0
        key = (row.platform.id, stock.id)
        balances[key] = balances.get(key, Decimal(0)) + sign * quantity
        stocks[stock.id] = stock
    return [stocks[stock_id] for stock_id in sorted({stock_id for (_, stock_id), shares in balances.items() if shares > 0}, key=str)]


def exchange_state(exchange, now):
    import exchange_calendars as calendars
    calendar = calendars.get_calendar(exchange)
    # Includes holidays, early closes and DST. XNYS is the session calendar for
    # our regular US equity quotes; no pre/post-market prices are requested.
    schedule = calendar.schedule.loc[(now - timedelta(days=20)).date().isoformat():now.date().isoformat()]
    completed = schedule[schedule['close'] <= now]
    last_close = completed.iloc[-1]['close'].to_pydatetime() if len(completed) else None
    opened = bool(((schedule['open'] <= now) & (now < schedule['close'])).any())
    return opened, last_close


def fetch_quote(symbol, currency, opened, last_close, now):
    import yfinance as yf
    ticker = yf.Ticker(symbol)
    # Confirm currency, rather than value a same-named foreign listing as CAD.
    if ticker.fast_info['currency'] != currency:
        raise ValueError('Provider currency differs from stock currency')
    bars = ticker.history(period='5d', interval='1m' if opened else '1d',
                          auto_adjust=False, prepost=False)
    if bars.empty:
        raise ValueError('No price returned')
    last = bars.iloc[-1]
    price = float(last['Close'])
    if not math.isfinite(price) or price <= 0:
        raise ValueError('Invalid market price')
    timestamp = bars.index[-1].to_pydatetime()
    if opened:
        quote_time = timestamp.astimezone(timezone.utc)
        # Preserve older observations, but never call them current/final.
        session_close = None
    else:
        if last_close is None or timestamp.date() != last_close.astimezone(timestamp.tzinfo).date():
            raise ValueError('Final closing price is not yet available')
        quote_time = last_close
        session_close = last_close.isoformat()
    if quote_time > now + timedelta(minutes=1):
        raise ValueError('Provider returned a future quote')
    return {'price': str(price), 'currency': currency, 'quote_time': quote_time.isoformat(),
            'last_updated': now.isoformat(), 'session_close': session_close,
            'source': 'yahoo-finance', 'price_kind': 'last_trade' if opened else 'close'}


def prune_quotes(stocks, client):
    """Remove quotes no longer needed anywhere, after a successful ledger read.

    Only scan our dedicated quote namespace. Other Redis application data and
    last-good quotes for still-held stocks must survive cleanup/provider outages.
    """
    keep = {quote_key(stock.id) for stock in stocks}
    removed = 0
    for key in client.scan_iter(match=quote_key('*'), count=100):
        decoded = key.decode('utf-8') if isinstance(key, bytes) else key
        if decoded not in keep:
            removed += client.delete(key)
    logger.info('Quotes: removed=%s no-longer-held', removed)
    return removed


def refresh(stocks, client, now=None, state=exchange_state, fetch=fetch_quote):
    now = now or datetime.now(timezone.utc)
    states, fetched, skipped, failed = {}, 0, 0, 0
    # Serial requests intentionally bound provider load; timeout is yfinance's
    # default. Failed symbols retry next hour; successful quotes stay untouched.
    for stock in stocks:
        try:
            symbol, exchange, currency = mapping(stock)
            if exchange not in states:
                states[exchange] = state(exchange, now)
            opened, last_close = states[exchange]
            existing = read_quote(stock, client, now)
            if not opened and existing and last_close and existing.get('session_close') == last_close.isoformat():
                skipped += 1
                continue
            quote = fetch(symbol, currency, opened, last_close, now)
            quote.update(symbol=symbol, exchange=exchange, stock_id=str(stock.id))
            client.set(quote_key(stock.id), json.dumps(quote))
            fetched += 1
        except Exception:
            failed += 1
            logger.exception('Quote refresh failed for %s; retaining previous price', stock.ticker)
    logger.info('Quotes: refreshed=%s closed-session-current=%s failed=%s', fetched, skipped, failed)
    return fetched, skipped, failed


def refresh_fx(client, now=None):
    """One shared USD/CAD observation, with inverse conversion at read time."""
    now = now or datetime.now(timezone.utc)
    try:
        states = [exchange_state(exchange, now) for exchange in ('XTSE', 'XNYS')]
        opened = any(state[0] for state in states)
        closing = max(state[1] for state in states if state[1])
        existing = read_fx('USD', 'CAD', client, now)
        if not opened and existing and existing.get('session_close') == closing.isoformat():
            return
        import yfinance as yf
        bars = yf.Ticker('CAD=X').history(period='5d', interval='1m', auto_adjust=False, prepost=False)
        if bars.empty:
            raise ValueError('No USD/CAD rate returned')
        rate = float(bars.iloc[-1]['Close'])
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError('Invalid USD/CAD rate')
        observed = bars.index[-1].to_pydatetime().astimezone(timezone.utc)
        if not opened and observed < closing:
            raise ValueError('Post-session FX observation not yet available')
        client.set(fx_key(), json.dumps(dict(base='USD', target='CAD', rate=str(rate),
                   quote_time=observed.isoformat(), last_updated=now.isoformat(),
                   session_close=None if opened else closing.isoformat(), source='yahoo-finance')))
        logger.info('USD/CAD FX rate refreshed')
    except Exception:
        logger.exception('FX refresh failed; retaining previous rate')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true', help='Refresh eligible holdings once and exit')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    interval = int(os.getenv('MARKET_DATA_INTERVAL_SECONDS', '3600'))
    if interval < 3600:
        raise ValueError('MARKET_DATA_INTERVAL_SECONDS must be at least 3600')
    from database.database import client as mongo_client
    from models.models import Transaction
    from query_loading import materialize_references
    from cache.backend import cache
    if cache.client is None:
        raise RuntimeError('Configure REDIS_URL for the market-data worker')
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    while not stopped.is_set():
        try:
            mongo_client.admin.command('ping')
            cache.client.ping()
            today = datetime.now(timezone.utc).astimezone(ZoneInfo('America/Toronto')).date()
            records = materialize_references(Transaction.objects(stock__ne=None, transaction_date__lte=today))
            stocks = held_stocks(records, today)
            logger.info('Refreshing %s unique currently held stocks', len(stocks))
            prune_quotes(stocks, cache.client)
            refresh(stocks, cache.client)
            if stocks:
                refresh_fx(cache.client)
        except Exception:
            logger.exception('Refresh cycle failed; next cycle will retry')
            if args.once:
                raise
        if args.once:
            return
        stopped.wait(interval)


if __name__ == '__main__':
    main()
