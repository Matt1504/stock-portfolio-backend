from type.custom_node import CustomNode as Node
from graphene_mongo import MongoengineObjectType

from models.models import Account as AccountModel 

class AccountType(MongoengineObjectType):
    class Meta:
        description = "Account"
        model = AccountModel
        interfaces = (Node,) 
