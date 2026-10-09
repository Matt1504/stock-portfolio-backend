import logging
from graphene import (
    InputObjectType, 
    ID, 
    Mutation,
    String, 
    Field, 
    Decimal,
    Boolean,
    Date,
    List,
    NonNull,
    ObjectType
)
from models.models import (
    Transaction,
    Platform,
    Activity
)
from type.transaction import TransactionType 
from cache.queries import invalidate_transactions
from schemas.profiles import owned_platform, owned_record
from graphql import GraphQLError
from schemas.spinoff import validate_spinoffs
from schemas.gic import validate_gic
from schemas.transaction_currency import validate_currency, number
from mongoengine import NotUniqueError, ValidationError, DoesNotExist
from schemas.transaction_validation import TransactionWarning, validate_ownership, contribution_warnings

from schemas.account_transfer import commit_transfer, protect_transfer, validate_closed_platform, TransferAssetValueInput

def validate_sec_fee(transaction):
    if not transaction.activity or transaction.activity.name != "SEC Fee":
        return
    if transaction.stock:
        raise GraphQLError("SEC Fee is an account-level fee and cannot have a stock.", extensions={"code": "SEC_FEE_ACCOUNT_ONLY"})
    if not transaction.platform.currency or transaction.platform.currency.code != "USD":
        raise GraphQLError("SEC Fee can only be recorded in a USD trading account.", extensions={"code": "INVALID_SEC_FEE_CURRENCY"})
    if transaction.total is None or number(transaction.total, "Total") <= 0:
        raise GraphQLError("Enter a positive SEC Fee amount in Total.", extensions={"code": "INVALID_SEC_FEE_TOTAL"})


class TransactionInput(InputObjectType):
    id = ID()
    account = ID()
    stock = ID()
    platform = ID()
    price = Decimal()
    price_currency = ID()
    total_currency = ID()
    exchange_rate = Decimal()
    shares = Decimal()
    # Accepted for older clients, but deliberately never persisted.
    description = String()
    fee = Decimal()
    transaction_date = Date()
    activity = ID()
    rate = Decimal()
    maturity_date = Date()
    total = Decimal()
    spinoff_source = ID()
    allocated_book_cost = Decimal()
    gic_purchase = ID()
    interest_calculation = String()

class CreateTransactionMutation(Mutation):
    transaction = Field(TransactionType)
    warnings = List(NonNull(TransactionWarning), required=True)

    class Arguments:
        trans_data = TransactionInput(required=True)
        profile_id = ID(required=True)

    def mutate(self, info, profile_id, trans_data=None):
        platform = owned_platform(profile_id, trans_data.platform)
        if trans_data.account and str(platform.account.id) != str(trans_data.account):
            raise GraphQLError("Account type must match the platform.")
        activity = Activity.objects(id=trans_data.activity).first() if trans_data.activity else None
        if activity and activity.name in ("Contribution", "Withdrawal", "Service Fee", "SEC Fee", "ETF Rebate") and trans_data.stock:
            raise GraphQLError("{} transactions cannot have a stock.".format(activity.name))
        transaction = Transaction(
            stock = trans_data.stock,
            spinoff_source = trans_data.spinoff_source,
            allocated_book_cost = trans_data.allocated_book_cost,
            account = platform.account,
            platform = platform,
            price = trans_data.price,
            price_currency = trans_data.price_currency,
            total_currency = trans_data.total_currency,
            exchange_rate = trans_data.exchange_rate,
            shares = trans_data.shares,
            fee = trans_data.fee,
            transaction_date = trans_data.transaction_date,
            activity = trans_data.activity,
            rate = trans_data.rate,
            maturity_date = trans_data.maturity_date,
            total = trans_data.total,
            gic_purchase = trans_data.gic_purchase,
            interest_calculation = trans_data.interest_calculation or "simple"
        ) 
        validate_closed_platform(transaction)
        validate_sec_fee(transaction)
        validate_spinoffs(transaction)
        validate_ownership(transaction)
        warnings = validate_currency(transaction) + validate_gic(transaction, profile_id) + contribution_warnings(transaction, profile_id)
        try:
            transaction.save()
        except NotUniqueError:
            raise GraphQLError("This GIC purchase already has a maturity transaction.", extensions={"code": "GIC_ALREADY_MATURED"})
        invalidate_transactions()

        return CreateTransactionMutation(transaction=transaction, warnings=warnings)

class UpdateTransactionMutation(Mutation):
    trans = Field(TransactionType)
    warnings = List(NonNull(TransactionWarning), required=True)

    class Arguments:
        trans_data = TransactionInput(required=True)
        profile_id = ID(required=True)

    def mutate(self, info, profile_id, trans_data=None, invalidate=True):
        trans = owned_record(Transaction, profile_id, trans_data.id)
        protect_transfer(trans)
        original = Transaction.objects.get(pk=trans.id)
        platform = owned_platform(profile_id, trans_data.platform) if trans_data.platform else trans.platform
        if trans_data.account and str(platform.account.id) != str(trans_data.account):
            raise GraphQLError("Account type must match the platform.")
        currency_changed = platform.currency != trans.platform.currency
        trans.platform = platform
        trans.account = platform.account
        # Accept zero values: fees and totals may legitimately be reset to zero.
        for name in ("stock", "spinoff_source", "allocated_book_cost", "price", "shares", "fee", "transaction_date", "activity", "rate", "maturity_date", "total", "gic_purchase", "interest_calculation", "price_currency", "total_currency", "exchange_rate"):
            if name in trans_data:
                value = trans_data[name]
                if name in ("stock", "spinoff_source", "activity", "gic_purchase", "price_currency", "total_currency") and value is not None:
                    value = trans._fields[name].to_python(value)
                setattr(trans, name, value)
        if currency_changed:
            if "total_currency" not in trans_data:
                trans.total_currency = platform.currency
            if trans_data.get("total") is None and trans.activity.name not in ("Stock Split", "Stock Spinoff"):
                raise GraphQLError("Enter the total in the destination platform currency.", extensions={"code": "TOTAL_REQUIRED"})
            if original.fee and "fee" not in trans_data:
                raise GraphQLError("Confirm the fee in the destination platform currency.", extensions={"code": "FEE_REQUIRED"})
            if trans.activity.name == "Stock Spinoff" and "allocated_book_cost" not in trans_data:
                raise GraphQLError("Enter the allocated book cost in the destination platform currency.", extensions={"code": "INVALID_SPINOFF_COST"})
            trade = trans.activity.name in ("Buy", "Sell") and bool(trans.shares)
            if "price_currency" not in trans_data:
                trans.price_currency = (original.price_currency or original.platform.currency) if trade else platform.currency
            if "exchange_rate" not in trans_data:
                trans.exchange_rate = 1 if trans.price_currency == platform.currency else None
        trans.description = None
        activity = Activity.objects(id=trans_data.activity).first() if trans_data.activity else trans.activity
        if activity and activity.name in ("Contribution", "Withdrawal", "Service Fee", "SEC Fee", "ETF Rebate") and trans.stock:
            raise GraphQLError("{} transactions cannot have a stock.".format(activity.name))
        validate_closed_platform(trans)
        validate_sec_fee(trans)
        validate_spinoffs(trans, original)
        validate_ownership(trans, original)
        warnings = validate_currency(trans, original) + validate_gic(trans, profile_id, original) + contribution_warnings(trans, profile_id)
        try:
            trans.save()
        except NotUniqueError:
            raise GraphQLError("This GIC purchase already has a maturity transaction.", extensions={"code": "GIC_ALREADY_MATURED"})
        if invalidate:
            invalidate_transactions()
        return UpdateTransactionMutation(trans=trans, warnings=warnings)

class BulkTransactionResult(ObjectType):
    id = ID(required=True)
    success = Boolean(required=True)
    error = String()
    code = String()
    warnings = List(NonNull(TransactionWarning), required=True)


class BulkUpdateTransactionsMutation(Mutation):
    results = List(NonNull(BulkTransactionResult), required=True)

    class Arguments:
        transactions = List(NonNull(TransactionInput), required=True)
        profile_id = ID(required=True)

    def mutate(self, info, profile_id, transactions):
        # Each row uses the same historical ownership/GIC/currency validations
        # as a single edit. Results explicitly report partial success.
        if not transactions or len(transactions) > 200:
            raise GraphQLError("Submit between 1 and 200 changed transactions.")
        ids = [str(row.get("id") or "") for row in transactions]
        if not all(ids) or len(set(ids)) != len(ids):
            raise GraphQLError("Each changed transaction must have a unique ID.")
        results = []
        saved = False
        try:
            for row in transactions:
                try:
                    result = UpdateTransactionMutation.mutate(None, info, profile_id, row, invalidate=False)
                    saved = True
                    results.append(BulkTransactionResult(id=row.id, success=True, warnings=result.warnings))
                except GraphQLError as error:
                    results.append(BulkTransactionResult(id=row.id, success=False, error=error.message,
                        code=(error.extensions or {}).get("code"), warnings=[]))
                except (ValueError, TypeError, ValidationError, DoesNotExist):
                    results.append(BulkTransactionResult(id=row.id, success=False,
                        error="Invalid transaction values or reference.", code="INVALID_TRANSACTION", warnings=[]))
                except Exception:
                    logging.getLogger(__name__).exception("Bulk transaction update failed for row %s", row.id)
                    results.append(BulkTransactionResult(id=row.id, success=False,
                        error="Could not save this transaction. Refresh before retrying.", code="UPDATE_FAILED", warnings=[]))
        finally:
            if saved:
                invalidate_transactions()
        return BulkUpdateTransactionsMutation(results=results)


class TransferTransactionMutation(Mutation):
    class Arguments:
        trans_from = ID(required=True)
        trans_to = ID(required=True)
        profile_id = ID(required=True)
        transfer_date = Date(required=True)
        close_original_account = Boolean(default_value=True)
        market_values = List(NonNull(TransferAssetValueInput))

    success = Boolean(required=True)
    def mutate(self, info, trans_from, trans_to, profile_id, transfer_date, close_original_account=True, market_values=None):
        commit_transfer(profile_id, trans_from, trans_to, transfer_date, close_original_account, market_values)
        invalidate_transactions()
        return TransferTransactionMutation(success=True)

class DeleteTransactionMutation(Mutation):
    class Arguments:
        id = ID(required=True)
        profile_id = ID(required=True)
        
    success = Boolean()

    def mutate(self, info, id, profile_id):
        transaction = owned_record(Transaction, profile_id, id)
        protect_transfer(transaction)
        if Transaction.objects(gic_purchase=transaction.id).first():
            raise GraphQLError("Delete the linked GIC maturity before deleting its purchase.")
        validate_spinoffs(None, transaction)
        if transaction.activity and transaction.activity.name == "Stock Spinoff":
            placeholder = Transaction(id=transaction.id, platform=transaction.platform, stock=transaction.stock, transaction_date=transaction.transaction_date)
            validate_ownership(placeholder, transaction)
        try:
            transaction.delete()
            invalidate_transactions()
            success = True
        except Exception:
            success = False
    
        return DeleteTransactionMutation(success=success)
