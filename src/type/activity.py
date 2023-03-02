from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Activity as ActivityModel 

class ActivityType(MongoengineObjectType):
    class Meta:
        description = "Activities"
        model = ActivityModel
        interfaces = (Node,)
