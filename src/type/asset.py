from type.custom_node import CustomNode as Node
from graphene_mongo import MongoengineObjectType
from models.asset import Asset


class AssetType(MongoengineObjectType):
    class Meta:
        model = Asset
        interfaces = (Node,)
