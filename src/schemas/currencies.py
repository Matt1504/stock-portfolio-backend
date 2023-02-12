from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Currencies as CurrenciesModel 

class Currencies(MongoengineObjectType):
    class Meta:
        description = "Currencies"
        model = CurrenciesModel
        interfaces = (Node,)
