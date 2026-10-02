from graphene_mongo import MongoengineObjectType
from type.custom_node import CustomNode as Node
from models.profile import Profile


class ProfileType(MongoengineObjectType):
    class Meta:
        model = Profile
        interfaces = (Node,)
