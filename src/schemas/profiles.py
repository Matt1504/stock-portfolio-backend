"""Profile ownership checks shared by every personal query and mutation.

Profiles are tracked people, not authenticated users or access-control identities.
"""
from graphene import ID, PageInfo
from graphene_mongo import MongoengineConnectionField
from graphql import GraphQLError
from graphql_relay.connection.arrayconnection import connection_from_list_slice
from mongoengine import DoesNotExist, ValidationError
from models.models import Profile, Platform, Transaction


def require_profile(profile_id):
    try:
        return Profile.objects.get(pk=profile_id)
    except (DoesNotExist, ValidationError):
        raise GraphQLError("Profile not found. Select a valid profile.")


def owned_platform(profile_id, platform_id):
    profile = require_profile(profile_id)
    try:
        return Platform.objects.get(pk=platform_id, profile=profile)
    except (DoesNotExist, ValidationError):
        raise GraphQLError("Platform does not belong to the selected profile.")


def personal_records(model, profile_id):
    profile = require_profile(profile_id)
    if model is Transaction:
        return Transaction.objects(platform__in=Platform.objects(profile=profile).scalar("id"))
    return model.objects(profile=profile)


def owned_record(model, profile_id, record_id):
    try:
        return personal_records(model, profile_id).get(pk=record_id)
    except (DoesNotExist, ValidationError):
        raise GraphQLError("Record does not belong to the selected profile.")


class ProfileConnectionField(MongoengineConnectionField):
    def __init__(self, *args, **kwargs):
        kwargs["profile_id"] = ID(required=True)
        super().__init__(*args, **kwargs)

    def default_resolver(self, root, info, **args):
        profile_id = args.pop("profile_id")
        pagination = {name: args.pop(name, None) for name in ("first", "last", "before", "after")}
        record_id = args.pop("id", None)
        scoped = personal_records(self.model, profile_id)
        if record_id is not None:
            scoped = scoped.filter(pk=record_id)
        # All filters, including generated reference filters, are intersected
        # with ownership. An id lookup must not bypass the profile restriction.
        filtered = self.get_queryset(self.model, info, **args)
        documents = list(scoped.filter(pk__in=filtered.scalar("id")))
        connection = connection_from_list_slice(
            list_slice=documents, args=pagination, list_length=len(documents),
            connection_type=self.type, edge_type=self.type.Edge, pageinfo_type=PageInfo,
        )
        connection.iterable = documents
        return connection
