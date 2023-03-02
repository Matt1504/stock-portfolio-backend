from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Transaction as TransactionModel 

class TransactionType(MongoengineObjectType):
    class Meta:
        description = "Transactions"
        model = TransactionModel
        interfaces = (Node,)
