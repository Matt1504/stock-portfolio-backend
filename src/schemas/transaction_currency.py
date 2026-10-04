"""Record quoted prices separately from account-currency cash amounts."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from mongoengine import DoesNotExist, ValidationError
from schemas.transaction_validation import fail

# Broker statements round quoted prices; preserve small settlement differences.
TOTAL_DIFFERENCE_TOLERANCE = Decimal("0.10")


def number(value, label):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation()
        return result
    except (InvalidOperation, ValueError, TypeError):
        fail("{} must be a finite number.".format(label), "INVALID_EXCHANGE_RATE")


def validate_currency(transaction, original=None):
    try:
        account_currency = transaction.platform.currency
        if not account_currency:
            fail("The platform must have a currency.", "CURRENCY_REQUIRED")
        transaction.total_currency = transaction.total_currency or account_currency
        if transaction.total_currency.id != account_currency.id:
            fail("Total and fees must use the platform currency ({}).".format(account_currency.code), "TOTAL_CURRENCY_MISMATCH")
        trade = transaction.activity and transaction.activity.name in ("Buy", "Sell") and bool(transaction.shares)
        fallback = account_currency if original else (transaction.stock.currency if trade and transaction.stock and transaction.stock.currency else account_currency)
        transaction.price_currency = transaction.price_currency or fallback
        different = transaction.price_currency.id != transaction.total_currency.id
    except (DoesNotExist, ValidationError):
        fail("Select existing price and total currencies.", "INVALID_CURRENCY")
    if transaction.exchange_rate is None:
        if different:
            fail("Enter an exchange rate from price currency to total currency.", "EXCHANGE_RATE_REQUIRED")
        transaction.exchange_rate = Decimal(1)
    rate = number(transaction.exchange_rate, "Exchange rate")
    if rate <= 0:
        fail("Exchange rate must be positive.", "INVALID_EXCHANGE_RATE")
    rate = rate.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
    if rate <= 0:
        fail("Exchange rate is too small to record.", "INVALID_EXCHANGE_RATE")
    transaction.exchange_rate = rate
    if not different and rate != 1:
        fail("Exchange rate must be 1 when currencies are the same.", "INVALID_EXCHANGE_RATE")
    if not trade or transaction.price is None:
        return []
    fee = number(transaction.fee or 0, "Fee")
    if fee < 0:
        fail("Fee cannot be negative.", "INVALID_FEE")
    price = number(transaction.price, "Price")
    shares = number(transaction.shares, "Shares").quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
    expected = (price * shares * rate + (fee if transaction.activity.name == "Buy" else -fee)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if transaction.total is None:
        transaction.total = expected
        return []
    total = number(transaction.total, "Total").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if abs(total - expected) <= TOTAL_DIFFERENCE_TOLERANCE:
        return []
    return [{"code": "TRANSACTION_TOTAL_DIFFERENCE", "message": "Recorded total {:.2f} {} differs from calculated total {:.2f} {} by more than the 0.10 rounding tolerance. The recorded total was saved; verify against your statement.".format(total, transaction.total_currency.code, expected, transaction.total_currency.code)}]
