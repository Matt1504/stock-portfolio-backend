from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Transactions as TransactionsModel 

class Transactions(MongoengineObjectType):
    class Meta:
        description = "Transactions"
        model = TransactionsModel
        interfaces = (Node,)
