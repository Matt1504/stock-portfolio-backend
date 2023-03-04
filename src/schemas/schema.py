import graphene 
from graphene.relay import Node
from graphene_mongo import MongoengineConnectionField
from graphene import ObjectType

from type.account import AccountType
from type.activity import ActivityType
from type.currency import CurrencyType
from type.platform import PlatformType
from type.stock import StockType
from type.transaction import TransactionType

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


schema = graphene.Schema(query = Query, mutation=Mutations, types=[AccountType, ActivityType, CurrencyType, PlatformType, StockType, TransactionType])
