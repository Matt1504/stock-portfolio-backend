from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Stocks as StocksModel 

class Stocks(MongoengineObjectType):
    class Meta:
        description = "Stocks"
        model = StocksModel
        interfaces = (Node,)
