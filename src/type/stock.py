from type.custom_node import CustomNode as Node
from graphene_mongo import MongoengineObjectType

from models.models import Stock as StockModel 
import graphene
from type.market_quote import MarketQuote, resolve_market_quote

class StockType(MongoengineObjectType):
    market_quote = graphene.Field(MarketQuote)

    def resolve_market_quote(self, info):
        return resolve_market_quote(self)

    class Meta:
        description = "Stocks"
        model = StockModel
        interfaces = (Node,)
