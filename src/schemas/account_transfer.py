"""Transfer the dated ledger, optionally close, and preserve original history."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4
from bson import ObjectId
from graphql import GraphQLError
from graphene import ObjectType, InputObjectType, ID, String, Decimal as GDecimal, List, Field
from models.models import Transaction, Platform, Activity
from query_loading import materialize_references
from schemas.profiles import owned_platform

ZERO = Decimal(0)
def amount(value):
    result = Decimal(str(value or 0))
    if not result.is_finite():
        fail("Correct non-finite transaction amounts before transferring.")
    return result
def money(value):
    return value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
def fail(message):
    raise GraphQLError(message, extensions={'code': 'ACCOUNT_TRANSFER_INVALID'})

class TransferAsset(ObjectType):
    stock_id = String(required=True)
    ticker = String()
    shares = GDecimal(required=True)
    book_cost = GDecimal(required=True)

class TransferAssetValueInput(InputObjectType):
    stock_id = ID(required=True)
    market_value = GDecimal(required=True)


def asset_values(assets, values):
    expected = {str(p['stock'].id) for p in assets}
    result = {}
    for item in values or []:
        stock_id = str(item['stock_id'])
        if stock_id not in expected or stock_id in result:
            fail('Provide one market value for each asset in the current transfer preview.')
        value = amount(item['market_value'])
        if value < 0:
            fail('Transfer market values cannot be negative.')
        result[stock_id] = money(value)
    if set(result) != expected:
        fail('Enter the total market value in the account currency for every transferred asset.')
    return result

class AccountTransferPreview(ObjectType):
    cash = GDecimal(required=True)
    assets = List(TransferAsset, required=True)
    currency = String(required=True)


def validate_closed_platform(transaction):
    closing = transaction.platform.closed_at
    if closing and (not transaction.transaction_date or transaction.transaction_date > closing):
        fail('This platform closed on {}. Only historical transactions dated on or before that date are allowed.'.format(closing))


def protect_transfer(transaction):
    if transaction.transfer_batch:
        fail('This transaction belongs to a linked account transfer and cannot be edited or deleted individually.')


def ledger(records):
    positions = {}
    cash = ZERO
    for row in sorted(records, key=lambda row: (row.transaction_date or date.min, str(row.id))):
        activity = row.activity.name if row.activity else ''
        total, shares = amount(row.total), amount(row.shares)
        stock = row.stock
        if activity in ('Contribution', 'Sell', 'Dividends', 'Interest', 'ETF Rebate', 'GIC Maturity'):
            cash += total
        elif activity in ('Buy', 'Withdrawal', 'Withholding Tax', 'Service Fee', 'SEC Fee'):
            cash -= total
        elif activity in ('Transfer In', 'Transfer Out') and not stock:
            cash += total if activity == 'Transfer In' else -total
        if stock and stock.asset and stock.asset.name == 'GIC' and activity in ('Buy', 'GIC Maturity'):
            cash -= amount(row.fee)
        if not stock:
            continue
        position = positions.setdefault(stock.id, {'stock': stock, 'shares': ZERO, 'cost': ZERO, 'gics': set()})
        if stock.asset and stock.asset.name == 'GIC':
            if activity == 'Buy':
                position['gics'].add(row.id)
            elif activity == 'GIC Maturity' and row.gic_purchase:
                position['gics'].discard(row.gic_purchase.id)
            continue
        if activity in ('Buy', 'Transfer In'):
            position['shares'] += shares
            position['cost'] += total
        elif activity == 'Stock Split':
            position['shares'] += shares
        elif activity == 'Stock Spinoff':
            allocation = amount(row.allocated_book_cost)
            position['shares'] += shares
            position['cost'] += allocation
            if row.spinoff_source:
                source = positions.get(row.spinoff_source.id)
                if not source or allocation > source['cost']:
                    fail('Correct the spinoff cost allocation before transferring this platform.')
                source['cost'] -= allocation
        elif activity in ('Sell', 'Transfer Out'):
            if shares:
                if shares > position['shares']:
                    fail('Correct the negative share balance for {} before transferring.'.format(stock.ticker))
                position['cost'] -= position['cost'] * shares / position['shares']
                position['shares'] -= shares
                if not position['shares']:
                    position['cost'] = ZERO
            elif activity == 'Transfer Out':
                position['cost'] -= total
            else:
                fail('A sale of an amount-only fund has an unknown disposal cost. Correct its cost basis before transferring.')
    for position in positions.values():
        if position['gics']:
            fail('Open GICs cannot be transferred yet. Their maturity is linked to the original purchase; mature them first or transfer the other assets separately.')
        if position['shares'] < 0 or position['cost'] < 0:
            fail('Correct negative holdings before transferring this platform.')
    if money(cash) < 0:
        fail('The recorded cash balance is negative ({}). Reconcile it before transferring and closing this platform.'.format(money(cash)))
    return money(cash), [p for p in positions.values() if p['shares'] or p['cost']]


def prepare(profile_id, source_id, destination_id, transfer_date, session=None, close_original_account=True):
    source = owned_platform(profile_id, source_id)
    destination = owned_platform(profile_id, destination_id)
    if source.id == destination.id:
        fail('Choose a different destination platform.')
    if source.account != destination.account or source.currency != destination.currency:
        fail('Transfers must use the same profile, account type and currency.')
    if source.closed_at or destination.closed_at:
        fail('Both platforms must be open to transfer and close an account.')
    if not transfer_date or transfer_date > date.today():
        fail('Choose a transfer date on or before today.')
    raw = Transaction._get_collection().find({'platform': source.id}, session=session)
    records = materialize_references(Transaction._from_son(row) for row in raw)
    if any(not row.transaction_date for row in records):
        fail('The source has undated transactions. Add their dates before transferring.')
    if close_original_account and any(row.transaction_date > transfer_date for row in records):
        fail('The source has transactions after the transfer date. Leave Close Original Account unchecked to keep it open, or choose a date after its final transaction.')
    records = [row for row in records if row.transaction_date <= transfer_date]
    monetary = {"Buy", "Sell", "Contribution", "Withdrawal", "Dividends", "Interest", "ETF Rebate", "Withholding Tax", "Service Fee", "SEC Fee", "GIC Maturity", "Transfer In", "Transfer Out"}
    if any(row.activity and row.activity.name in monetary and row.total is None for row in records):
        fail("Complete missing transaction totals before transferring this platform.")
    cash, assets = ledger(records)
    return source, destination, cash, assets


def preview(profile_id, source_id, destination_id, transfer_date, close_original_account=True):
    source, _, cash, assets = prepare(profile_id, source_id, destination_id, transfer_date, close_original_account=close_original_account)
    return AccountTransferPreview(cash=cash, currency=source.currency.code, assets=[TransferAsset(stock_id=str(p['stock'].id), ticker=p['stock'].ticker, shares=p['shares'], book_cost=money(p['cost'])) for p in assets])


def commit_transfer(profile_id, source_id, destination_id, transfer_date, close_original_account=True, market_values=None):
    """Replica-set transaction: all pairs and closure commit together or none do."""
    _, _, _, assets = prepare(profile_id, source_id, destination_id, transfer_date, close_original_account=close_original_account)
    asset_values(assets, market_values)
    client = Platform._get_db().client
    def write(session):
        source, destination, cash, assets = prepare(profile_id, source_id, destination_id, transfer_date, session, close_original_account)
        values = asset_values(assets, market_values)
        activities = {a.name: a for a in Activity.objects(name__in=['Transfer In', 'Transfer Out'])}
        if len(activities) != 2:
            fail('Run the setup script to create Transfer In and Transfer Out activities.')
        batch = str(uuid4())
        documents = []
        entries = [(p['stock'], p['shares'], money(p['cost'])) for p in assets]
        if cash:
            entries.append((None, None, cash))
        for stock, shares, total in entries:
            pair = str(uuid4())
            for platform, counterparty, activity in [(source, destination, 'Transfer Out'), (destination, source, 'Transfer In')]:
                row = Transaction(id=ObjectId(), account=platform.account, platform=platform, stock=stock, shares=shares,
                    total=total, activity=activities[activity], transaction_date=transfer_date,
                    total_currency=platform.currency, price_currency=platform.currency, exchange_rate=1,
                    transfer_batch=batch, transfer_pair=pair, transfer_counterparty=counterparty)
                if stock:
                    row.transfer_market_value = values[str(stock.id)]
                    row.transfer_market_value_source = 'manual'
                row.validate()
                documents.append(row.to_mongo())
        if not close_original_account:
            # Later income is allowed, but transferring historical holdings must
            # not leave a later sale without shares or an invalid cash balance.
            existing = Transaction._get_collection().find({'platform': source.id}, session=session)
            outgoing = [row for row in documents if row['platform'] == source.id]
            ledger(materialize_references(Transaction._from_son(row) for row in [*existing, *outgoing]))
        if documents:
            Transaction._get_collection().insert_many(documents, session=session)
        update = {'$inc': {'transfer_revision': 1}}
        if close_original_account:
            update['$set'] = {'closed_at': Platform._fields['closed_at'].to_mongo(transfer_date)}
        # A shared source write makes concurrent transfers conflict and retry
        # against the committed ledger instead of inserting duplicate balances.
        result = Platform._get_collection().update_one({'_id': source.id, 'closed_at': None}, update, session=session)
        if result.modified_count != 1:
            fail('The source platform has already been closed. Refresh before retrying.')
        return len(documents)
    try:
        with client.start_session() as session:
            return session.with_transaction(write)
    except GraphQLError:
        raise
    except Exception:
        import logging
        logging.getLogger(__name__).exception('Account transfer aborted')
        raise GraphQLError('The transfer could not be committed. No partial transfer is saved. MongoDB must support transactions (a replica set or Atlas).', extensions={'code': 'ACCOUNT_TRANSFER_FAILED'})
