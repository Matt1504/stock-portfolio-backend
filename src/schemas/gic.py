"""GIC contracts are Buy transactions; maturity returns principal plus interest."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from mongoengine import DoesNotExist, ValidationError
from models.models import Transaction
from schemas.profiles import owned_record
from schemas.transaction_validation import fail


def is_gic(transaction):
    return bool(transaction.stock and transaction.stock.asset and transaction.stock.asset.name == "GIC")


def money(value):
    try:
        amount = Decimal(str(value or 0))
        if not amount.is_finite():
            raise InvalidOperation()
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        fail("GIC amounts must be finite numbers.", "INVALID_GIC_AMOUNT")


def expected_payout(purchase):
    if not purchase.transaction_date or not purchase.maturity_date or purchase.rate is None:
        return None
    years = Decimal((purchase.maturity_date - purchase.transaction_date).days) / Decimal(365)
    rate = Decimal(str(purchase.rate)) / 100
    principal = money(purchase.total)
    if years <= 0 or rate < 0 or rate > 1 or principal <= 0:
        return None
    if purchase.interest_calculation == "annual_compound":
        return money(principal * ((1 + rate) ** years))
    return money(principal * (1 + rate * years))


def validate_purchase(purchase):
    if not is_gic(purchase) or not purchase.activity or purchase.activity.name != "Buy":
        fail("Select an original Buy transaction for a GIC asset.", "INVALID_GIC_PURCHASE")
    if money(purchase.total) <= 0:
        fail("GIC principal must be positive.", "INVALID_GIC_PRINCIPAL")
    if not purchase.transaction_date or not purchase.maturity_date or purchase.maturity_date <= purchase.transaction_date:
        fail("GIC maturity date must be after its purchase date.", "INVALID_GIC_DATES")
    if purchase.rate is None or not Decimal(str(purchase.rate)).is_finite() or not 0 <= purchase.rate <= 100:
        fail("GIC annual interest rate must be between 0 and 100 percent.", "INVALID_GIC_RATE")
    if purchase.interest_calculation not in ("simple", "annual_compound"):
        fail("Select a supported GIC interest calculation method.", "INVALID_GIC_RATE")
    if purchase.shares or purchase.price:
        fail("GIC transactions cannot contain shares or a share price.", "INVALID_GIC_FIELDS")


def validate_maturity(transaction, purchase):
    validate_purchase(purchase)
    if transaction.platform.id != purchase.platform.id or not transaction.stock or transaction.stock.id != purchase.stock.id:
        fail("The GIC maturity must use the original purchase's platform and GIC asset.", "GIC_PURCHASE_MISMATCH")
    if not transaction.transaction_date or transaction.transaction_date < purchase.transaction_date:
        fail("GIC payout date cannot precede its purchase date.", "INVALID_GIC_DATES")
    if transaction.shares or transaction.price:
        fail("GIC transactions cannot contain shares or a share price.", "INVALID_GIC_FIELDS")
    principal = money(purchase.total)
    payout = money(transaction.total)
    if payout < principal:
        fail("Gross GIC payout cannot be less than the original principal.", "INVALID_GIC_PAYOUT")
    transaction.principal_returned = principal
    transaction.interest_earned = payout - principal
    warnings = []
    if transaction.transaction_date < purchase.maturity_date:
        warnings.append({"code": "EARLY_GIC_MATURITY", "message": "GIC maturity saved before its scheduled maturity date."})
    expected = expected_payout(purchase)
    if payout != expected:
        warnings.append({"code": "GIC_PAYOUT_DIFFERENCE", "message": "Gross payout {:.2f} differs from the estimated maturity payout {:.2f}. The estimate uses the recorded interest method and actual days/365; verify against your statement.".format(payout, expected)})
    return warnings


def validate_gic(transaction, profile_id, original=None):
    activity = transaction.activity.name if transaction.activity else ""
    warnings = []
    if original:
        linked = Transaction.objects(gic_purchase=original.id).first()
        if linked:
            if money(transaction.total) != money(original.total):
                fail("Cannot change principal on a matured GIC purchase. Remove its maturity transaction first.", "GIC_ALREADY_MATURED")
            warnings.extend(validate_maturity(linked, transaction))
    if activity == "GIC Maturity":
        if not transaction.gic_purchase:
            fail("Select the original GIC purchase.", "GIC_PURCHASE_REQUIRED")
        try:
            purchase_id = transaction.gic_purchase.id
        except (DoesNotExist, ValidationError):
            fail("Original GIC purchase was not found.", "INVALID_GIC_PURCHASE")
        purchase = owned_record(Transaction, profile_id, purchase_id)
        if Transaction.objects(gic_purchase=purchase.id, id__ne=transaction.id).first():
            fail("This GIC purchase already has a maturity transaction.", "GIC_ALREADY_MATURED")
        return warnings + validate_maturity(transaction, purchase)
    if transaction.gic_purchase:
        fail("Only GIC Maturity can reference an original GIC purchase.", "INVALID_GIC_FIELDS")
    transaction.principal_returned = None
    transaction.interest_earned = None
    if is_gic(transaction):
        if transaction.shares or transaction.price or activity in ("Dividends", "Stock Split", "Interest", "Sell"):
            fail("GICs use Buy and GIC Maturity, without shares, share prices, dividends, or separate GIC interest transactions.", "INVALID_GIC_ACTIVITY")
        if activity == "Buy":
            validate_purchase(transaction)
    return warnings


def outstanding_purchases(profile_id, platform, stock=None):
    from schemas.profiles import owned_platform
    selected = owned_platform(profile_id, platform)
    settled = Transaction.objects(platform=selected, gic_purchase__ne=None).scalar("gic_purchase")
    settled_ids = [record.id for record in settled if record]
    records = Transaction.objects(platform=selected, id__nin=settled_ids).order_by("transaction_date", "id")
    if stock:
        records = records.filter(stock=stock)
    return [record for record in records if is_gic(record) and record.activity and record.activity.name == "Buy"]
