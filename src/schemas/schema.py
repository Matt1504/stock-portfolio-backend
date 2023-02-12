import graphene 
from graphene.relay import Node
from graphene_mongo import MongoengineConnectionField
from graphene import ObjectType

from schemas.accounts import Accounts
from schemas.activities import Activities
from schemas.currencies import Currencies
from schemas.platforms import Platforms
from schemas.stocks import Stocks
from schemas.transactions import Transactions

class Query(ObjectType):
    node = Node.Field

    accounts = MongoengineConnectionField(Accounts)
    activities = MongoengineConnectionField(Activities)
    currencies = MongoengineConnectionField(Currencies)
    platforms = MongoengineConnectionField(Platforms)
    stocks = MongoengineConnectionField(Stocks)
    transactions = MongoengineConnectionField(Transactions)

    
    


schema = graphene.Schema(query = Query, types=[Accounts, Activities, Currencies, Platforms, Stocks, Transactions])
