import json
from datetime import datetime, timezone
from decimal import Decimal

from redis.exceptions import RedisError
from cache.backend import cache


def mapping(stock):
    currency = stock.currency.code if stock.currency else None
    asset = stock.asset.name if stock.asset else 'Stock'
    # Amount-only funds/GICs require a different valuation model.
    if asset != 'Stock' or currency not in ('CAD', 'USD') or not stock.ticker:
        return None
    symbol = stock.market_symbol or stock.ticker
    if currency == 'CAD' and not stock.market_symbol and not symbol.endswith('.TO'):
        symbol += '.TO'
    return symbol, stock.market_exchange or ('XTSE' if currency == 'CAD' else 'XNYS'), currency


def quote_key(stock_id):
    # Independent of transaction-cache generations. No short TTL: retain close
    # prices during holidays and keep the last good quote after provider errors.
    return '{}:market-quotes:v1:{}'.format(cache.settings.prefix, stock_id)


def read_quote(stock, client=None, now=None):
    client = client if client is not None else cache.client
    if client is None or mapping(stock) is None:
        return None
    try:
        raw = client.get(quote_key(stock.id))
        if raw is None:
            return None
        quote = json.loads(raw)
        symbol, exchange, currency = mapping(stock)
        if (quote['symbol'], quote['exchange'], quote['currency']) != (symbol, exchange, currency):
            return None  # Stock mapping changed; don't display the old quote.
        price = Decimal(quote['price'])
        if not price.is_finite() or price <= 0:
            return None
        updated = datetime.fromisoformat(quote['last_updated'])
        quoted = datetime.fromisoformat(quote['quote_time'])
        if quote['price_kind'] not in ('close', 'last_trade') or not quote['source']:
            return None
        now = now or datetime.now(timezone.utc)
        # age is exposed separately; a valid closing quote can be days old.
        quote['age_seconds'] = max(0, int((now - quoted).total_seconds()))
        quote['refresh_overdue'] = (now - updated).total_seconds() > 7200
        return quote
    except (RedisError, ValueError, TypeError, KeyError):
        return None


def fx_key():
    return '{}:market-fx:v1:USD:CAD'.format(cache.settings.prefix)


def read_fx(base, target, client=None, now=None):
    if base == target:
        return None  # Consumers skip conversion for identical currencies.
    if {base, target} != {'CAD', 'USD'}:
        return None
    client = client if client is not None else cache.client
    if client is None:
        return None
    try:
        raw = client.get(fx_key())
        if raw is None:
            return None
        quote = json.loads(raw)
        rate = Decimal(quote['rate'])
        if quote['base'] != 'USD' or quote['target'] != 'CAD' or not rate.is_finite() or rate <= 0:
            return None
        observed = datetime.fromisoformat(quote['quote_time'])
        updated = datetime.fromisoformat(quote['last_updated'])
        now = now or datetime.now(timezone.utc)
        if base == 'CAD': rate = Decimal(1) / rate
        return dict(quote, rate=str(rate), base=base, target=target,
                    age_seconds=max(0, int((now - observed).total_seconds())),
                    refresh_overdue=(now - updated).total_seconds() > 7200)
    except (RedisError, ValueError, TypeError, KeyError):
        return None
