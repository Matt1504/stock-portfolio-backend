from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Account as AccountModel 

class AccountType(MongoengineObjectType):
    class Meta:
        description = "Account"
        model = AccountModel
        interfaces = (Node,) 
