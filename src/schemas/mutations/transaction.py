from graphene import (
    InputObjectType, 
    ID, 
    Mutation,
    String, 
    Field, 
    Decimal,
    Boolean,
    Date
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

class TransactionInput(InputObjectType):
    id = ID()
    account = ID()
    stock = ID()
    platform = ID()
    price = Decimal()
    shares = Decimal()
    description = String()
    fee = Decimal()
    transaction_date = Date()
    activity = ID()
    rate = Decimal()
    maturity_date = Date()
    total = Decimal()

class CreateTransactionMutation(Mutation):
    transaction = Field(TransactionType)

    class Arguments:
        trans_data = TransactionInput(required=True)
        profile_id = ID(required=True)

    def mutate(self, info, profile_id, trans_data=None):
        platform = owned_platform(profile_id, trans_data.platform)
        if trans_data.account and str(platform.account.id) != str(trans_data.account):
            raise GraphQLError("Account type must match the platform.")
        activity = Activity.objects(id=trans_data.activity).first() if trans_data.activity else None
        if activity and activity.name in ("Contribution", "Withdrawal") and trans_data.stock:
            raise GraphQLError("{} transactions cannot have a stock.".format(activity.name))
        transaction = Transaction(
            stock = trans_data.stock,
            account = platform.account,
            platform = platform,
            price = trans_data.price,
            shares = trans_data.shares,
            description = trans_data.description,
            fee = trans_data.fee,
            transaction_date = trans_data.transaction_date,
            activity = trans_data.activity,
            rate = trans_data.rate,
            maturity_date = trans_data.maturity_date,
            total = trans_data.total
        ) 
        transaction.save()
        invalidate_transactions()

        return CreateTransactionMutation(transaction=transaction)

class UpdateTransactionMutation(Mutation):
    trans = Field(TransactionType)

    class Arguments:
        trans_data = TransactionInput(required=True)
        profile_id = ID(required=True)

    def mutate(self, info, profile_id, trans_data=None):
        trans = owned_record(Transaction, profile_id, trans_data.id)
        platform = owned_platform(profile_id, trans_data.platform) if trans_data.platform else trans.platform
        if trans_data.account and str(platform.account.id) != str(trans_data.account):
            raise GraphQLError("Account type must match the platform.")
        if platform.currency != trans.platform.currency:
            raise GraphQLError("Platform changes must use the same currency.")
        trans.platform = platform
        trans.account = platform.account
        # Accept zero values: fees and totals may legitimately be reset to zero.
        for name in ("stock", "price", "description", "shares", "fee", "transaction_date", "activity", "rate", "maturity_date", "total"):
            if name in trans_data:
                value = trans_data[name]
                if name in ("stock", "activity") and value is not None:
                    value = trans._fields[name].to_python(value)
                setattr(trans, name, value)
        activity = Activity.objects(id=trans_data.activity).first() if trans_data.activity else trans.activity
        if activity and activity.name in ("Contribution", "Withdrawal") and trans.stock:
            raise GraphQLError("{} transactions cannot have a stock.".format(activity.name))
        trans.save()
        invalidate_transactions()
        return UpdateTransactionMutation(trans=trans)

class TransferTransactionMutation(Mutation):
    transFrom = Field(TransactionType)
    transTo = Field(TransactionType)

    class Arguments:
        trans_from = ID(required=True)
        trans_to = ID(required=True)
        profile_id = ID(required=True)

    success = Boolean()
    def mutate(self, info, trans_from, trans_to, profile_id):
        source = owned_platform(profile_id, trans_from)
        destination = owned_platform(profile_id, trans_to)
        if source.account != destination.account or source.currency != destination.currency:
            raise GraphQLError("Transfers must use the same account type and currency.")
        if source.id == destination.id:
            raise GraphQLError("Choose a different destination platform.")
        try:
            trans = Transaction.objects.filter(platform=source)
            platform = destination
            for tran in trans:
                tran.platform = platform
                tran.account = platform.account
                tran.save()
            success = True
        except Exception:
            success = False
        finally:
            # A transfer can partially save before failing. Retire cached
            # transaction lists even when only some documents were moved.
            invalidate_transactions()
        return TransferTransactionMutation(success=success)

class DeleteTransactionMutation(Mutation):
    class Arguments:
        id = ID(required=True)
        profile_id = ID(required=True)
        
    success = Boolean()

    def mutate(self, info, id, profile_id):
        transaction = owned_record(Transaction, profile_id, id)
        try:
            transaction.delete()
            invalidate_transactions()
            success = True
        except Exception:
            success = False
    
        return DeleteTransactionMutation(success=success)
