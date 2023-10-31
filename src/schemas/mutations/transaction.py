from graphene import (
    InputObjectType, 
    ID, 
    Mutation,
    String, 
    Field, 
    Int,
    Decimal,
    Boolean,
    Date
)
from models.models import (
    Transaction,
    Platform
)
from type.transaction import TransactionType 

class TransactionInput(InputObjectType):
    id = ID()
    account = ID()
    stock = ID()
    platform = ID()
    price = Decimal()
    shares = Int()
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

    def mutate(self, info, trans_data=None):
        transaction = Transaction(
            stock = trans_data.stock,
            account = trans_data.account,
            platform = trans_data.platform,
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

        return CreateTransactionMutation(transaction=transaction)

class UpdateTransactionMutation(Mutation):
    trans = Field(TransactionType)

    class Arguments:
        trans_data = TransactionInput(required=True)

    def mutate(self, info, trans_data=None):
        trans = Transaction.objects.get(pk=trans_data.id)
        if (trans_data.stock):
            trans.stock = trans_data.stock
        if (trans_data.account):
            trans.account = trans_data.account
        if (trans_data.platform):
            trans.platform = trans_data.platform
        if (trans_data.price):
            trans.price = trans_data.price
        if (trans_data.description):
            trans.description = trans_data.description
        if (trans_data.shares):
            trans.shares = trans_data.shares
        if (trans_data.fee):
            trans.fee = trans_data.fee
        if (trans_data.transaction_date):
            trans.transaction_date = trans_data.transaction_date
        if (trans_data.activity):
            trans.activity = trans_data.activity
        if (trans_data.rate):
            trans.rate = trans_data.rate
        if (trans_data.maturity_date):
            trans.maturity_date = trans_data.maturity_date
        if (trans_data.total):
            trans.total = trans_data.total

        trans.save()
    
        return UpdateTransactionMutation(trans=trans)

class TransferTransactionMutation(Mutation):
    transFrom = Field(TransactionType)
    transTo = Field(TransactionType)

    class Arguments:
        trans_from = ID(required=True)
        trans_to = ID(required=True)

    success = Boolean()
    def mutate(self, info, trans_from, trans_to):
        try:
            trans = Transaction.objects.filter(platform=trans_from)
            platform = Platform.objects.get(pk=trans_to)
            for tran in trans:
                tran.platform = platform
                tran.save()
            success = True
        except Exception:
            success = False
        return TransferTransactionMutation(success=success)

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