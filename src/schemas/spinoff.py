"""A spinoff is one atomic document: received shares plus cost moved from source."""
from decimal import Decimal, InvalidOperation
from bson import ObjectId
from mongoengine import DoesNotExist, ValidationError
from models.models import Transaction
from schemas.transaction_validation import fail, order_key, quantity


def amount(value):
    try:
        result = Decimal(str(value or 0))
        if not result.is_finite():
            raise InvalidOperation()
        return result.quantize(Decimal('0.01'), rounding=Transaction._fields['total'].rounding)
    except (InvalidOperation, ValueError):
        fail('Allocated book cost must be a finite amount.', 'INVALID_SPINOFF_COST')


def validate_fields(transaction):
    if transaction.activity.name != 'Stock Spinoff':
        if transaction.spinoff_source or transaction.allocated_book_cost is not None:
            fail('Only Stock Spinoff can contain a source stock or allocated book cost.', 'INVALID_SPINOFF_FIELDS')
        return
    try:
        source, target = transaction.spinoff_source, transaction.stock
        if not source or not target or source.id == target.id:
            fail('Select different original and received stocks.', 'INVALID_SPINOFF_STOCKS')
        if any(not stock.asset or stock.asset.name != 'Stock' for stock in (source, target)):
            fail('Stock Spinoff currently supports Stock assets only.', 'INVALID_SPINOFF_STOCKS')
        if source.currency != target.currency:
            fail('Original and received stocks must have the same currency.', 'INVALID_SPINOFF_CURRENCY')
    except (DoesNotExist, ValidationError):
        fail('Select existing original and received stocks.', 'INVALID_SPINOFF_STOCKS')
    if not transaction.transaction_date:
        fail('Enter the spinoff date.', 'INVALID_SPINOFF_DATE')
    if quantity(transaction.shares) <= 0:
        fail('Enter a positive number of shares received.', 'INVALID_SHARES')
    if transaction.allocated_book_cost is None or amount(transaction.allocated_book_cost) < 0:
        fail('Enter a nonnegative allocated book cost.', 'INVALID_SPINOFF_COST')
    if any(getattr(transaction, field) for field in ('price', 'fee', 'total', 'gic_purchase', 'rate', 'maturity_date')):
        fail('Spinoffs have no cash total, share price, trading fee, or GIC fields.', 'INVALID_SPINOFF_FIELDS')
    transaction.allocated_book_cost = amount(transaction.allocated_book_cost)
    transaction.total = Decimal(0)
    transaction.exchange_rate = Decimal(1)
    transaction.price_currency = transaction.platform.currency
    transaction.total_currency = transaction.platform.currency


def validate_spinoffs(candidate, original=None):
    """Replay affected platforms to protect later spinoffs and sales."""
    if candidate:
        candidate.id = candidate.id or ObjectId()
        validate_fields(candidate)
    platform_ids = {record.platform.id for record in (candidate, original) if record}
    excluded_id = candidate.id if candidate else original.id
    for platform_id in platform_ids:
        records = list(Transaction.objects(platform=platform_id, id__ne=excluded_id).select_related(max_depth=2))
        if candidate and candidate.platform.id == platform_id:
            records.append(candidate)
        if not any(record.activity and record.activity.name == 'Stock Spinoff' for record in records):
            continue
        positions = {}
        received = set()
        for record in sorted(records, key=order_key):
            activity = record.activity.name if record.activity else ''
            if not record.stock:
                continue
            shares, cost = positions.get(record.stock.id, (Decimal(0), Decimal(0)))
            qty = quantity(record.shares)
            if activity == 'Stock Spinoff':
                validate_fields(record)
                source_shares, source_cost = positions.get(record.spinoff_source.id, (Decimal(0), Decimal(0)))
                if source_shares <= 0:
                    fail('Original stock must be owned in this platform on the spinoff date.', 'SPINOFF_SOURCE_NOT_OWNED')
                allocation = amount(record.allocated_book_cost)
                if allocation > source_cost:
                    fail('Allocated book cost exceeds the original stock’s remaining book cost on the spinoff date.', 'INSUFFICIENT_SPINOFF_COST')
                positions[record.spinoff_source.id] = (source_shares, source_cost - allocation)
                shares, cost = shares + qty, cost + allocation
                received.add(record.stock.id)
            elif activity in ('Buy', 'Transfer In'):
                shares, cost = shares + qty, cost + amount(record.total)
            elif activity in ('Sell', 'Transfer Out'):
                if record.stock.id in received and (qty > shares or shares <= 0):
                    fail('This change would leave a later sale or transfer without its spinoff shares.', 'INSUFFICIENT_SHARES')
                if shares > 0:
                    cost -= cost * min(qty, shares) / shares
                shares -= qty
            elif activity == 'Stock Split':
                shares += qty
            positions[record.stock.id] = (shares, cost if shares > 0 else Decimal(0))
