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

from models.models import Transaction

class Mutations(ObjectType):
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
    transactions_by_stock = graphene.List(TransactionType, stockId=graphene.ID())
    def resolve_transactions_by_stock(self, info, stockId):
        return Transaction.objects.filter(stock=stockId)
    
    transactions_by_account = graphene.List(TransactionType, accountId=graphene.ID())
    def resolve_transactions_by_account(self, info, accountId):
        return Transaction.objects.filter(account=accountId)
    
    transactions_by_platform = graphene.List(TransactionType, platformId=graphene.ID())
    def resolve_transactions_by_platform(self, info, platformId):
        return Transaction.objects.filter(platform=platformId)

schema = graphene.Schema(query = Query, mutation=Mutations, types=[AccountType, ActivityType, CurrencyType, PlatformType, StockType, TransactionType])
