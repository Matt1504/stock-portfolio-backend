from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Platform as PlatformModel 

class PlatformType(MongoengineObjectType):
    class Meta:
        description = "Platforms"
        model = PlatformModel
        interfaces = (Node,)
