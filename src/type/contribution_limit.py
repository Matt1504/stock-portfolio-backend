from type.custom_node import CustomNode as Node
from graphene_mongo import MongoengineObjectType

from models.models import ContributionLimit as ContributionLimitModel 

class ContributionLimitType(MongoengineObjectType):
    class Meta:
        description = "Contribution Limits"
        model = ContributionLimitModel
        interfaces = (Node,)
