import graphene 
from graphene.relay import Node
from graphene_mongo import MongoengineConnectionField
from graphene import ObjectType

from type.type import (
    AccountType,
    ActivityType,
    CurrencyType,
    PlatformType,
    StockType,
    TransactionType
)

from schemas.mutations.stock import (
    CreateStockMutation,
    UpdateStockMutation,
    DeleteStockMutation
)

from schemas.mutations.transaction import (
    CreateTransactionMutation,
    UpdateTransactionMutation,
    DeleteTransactionMutation
)

from schemas.mutations.platform import (
    CreatePlatformMutation,
    DeletePlatformMutation
)

from models.models import Transaction

class Mutations(ObjectType):
    create_platform = CreatePlatformMutation.Field()
    delete_platform = DeletePlatformMutation.Field()
    create_stock = CreateStockMutation.Field()
    update_stock = UpdateStockMutation.Field()
    delete_stock = DeleteStockMutation.Field()
    create_transaction = CreateTransactionMutation.Field()
    update_transaction = UpdateTransactionMutation.Field()
    delete_transaction = DeleteTransactionMutation.Field()

class Query(ObjectType):
    node = Node.Field

    accounts = MongoengineConnectionField(AccountType)
    activities = MongoengineConnectionField(ActivityType)
    currencies = MongoengineConnectionField(CurrencyType)
    platforms = MongoengineConnectionField(PlatformType)
    stocks = MongoengineConnectionField(StockType)
    transactions = MongoengineConnectionField(TransactionType)

    # TODO: Move these to its own file similar to mutation
    transactions_by_stock = graphene.List(TransactionType, stock=graphene.ID())
    def resolve_transactions_by_stock(self, info, stock):
        return Transaction.objects.filter(stock=stock)
    
    transactions_by_account = graphene.List(TransactionType, account=graphene.ID())
    def resolve_transactions_by_account(self, info, account):
        return Transaction.objects.filter(account=account)
    
    transactions_by_platform = graphene.List(TransactionType, platform=graphene.ID())
    def resolve_transactions_by_platform(self, info, platform):
        return Transaction.objects.filter(platform=platform)
    
    transactions_by_activity = graphene.List(TransactionType, activity=graphene.ID())
    def resolve_transactions_by_activity(self, info, activity):
        return Transaction.objects.filter(activity=activity)

schema = graphene.Schema(query = Query, mutation=Mutations, types=[AccountType, ActivityType, CurrencyType, PlatformType, StockType, TransactionType])
