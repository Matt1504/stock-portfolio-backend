import graphene 
from graphene.relay import Node
from graphene_mongo import MongoengineConnectionField

from schemas.accounts import Accounts
from schemas.activities import Activities
from schemas.currencies import Currencies
from schemas.platforms import Platforms
from schemas.stocks import Stocks
from schemas.transactions import Transactions

class Query(graphene.ObjectType):
    node = Node.Field

    all_accounts = MongoengineConnectionField(Accounts)
    all_activities = MongoengineConnectionField(Activities)
    all_currencies = MongoengineConnectionField(Currencies)
    all_platforms = MongoengineConnectionField(Platforms)
    all_stocks = MongoengineConnectionField(Stocks)
    all_transactions = MongoengineConnectionField(Transactions)


schema = graphene.Schema(query = Query, types=[Accounts, Activities, Currencies, Platforms, Stocks, Transactions])
