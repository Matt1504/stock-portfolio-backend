from graphene_mongo import MongoengineConnectionField

from query_loading import materialize_references


class MaterializedConnectionField(MongoengineConnectionField):
    """Keep existing Relay filters/cursors; batch references of returned nodes."""

    def default_resolver(self, root, info, **args):
        connection = super().default_resolver(root, info, **args)
        materialize_references(edge.node for edge in connection.edges if edge.node is not None)
        return connection
