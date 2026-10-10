"""Recorded financial analytics, independent of presentation and market prices.

Decimal arithmetic and date/creation ordering preserve average-cost basis across
sales, splits, spinoffs and in-kind transfers. Amount-only disposal basis remains
unknown rather than treating sale proceeds as returned principal.
"""
from collections import defaultdict
from datetime import date
from decimal import Decimal

import graphene
from mongoengine import Q
from models.models import Transaction, ContributionLimit, Account, Activity
from query_loading import materialize_references
from schemas.profiles import personal_records, owned_platform
from schemas.analytics_ledger import amount, money, ledger

ZERO = Decimal(0)


class Statistic(graphene.ObjectType):
    title = graphene.String(required=True)
    value = graphene.Decimal()
    text = graphene.String()
    monetary = graphene.Boolean(required=True)
    holding = graphene.Boolean(required=True)


class AnalyticsPoint(graphene.ObjectType):
    name = graphene.String(required=True)
    value = graphene.Decimal(required=True)
    value_1 = graphene.Decimal()
    shares = graphene.Decimal()
    sell_shares = graphene.Decimal()


class FinancialAnalytics(graphene.ObjectType):
    currency = graphene.String(required=True)
    statistics = graphene.List(Statistic, required=True)
    distribution = graphene.List(AnalyticsPoint, required=True)
    account_distribution = graphene.List(AnalyticsPoint, required=True)
    book_cost_history = graphene.List(AnalyticsPoint, required=True)
    trade_history = graphene.List(AnalyticsPoint, required=True)
    income_history = graphene.List(AnalyticsPoint, required=True)
    issues = graphene.List(graphene.String, required=True)



def calculate_analytics(records, currency, stock_id=None, asset_type='Stock'):
    result = ledger(records, stock_id)
    positions = result['positions']
    holdings = {}
    account_costs = defaultdict(lambda: ZERO)
    for p in positions:
        key = str(p['stock'].id)
        h = holdings.setdefault(key, dict(stock=p['stock'], shares=ZERO, cost=ZERO))
        h['shares'] += p['shares']; h['cost'] += p['cost']
        account_costs[getattr(p['account'], 'code', 'Unknown') or 'Unknown'] += p['cost']
    costs = [h for h in holdings.values() if h['cost'] > 0]
    largest = max(costs, key=lambda h: h['cost'], default=None)
    smallest = min(costs, key=lambda h: h['cost'], default=None)
    shares_owned = sum((h['shares'] for h in holdings.values()), ZERO)
    book_cost = sum((h['cost'] for h in holdings.values()), ZERO)
    sums = defaultdict(lambda: ZERO)
    last_buy = last_sell = None
    trade, income, cost_history = {}, {}, {}
    net_deposits = ZERO
    selected_rows = [r for r in records if not stock_id or (r.stock and str(r.stock.id) == str(stock_id))]
    for r in selected_rows:
        name = r.activity.name if r.activity else ''
        total, fee, shares = amount(r.total), amount(r.fee), amount(r.shares)
        sums[name] += total
        sums['tradingFees'] += fee
        if r.stock and r.stock.asset and r.stock.asset.name == 'GIC': sums['gicFees'] += fee
        if name == 'GIC Maturity':
            sums['principal'] += amount(r.principal_returned)
            sums['income'] += amount(r.interest_earned)
        if name in ('Dividends', 'Interest'): sums['income'] += total
        elif name == 'Withholding Tax' and r.stock: sums['income'] -= total
        if name == 'Buy':
            sums['bought'] += shares
            if r.transaction_date: last_buy = max(last_buy or date.min, r.transaction_date)
        if name == 'Sell':
            sums['sold'] += shares
            if r.transaction_date: last_sell = max(last_sell or date.min, r.transaction_date)
        if not r.transaction_date: continue
        day = r.transaction_date.isoformat()
        if name in ('Buy', 'Sell', 'GIC Maturity'):
            point = trade.setdefault(day, dict(name=day, value=ZERO, value_1=None, shares=None, sell_shares=None))
            if name == 'Buy':
                point['value'] += total; point['shares'] = (point['shares'] or ZERO) + shares
            else:
                point['value_1'] = (point['value_1'] or ZERO) + (amount(r.principal_returned) if name == 'GIC Maturity' else total)
                if name == 'Sell': point['sell_shares'] = (point['sell_shares'] or ZERO) + shares
        if name in ('Dividends', 'Interest', 'GIC Maturity', 'Withholding Tax') and (name != 'Withholding Tax' or r.stock):
            point = income.setdefault(day, dict(name=day, value=ZERO, value_1=None))
            if name == 'Withholding Tax': point['value_1'] = (point['value_1'] or ZERO) - total
            else: point['value'] += amount(r.interest_earned) if name == 'GIC Maturity' else total
    for r, cost in result['history']:
        if not r.transaction_date: continue
        name, total = r.activity.name, amount(r.total)
        if name in ('Contribution', 'Transfer In'): net_deposits += total
        elif name in ('Withdrawal', 'Transfer Out'): net_deposits -= total
        cost_history[r.transaction_date.isoformat()] = dict(name=r.transaction_date.isoformat(), value=cost, value_1=net_deposits)
    gain = result['gain']
    fees_paid = sums['tradingFees'] + sums['Service Fee'] + sums['SEC Fee'] + sums['Withholding Tax']
    profit = gain + sums['income'] + sums['ETF Rebate'] - sums['gicFees'] - sums['Service Fee'] - sums['SEC Fee'] if gain is not None else None
    stats = []
    def add(title, value, monetary=True, text=None, holding=False):
        stats.append(Statistic(title=title, value=value, text=text, monetary=monetary, holding=holding))
    if stock_id:
        is_fund = asset_type in ('Index Fund', 'Mutual Fund')
        amount_only = is_fund and (not any(r.activity.name in ('Buy', 'Sell') for r in selected_rows) or any(not amount(r.shares) for r in selected_rows if r.activity.name in ('Buy', 'Sell')))
        if asset_type == 'GIC':
            add('Book Cost', book_cost); add('Interest Earned', sums['income']); add('Realized Profit/Loss', sums['income'] - sums['tradingFees'])
            add('Total Invested', sums['Buy']); add('Principal Returned', sums['principal']); add('Last Buy Date', None, False, last_buy.isoformat() if last_buy else '—')
        else:
            add('Book Cost', book_cost)
            add('Average Cost per Share', book_cost / shares_owned if shares_owned else None)
            add('Realized Profit/Loss', None if amount_only else profit)
            add('Realized Gain/Loss', None if amount_only else gain)
            add('Share(s) Owned', shares_owned, False); add('Total Shares Bought', sums['bought'], False); add('Total Shares Sold', sums['sold'], False)
            add('Dividends/Interest Earned', sums['income']); add('Total Invested', sums['Buy']); add('Sale Proceeds', sums['Sell']); add('Total Fees Paid', sums['tradingFees'])
            add('Last Buy Date', None, False, last_buy.isoformat() if last_buy else '—')
            if not is_fund: add('Last Sell Date', None, False, last_sell.isoformat() if last_sell else '—')
            if is_fund: stats = [s for s in stats if s.title not in ('Average Cost per Share', 'Share(s) Owned', 'Total Shares Bought', 'Total Shares Sold', 'Dividends/Interest Earned', 'Total Invested')]
            if asset_type == 'Index Fund': stats = [s for s in stats if s.title in ('Book Cost', 'Realized Gain/Loss', 'Sale Proceeds', 'Last Buy Date')]
    else:
        for title, value in [('Total Book Cost',book_cost), ('Net Deposits',sums['Contribution']+sums['Transfer In']-sums['Transfer Out']-sums['Withdrawal']), ('Realized Profit',profit), ('Realized Gain/Loss',gain), ('Fees Paid',fees_paid), ('Dividends/Interest Earned',sums['income']), ('Amount Transferred In',sums['Transfer In']), ('Amount Transferred Out',sums['Transfer Out']), ('Amount Contributed',sums['Contribution']), ('Amount Withdrawn',sums['Withdrawal']), ('Cash Balance',result['cash'])]: add(title,value)
        add('Total Share(s) Owned',shares_owned,False); add('Unique Share(s) Owned',Decimal(sum(h['shares'] > 0 for h in holdings.values())),False)
        for title,h in [('Largest Holding',largest),('Smallest Holding',smallest)]: add(title,h['cost'] if h else None,False,h['stock'].ticker if h else '—',bool(h))
    if stock_id:
        distribution = [AnalyticsPoint(name='{} ({})'.format(getattr(p['platform'], 'name', ''), getattr(p['account'], 'code', '')), value=p['cost'], shares=p['shares']) for p in positions if p['cost'] > 0]
    else:
        distribution = [AnalyticsPoint(name=h['stock'].ticker,value=h['cost'],shares=h['shares']) for h in costs]

    return FinancialAnalytics(currency=currency, statistics=stats, distribution=distribution, account_distribution=[AnalyticsPoint(name=k,value=v) for k,v in sorted(account_costs.items())], book_cost_history=[AnalyticsPoint(**p) for _,p in sorted(cost_history.items())], trade_history=[AnalyticsPoint(**p) for _,p in sorted(trade.items())], income_history=[AnalyticsPoint(**p) for _,p in sorted(income.items())], issues=result['issues'])


def scoped_records(info, profile_id, account=None, platform=None, stock=None):
    """Request-local reuse avoids reading a scope twice for table + analytics."""
    context = info.context
    key = (str(profile_id), str(account or ''), str(platform or ''), str(stock or ''))
    if isinstance(context, dict): memo = context.setdefault('_financial_records', {})
    elif context is not None:
        memo = getattr(context, '_financial_records', None)
        if memo is None:
            memo = {}; setattr(context, '_financial_records', memo)
    else: memo = {}
    if key not in memo:
        if account and not platform and not stock:
            from cache.queries import transactions_by_account, bypass_cache
            memo[key] = transactions_by_account(account, profile_id, force_refresh=bypass_cache(info))
        else:
            rows = personal_records(Transaction, profile_id)
            if platform:
                owned_platform(profile_id, platform)
                rows = rows.filter(platform=platform)
            if account: rows = rows.filter(account=account)
            if stock: rows = rows.filter(Q(stock=stock) | Q(spinoff_source=stock))
            memo[key] = materialize_references(rows)
    return memo[key]


def financial_analytics(info, profile_id, account=None, platform=None, stock=None):
    from models.models import Stock
    records = scoped_records(info, profile_id, account, platform, stock)
    selected = Stock.objects.get(id=stock) if stock else None
    asset = selected.asset.name if selected and selected.asset else 'Stock'
    codes = sorted({'CAD','USD'} | {r.platform.currency.code for r in records if r.platform.currency})
    return [calculate_analytics([r for r in records if r.platform.currency and r.platform.currency.code == code], code, stock, asset) for code in codes]


class ContributionAnalytics(graphene.ObjectType):
    account_id = graphene.ID(required=True)
    contribution = graphene.Decimal(required=True)
    limit = graphene.Decimal()
    percentage = graphene.Decimal()
    history = graphene.List(AnalyticsPoint, required=True)


def contribution_analytics(profile_id):
    records = materialize_references(personal_records(Transaction, profile_id).filter(activity__in=Activity.objects(name='Contribution')))
    limits = materialize_references(personal_records(ContributionLimit, profile_id))
    results = []
    for account in Account.objects:
        rows = sorted([r for r in records if r.account and r.account.id == account.id],key=lambda r:(r.transaction_date or date.min,str(r.id)))
        saved = sorted([l for l in limits if l.account.id == account.id],key=lambda l:l.yearEnd or date.min)
        amount_total = sum((amount(r.total) for r in rows),ZERO)
        limited = account.has_contribution_limit is not False
        limit_total = sum((amount(l.amount) for l in saved),ZERO) if limited else None
        history, cumulative = {}, ZERO
        for r in rows:
            cumulative += amount(r.total)
            if r.transaction_date:
                # Include all limits effective by this date, and the first saved
                # limit for early contributions, matching the existing overview.
                effective = [l for l in saved if not l.yearEnd or l.yearEnd >= r.transaction_date]
                next_limit = effective[0] if effective else (saved[-1] if saved else None)
                history[r.transaction_date.isoformat()] = AnalyticsPoint(name=r.transaction_date.isoformat(),value=cumulative,value_1=sum((amount(l.amount) for l in saved if next_limit and (l.yearEnd or date.min) <= (next_limit.yearEnd or date.min)),ZERO) if limited else None)
        results.append(ContributionAnalytics(account_id=str(account.id),contribution=amount_total,limit=limit_total,percentage=money(amount_total/limit_total*100) if limit_total and limit_total>0 else (ZERO if limited else None),history=list(history.values())))
    return results
