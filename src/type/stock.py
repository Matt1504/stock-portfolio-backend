from type.custom_node import CustomNode as Node
from graphene_mongo import MongoengineObjectType

from models.models import Stock as StockModel 

class StockType(MongoengineObjectType):
    class Meta:
        description = "Stocks"
        model = StockModel
        interfaces = (Node,)
