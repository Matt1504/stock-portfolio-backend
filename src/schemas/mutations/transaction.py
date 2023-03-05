from graphene import (
    InputObjectType, 
    ID, 
    Mutation, 
    Field, 
    Int,
    Decimal,
    Boolean,
    Date
)
from models.models import (
    Stock, 
    Activity, 
    Platform,
    Transaction
)
from type.transaction import TransactionType 

class TransactionInput(InputObjectType):
    id = ID()
    stock = Stock()
    platform = Platform()
    price = Decimal()
    shares = Int()
    fee = Decimal()
    transaction_date = Date()
    activity = Activity()
    total = Decimal()


class CreateTransactionMutation(Mutation):
    trans = Field(TransactionType)

    class Arguments:
        trans_data = TransactionInput(required=True)

    def mutate(self, info, trans_data=None):
        trans = Transaction(
            stock = trans_data.stock,
            platform = trans_data.platform,
            price = trans_data.price,
            shares = trans_data.shares,
            fee = trans_data.FileExistsError,
            transaction_data = trans_data.transaction_data,
            activity = trans_data.activity,
            total = trans_data.total
        ) 
        trans.save()

        return CreateTransactionMutation(transaction=trans)

class UpdateTransactionMutation(Mutation):
    stock = Field(TransactionType)

    class Arguments:
        trans_data = TransactionInput(required=True)

    def mutate(self, info, trans_data=None):
        trans = Transaction.objects.get(pk=trans_data.id)
        if (trans_data.stock):
            trans.stock = trans_data.stock
        if (trans_data.platform):
            trans.platform = trans_data.platform
        if (trans_data.price):
            trans.price = trans_data.price
        if (trans_data.shares):
            trans.shares = trans_data.shares
        if (trans_data.fee):
            trans.fee = trans_data.fee
        if (trans_data.transaction_date):
            trans.transaction_date = trans_data.transaction_date
        if (trans_data.activity):
            trans.activity = trans_data.activity
        if (trans_data.total):
            trans.total = trans_data.total

        trans.save()
    
        return UpdateTransactionMutation(transaction=trans)

class DeleteTransactionMutation(Mutation):
    class Arguments:
        id = ID(required=True)
        
    success = Boolean()

    def mutate(self, info, id):
        try:
            Transaction.objects.get(pk=id).delete()
            success = True
        except Exception:
            success = False
    
        return DeleteTransactionMutation(success=success)