"""Validate prospective transaction histories directly against MongoDB."""
from datetime import date
from decimal import Decimal, InvalidOperation
from bson import ObjectId
from mongoengine import Q
from graphql import GraphQLError
from graphene import ObjectType, String
from models.models import Transaction, Platform, ContributionLimit, Activity


class TransactionWarning(ObjectType):
    code = String(required=True)
    message = String(required=True)


def fail(message, code):
    raise GraphQLError(message, extensions={"code": code})


def quantity(value):
    try:
        result = Decimal(str(value or 0))
        if not result.is_finite():
            raise InvalidOperation()
        return result.quantize(Decimal("0.00000001"), rounding=Transaction._fields["shares"].rounding)
    except (InvalidOperation, ValueError):
        fail("Shares must be a finite decimal quantity.", "INVALID_SHARES")


def order_key(transaction):
    # Dates have day precision; existing records on the same day precede new ones.
    return (transaction.transaction_date or date.max, str(transaction.id))


def validate_ownership(candidate, original=None):
    candidate.id = candidate.id or ObjectId()
    ledger_activities = ("Buy", "Sell", "Stock Split", "Transfer In", "Transfer Out", "Stock Spinoff")
    affects_balance = ((candidate.activity and candidate.activity.name in ledger_activities) or
                       (original and original.activity and original.activity.name in ledger_activities))
    activity = candidate.activity.name if candidate.activity else ""
    if not candidate.stock and activity in ("Sell", "Dividends", "Stock Split"):
        fail("{} requires a stock.".format(activity), "STOCK_REQUIRED")
    if not affects_balance:
        return  # Income entry/edit must not be gated by legacy share ledgers.
    scopes = {}
    if original and original.stock:
        scopes[(original.platform.id, original.stock.id)] = order_key(original)
    if candidate.stock:
        scope = (candidate.platform.id, candidate.stock.id)
        scopes[scope] = min(scopes.get(scope, order_key(candidate)), order_key(candidate))
    for record in (original, candidate):
        if record and record.spinoff_source:
            scope = (record.platform.id, record.spinoff_source.id)
            scopes[scope] = min(scopes.get(scope, order_key(record)), order_key(record))
    for (platform_id, stock_id), changed_from in scopes.items():
        stock = next(stock for record in (candidate, original) if record for stock in (record.stock, record.spinoff_source) if stock and stock.id == stock_id)
        # TODO: Define ownership for amount-only funds before enforcing
        # these share-based checks. GICs use their separate contract validator.
        if stock.asset is not None and stock.asset.name == "GIC":
            continue
        platform = candidate.platform if candidate.platform.id == platform_id else original.platform
        platform_ids = [platform.id]
        records = list(Transaction.objects(Q(stock=stock_id) | Q(spinoff_source=stock_id), platform__in=platform_ids, id__ne=candidate.id).select_related(max_depth=2))
        if candidate.stock and candidate.platform.id in platform_ids and (candidate.stock.id == stock_id or (candidate.spinoff_source and candidate.spinoff_source.id == stock_id)):
            records.append(candidate)
        if stock.asset is not None and stock.asset.name in ("Index Fund", "Mutual Fund"):
            trades = [record for record in records if record.platform.id == platform_id and record.activity and record.activity.name in ("Buy", "Sell")]
            if not any(quantity(record.shares) for record in trades):
                continue  # TODO: define ownership for amount-only fund transactions.
            if any(quantity(record.shares) <= 0 for record in trades):
                fail("This fund mixes amount-only and share-based trades. Record shares for its purchases and sales before using share-based tracking.", "MIXED_FUND_TRACKING")
        balances = {}
        for transaction in sorted(records, key=order_key):
            activity = transaction.activity.name if transaction.activity else ""
            shares = quantity(transaction.shares) if transaction.stock and transaction.stock.id == stock_id else Decimal(0)
            balance = balances.get(transaction.platform.id, Decimal(0))
            # Replay the platform ledger so historical edits cannot oversell
            # a later position. Income eligibility is left to the user.
            if order_key(transaction) >= changed_from and (transaction.id == candidate.id or affects_balance):
                operation = "save {}".format(candidate.activity.name) if candidate.activity else "delete {}".format(original.activity.name)
                context = "{}".format(operation) if transaction.id == candidate.id else "{} because it would invalidate a later {} transaction for {} dated {}".format(operation, activity, stock.ticker or stock.name, transaction.transaction_date or "unknown")
                if activity == "Sell":
                    if shares <= 0:
                        fail("Sell transactions require a positive number of shares.", "INVALID_SHARES")
                    if balance <= 0:
                        fail("Cannot {}: this stock is not owned in the selected platform on that transaction date.".format(context), "STOCK_NOT_OWNED")
                    if shares > balance:
                        fail("Cannot {}: selling {} shares exceeds the {} shares owned in the selected platform.".format(context, format(shares.normalize(), 'f'), format(balance.normalize(), 'f')), "INSUFFICIENT_SHARES")
                if activity == "Buy" and shares <= 0:
                    fail("Buy transactions require a positive number of shares.", "INVALID_SHARES")
            if activity in ("Buy", "Transfer In", "Stock Split", "Stock Spinoff"):
                balance += shares
            elif activity in ("Sell", "Transfer Out"):
                balance -= shares
            balances[transaction.platform.id] = balance


def contribution_warnings(candidate, profile_id):
    if not candidate.activity or candidate.activity.name != "Contribution":
        return []
    if candidate.account and not candidate.account.has_contribution_limit:
        return []
    limits = list(ContributionLimit.objects(profile=profile_id, account=candidate.account))
    if not limits:
        return []
    limit = sum((Decimal(str(record.amount or 0)) for record in limits), Decimal(0))
    platform_ids = list(Platform.objects(profile=profile_id, account=candidate.account).scalar("id"))
    activity_ids = list(Activity.objects(name="Contribution").scalar("id"))
    records = Transaction.objects(platform__in=platform_ids, account=candidate.account,
                                  activity__in=activity_ids, id__ne=candidate.id).only("total")
    total = sum((Decimal(str(record.total or 0)) for record in records), Decimal(0))
    total += Decimal(str(candidate.total or 0)).quantize(Decimal("0.01"), rounding=Transaction._fields["total"].rounding)
    if total <= limit:
        return []
    return [{"code": "CONTRIBUTION_LIMIT_EXCEEDED", "message":
             "Transaction saved. Recorded lifetime contributions for {} now total {:.2f}, exceeding the recorded limit of {:.2f} by {:.2f}. This uses the contribution overview's recorded amounts without currency conversion.".format(candidate.account.code or candidate.account.name or "this account type", total, limit, total - limit)}]
