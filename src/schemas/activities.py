from graphene.relay import Node
from graphene_mongo import MongoengineObjectType

from models.models import Activities as ActivitiesModel 

class Activities(MongoengineObjectType):
    class Meta:
        description = "Activities"
        model = ActivitiesModel
        interfaces = (Node,)
