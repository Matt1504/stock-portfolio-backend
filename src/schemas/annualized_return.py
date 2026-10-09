"""Money-weighted annualized return from dated cash flows, in one currency.

XIRR solves sum(flow / (1 + rate) ** (days / 365)) == 0. Work in
log(1 + rate) to support negative returns and avoid discount overflow.
"""
from collections import defaultdict
from decimal import Decimal
import math

ZERO = Decimal(0)


def xirr(flows):
    grouped = defaultdict(lambda: ZERO)
    for day, value in flows:
        value = Decimal(str(value))
        if day is None or not value.is_finite():
            return None, 'Annualized return needs dated, finite cash flows.'
        grouped[day] += value
    values = sorted((day, value) for day, value in grouped.items() if value)
    if len(values) < 2 or not any(v < 0 for _, v in values) or not any(v > 0 for _, v in values):
        return None, 'Annualized return needs investment and return cash flows on different dates.'
    start = values[0][0]
    scale = max(abs(v) for _, v in values)
    terms = [(float(v / scale), (day - start).days / 365.0) for day, v in values]

    def npv(log_rate):
        exponents = [-years * log_rate for _, years in terms]
        offset = max(exponents)
        return math.fsum(v * math.exp(exponent - offset) for (v, _), exponent in zip(terms, exponents))

    roots = []
    # Bounded scanning detects multiple bracketed roots rather than choosing an
    # arbitrary result from a Newton guess. This covers rates near -100% through
    # very large positive rates. Ill-conditioned/unbracketed results stay blank.
    left, left_value = -20.0, npv(-20.0)
    for index in range(1, 1601):
        right = -20.0 + index * .025
        right_value = npv(right)
        if left_value == 0:
            roots.append(left)
        if left_value * right_value < 0:
            lo, hi, flo = left, right, left_value
            for _ in range(80):
                mid = (lo + hi) / 2
                fm = npv(mid)
                if fm == 0 or hi - lo < 1e-13:
                    break
                if flo * fm < 0:
                    hi = mid
                else:
                    lo, flo = mid, fm
            roots.append(mid)
        left, left_value = right, right_value
    if left_value == 0:
        roots.append(left)
    unique = []
    for root in roots:
        if not unique or abs(root - unique[-1]) > 1e-8:
            unique.append(root)
    if len(unique) != 1:
        return None, 'These cash flows do not produce one stable annualized return.'
    root = unique[0]
    if abs(npv(root)) > 1e-8:
        return None, 'These cash flows do not produce a stable annualized return.'
    return Decimal(str(math.expm1(root))) * 100, None


def calculate_annualized_return(records, ending_value, end_date, stock_id=None):
    """Account flows: deposits/withdrawals/boundary transfers, ending cash+assets.

    Stock flows: purchases/sales/stock-linked income/tax/boundary transfers,
    ending holdings only. Internal linked transfers cancel within the scope.
    """
    if ending_value is None:
        return None, None, 'Annualized return needs a complete market valuation.'
    if ending_value < 0:
        return None, None, 'Annualized return needs a non-negative ending value.'
    relevant = [r for r in records if not stock_id or
                (r.stock and str(r.stock.id) == str(stock_id)) or
                (r.spinoff_source and str(r.spinoff_source.id) == str(stock_id))]
    transfers = defaultdict(list)
    for row in relevant:
        if row.activity and row.activity.name in ('Transfer In', 'Transfer Out') and getattr(row, 'transfer_pair', None):
            transfers[row.transfer_pair].append(row)
    internal = set()
    for pair, legs in transfers.items():
        if len(legs) != 2:
            continue
        a, b = legs
        if ({a.activity.name, b.activity.name} == {'Transfer In', 'Transfer Out'} and
            a.platform.id != b.platform.id and a.transaction_date == b.transaction_date and
            (a.stock.id if a.stock else None) == (b.stock.id if b.stock else None) and
            a.shares == b.shares and a.total == b.total and
            getattr(a, 'transfer_market_value', None) == getattr(b, 'transfer_market_value', None)):
            internal.add(pair)
        else:
            return None, None, 'Reconcile inconsistent linked transfers before calculating annualized return.'
    flows, estimated = [], False
    for row in relevant:
        name = row.activity.name if row.activity else ''
        if row.transaction_date and row.transaction_date > end_date:
            continue
        total = Decimal(str(row.total or 0))
        flow = ZERO
        if name in ('Transfer In', 'Transfer Out'):
            if getattr(row, 'transfer_pair', None) in internal:
                continue
            if row.stock:
                value = getattr(row, 'transfer_market_value', None)
                if value is None:
                    return None, None, 'Annualized return needs market values for assets transferred across this view’s boundary.'
                total = Decimal(str(value))
                estimated |= getattr(row, 'transfer_market_value_source', None) == 'yahoo_close'
            flow = -total if name == 'Transfer In' else total
        elif stock_id:
            if name == 'Stock Spinoff':
                return None, None, 'Stock annualized return needs the spinoff-date market value; book-cost allocation is not a market value.'
            if name == 'Buy': flow = -total
            elif name in ('Sell', 'Dividends', 'Interest', 'ETF Rebate', 'GIC Maturity'): flow = total
            elif name == 'Withholding Tax': flow = -total
        else:
            if name == 'Contribution': flow = -total
            elif name == 'Withdrawal': flow = total
        if flow:
            if not row.transaction_date or not flow.is_finite():
                return None, None, 'Annualized return needs dated, finite cash flows.'
            flows.append((row.transaction_date, flow))
    start = min((day for day, value in flows if value < 0), default=None)
    result, note = xirr([*flows, (end_date, ending_value)])
    if note is None and estimated:
        note = 'Estimated using transfer-date closing prices for historical asset transfers.'
    return result, start, note
