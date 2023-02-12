from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Platforms as PlatformsModel 

class Platforms(MongoengineObjectType):
    class Meta:
        description = "Platforms"
        model = PlatformsModel
        interfaces = (Node,)
