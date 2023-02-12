from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Accounts as AccountsModel 

class Accounts(MongoengineObjectType):
    class Meta:
        description = "Accounts"
        model = AccountsModel
        interfaces = (Node,)
