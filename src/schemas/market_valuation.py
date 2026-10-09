"""Current valuation from the dated ledger and cached quotes (no provider calls)."""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

import graphene
from graphql import GraphQLError
from models.models import Transaction
from query_loading import materialize_references
from schemas.profiles import personal_records, owned_platform
from market_data.quotes import read_quote, read_fx
from schemas.annualized_return import calculate_annualized_return

ZERO = Decimal(0)


def amount(value):
    value = Decimal(str(value or 0))
    if not value.is_finite():
        raise GraphQLError('Correct non-finite transaction values before valuing the portfolio.')
    return value


def money(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP) if value is not None else None


class MarketValuation(graphene.ObjectType):
    currency = graphene.String(required=True)
    complete = graphene.Boolean(required=True)
    market_value = graphene.Decimal()
    priced_market_value = graphene.Decimal(required=True)
    book_cost = graphene.Decimal(required=True)
    unrealized_gain = graphene.Decimal()
    unrealized_return = graphene.Decimal()
    annualized_return = graphene.Decimal()
    annualized_start_date = graphene.Date()
    annualized_return_note = graphene.String()
    cash_balance = graphene.Decimal()
    total_value = graphene.Decimal()
    quote_time = graphene.DateTime()
    last_updated = graphene.DateTime()
    refresh_overdue = graphene.Boolean(required=True)
    missing_tickers = graphene.List(graphene.String, required=True)
    warnings = graphene.List(graphene.String, required=True)
    current_price = graphene.Decimal()
    price_currency = graphene.String()


def positions_and_cash(records, stock_id=None):
    positions, cash, warnings = {}, ZERO, set()
    for row in sorted(records, key=lambda r: (r.transaction_date or date.min, str(r.id))):
        name = row.activity.name if row.activity else ''
        total, shares = amount(row.total), amount(row.shares)
        stock = row.stock
        relevant = not stock_id or (stock and str(stock.id) == str(stock_id)) or (row.spinoff_source and str(row.spinoff_source.id) == str(stock_id))
        def warn(message):
            if relevant: warnings.add(message)
        if row.total_currency and row.total_currency.code != row.platform.currency.code:
            warn('Some recorded totals use a different currency from their platform; reconcile these transactions.')
        if name in ('Contribution', 'Sell', 'Dividends', 'Interest', 'ETF Rebate', 'GIC Maturity'):
            cash += total
        elif name in ('Buy', 'Withdrawal', 'Withholding Tax', 'Service Fee', 'SEC Fee'):
            cash -= total
        elif name in ('Transfer In', 'Transfer Out') and not stock and not shares:
            cash += total if name == 'Transfer In' else -total
        if not stock:
            continue
        asset = stock.asset.name if stock.asset else 'Stock'
        if asset == 'GIC' and name in ('Buy', 'GIC Maturity'):
            cash -= amount(row.fee)
        key = (row.platform.id, stock.id)
        p = positions.setdefault(key, dict(stock=stock, shares=ZERO, cost=ZERO))
        if asset == 'GIC':
            if name == 'Buy': p['cost'] += total
            elif name == 'GIC Maturity': p['cost'] = max(ZERO, p['cost'] - amount(row.principal_returned))
            continue
        if name == 'Stock Spinoff':
            allocation = amount(row.allocated_book_cost)
            p['shares'] += shares
            p['cost'] += allocation
            source = positions.get((row.platform.id, row.spinoff_source.id)) if row.spinoff_source else None
            if source:
                if allocation > source['cost']:
                    warn('Spinoff allocation exceeds the original holding cost.')
                source['cost'] = max(ZERO, source['cost'] - allocation)
        elif name in ('Buy', 'Transfer In'):
            p['shares'] += shares
            p['cost'] += total if row.total is not None else amount(row.price) * shares + amount(row.fee)
        elif name == 'Stock Split':
            p['shares'] += shares
        elif name in ('Sell', 'Transfer Out'):
            if shares:
                if shares > p['shares']:
                    warn('Some disposals exceed recorded share holdings.')
                if p['shares'] > 0:
                    p['cost'] -= p['cost'] * min(shares, p['shares']) / p['shares']
                p['shares'] -= shares
                if p['shares'] <= 0: p['cost'] = ZERO
            elif name == 'Transfer Out':
                p['cost'] = max(ZERO, p['cost'] - total)
            else:
                warn('Amount-only fund sales have no recorded disposal cost.')
    return list(positions.values()), cash, warnings


def calculate_valuation(records, currency, stock_id=None, quote_reader=read_quote, fx_reader=read_fx, valuation_date=None):
    positions, cash, warnings = positions_and_cash(records, stock_id)
    selected = [p for p in positions if not stock_id or str(p['stock'].id) == str(stock_id)]
    current = [p for p in selected if p['shares'] > 0 or p['cost'] > 0]
    cost = sum((p['cost'] for p in current), ZERO)
    value, missing, timestamps, updated = ZERO, set(), [], []
    quotes, fx_quotes = {}, {}
    overdue = False
    for p in current:
        stock = p['stock']
        if stock.id not in quotes: quotes[stock.id] = quote_reader(stock)
        quote = quotes[stock.id]
        if quote is None or p['shares'] <= 0:
            missing.add(stock.ticker or stock.name or str(stock.id))
            continue
        rate = Decimal(1)
        if quote['currency'] != currency:
            pair = (quote['currency'], currency)
            if pair not in fx_quotes: fx_quotes[pair] = fx_reader(*pair)
            fx = fx_quotes[pair]
            if fx is None:
                missing.add(stock.ticker or str(stock.id))
                warnings.add('Current FX rate is unavailable for {} to {}.'.format(*pair))
                continue
            rate = amount(fx['rate'])
            timestamps.append(datetime.fromisoformat(fx['quote_time']))
            updated.append(datetime.fromisoformat(fx['last_updated']))
            overdue |= fx['refresh_overdue']
        value += p['shares'] * amount(quote['price']) * rate
        timestamps.append(datetime.fromisoformat(quote['quote_time']))
        updated.append(datetime.fromisoformat(quote['last_updated']))
        overdue |= quote['refresh_overdue']
    complete = not missing and not warnings
    gain = value - cost if complete else None
    price = next((q for sid, q in quotes.items() if q and str(sid) == str(stock_id)), None)
    valuation_date = valuation_date or datetime.now(timezone.utc).astimezone(ZoneInfo('America/Toronto')).date()
    annualized, start, note = calculate_annualized_return(
        records, (value if stock_id else value + cash) if complete else None, valuation_date, stock_id)
    return MarketValuation(currency=currency, complete=complete, market_value=money(value) if complete else None,
                           priced_market_value=money(value), book_cost=money(cost), unrealized_gain=money(gain),
                           unrealized_return=money(gain / cost * 100) if gain is not None and cost > 0 else None,
                           annualized_return=money(annualized), annualized_start_date=start, annualized_return_note=note,
                           cash_balance=money(cash) if not stock_id else None,
                           total_value=money(value + cash) if complete and not stock_id else None,
                           quote_time=min(timestamps) if timestamps else None,
                           last_updated=min(updated) if updated else None,
                           refresh_overdue=overdue, missing_tickers=sorted(missing), warnings=sorted(warnings),
                           current_price=amount(price['price']) if price else None,
                           price_currency=price['currency'] if price else None)


def market_valuation(profile_id, currency, platform=None, stock=None, account=None):
    if currency not in ('CAD', 'USD'):
        raise GraphQLError('Select CAD or USD for valuation.')
    rows = personal_records(Transaction, profile_id)
    if platform:
        owned_platform(profile_id, platform)
        rows = rows.filter(platform=platform)
    if account: rows = rows.filter(account=account)
    today = datetime.now(timezone.utc).astimezone(ZoneInfo('America/Toronto')).date()
    # Always load the complete scoped ledger before selecting a stock: spinoff
    # source cost and transfer history are needed to preserve remaining basis.
    records = [r for r in materialize_references(rows.filter(transaction_date__lte=today))
               if r.platform.currency and r.platform.currency.code == currency]
    return calculate_valuation(records, currency, stock)
