"""Shared recorded holdings/cash ledger for analytics and market valuation."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from graphql import GraphQLError

ZERO = Decimal(0)


def amount(value):
    value = Decimal(str(value or 0))
    if not value.is_finite():
        raise GraphQLError('Correct non-finite transaction values before calculating portfolio statistics.')
    return value


def money(value):
    return value.quantize(Decimal('.01'), rounding=ROUND_HALF_UP) if value is not None else None


def ledger(records, selected_stock=None):
    positions, history, issues = {}, [], []
    gain, incomplete, cash = ZERO, False, ZERO
    for row in sorted(records, key=lambda r: (r.transaction_date or date.min, str(r.id))):
        name = row.activity.name if row.activity else ''
        total, shares, fee = amount(row.total), amount(row.shares), amount(row.fee)
        stock = row.stock
        if name in ('Contribution', 'Sell', 'Dividends', 'Interest', 'ETF Rebate', 'GIC Maturity'): cash += total
        elif name in ('Buy', 'Withdrawal', 'Withholding Tax', 'Service Fee', 'SEC Fee'): cash -= total
        elif name in ('Transfer In', 'Transfer Out') and not stock and not shares: cash += total if name == 'Transfer In' else -total
        asset = stock.asset.name if stock and stock.asset else 'Stock'
        if asset == 'GIC' and name in ('Buy', 'GIC Maturity'): cash -= fee
        if stock:
            key = (str(row.platform.id), str(stock.id))
            p = positions.setdefault(key, dict(stock=stock, platform=row.platform, account=getattr(row, 'account', None), shares=ZERO, cost=ZERO))
            relevant = not selected_stock or str(stock.id) == str(selected_stock)
            if asset == 'GIC':
                if name == 'Buy': p['cost'] += total
                elif name == 'GIC Maturity': p['cost'] = max(ZERO, p['cost'] - amount(row.principal_returned))
            elif name == 'Stock Spinoff':
                allocation = amount(row.allocated_book_cost)
                p['shares'] += shares
                p['cost'] += allocation
                source = positions.get((str(row.platform.id), str(row.spinoff_source.id))) if row.spinoff_source else None
                if source:
                    if allocation > source['cost'] and (not selected_stock or str(row.spinoff_source.id) == str(selected_stock)):
                        issues.append('Spinoff allocation exceeds the original holding cost.')
                    source['cost'] = max(ZERO, source['cost'] - allocation)
            elif name in ('Buy', 'Transfer In') and (shares or asset in ('Index Fund', 'Mutual Fund')):
                p['shares'] += shares
                p['cost'] += total if row.total is not None else amount(row.price) * shares + fee
            elif name == 'Stock Split': p['shares'] += shares
            elif name in ('Sell', 'Transfer Out'):
                if shares:
                    if name == 'Sell' and relevant:
                        if shares > p['shares']: incomplete = True
                        elif p['shares'] > 0: gain += total - p['cost'] * shares / p['shares']
                    if p['shares'] > 0: p['cost'] -= p['cost'] * min(shares, p['shares']) / p['shares']
                    p['shares'] -= shares
                    if abs(p['shares']) < Decimal('0.00000001'): p['shares'] = ZERO
                    if p['shares'] <= 0: p['cost'] = ZERO
                elif name == 'Transfer Out': p['cost'] = max(ZERO, p['cost'] - total)
                elif relevant: incomplete = True
        cost = sum((p['cost'] for p in positions.values() if p['shares'] >= 0 and (not selected_stock or str(p['stock'].id) == str(selected_stock))), ZERO)
        history.append((row, cost))
    selected = [p for p in positions.values() if not selected_stock or str(p['stock'].id) == str(selected_stock)]
    for p in selected:
        if p['shares'] < 0:
            issues.append('{} ({}): the recorded share balance is −{}.'.format(p['stock'].ticker, getattr(p['platform'], 'name', ''), -p['shares']))
    if incomplete: issues.append('Some sales have missing holdings or no recorded disposal cost; realized gain/loss is unavailable.')
    return dict(positions=[p for p in selected if p['shares'] >= 0 and (p['shares'] > 0 or p['cost'] > 0)], history=history, gain=None if incomplete else gain, cash=cash, issues=issues)

