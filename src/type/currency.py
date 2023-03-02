from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Currency as CurrencyModel 

class CurrencyType(MongoengineObjectType):
    class Meta:
        description = "Currencies"
        model = CurrencyModel
        interfaces = (Node,)
