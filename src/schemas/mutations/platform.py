from graphene import (
    InputObjectType,
    ID,
    Mutation,
    Field,
    String,
    Boolean,
)
from models.models import (
    Platform
)
from type.platform import PlatformType
from cache.queries import invalidate_transactions
from schemas.profiles import require_profile, owned_record
from models.transaction import Transaction
from graphql import GraphQLError

class  PlatformInput(InputObjectType):
    id = ID()
    name = String()
    account = ID()
    currency = ID()

class CreatePlatformMutation(Mutation):
    platform = Field(PlatformType)

    class Arguments:
        platform_data = PlatformInput(required=True)
        profile_id = ID(required=True)
    
    def mutate(self, info, profile_id, platform_data=None):
        profile = require_profile(profile_id)
        if not platform_data.name or not platform_data.name.strip():
            raise GraphQLError("Enter a platform name.")
        platform = Platform(
            profile=profile,
            name=platform_data.name.strip(),
            account=platform_data.account,
            currency=platform_data.currency
        )
        platform.save()
        invalidate_transactions()

        return CreatePlatformMutation(platform=platform)

class DeletePlatformMutation(Mutation):
    class Arguments:
        id = ID(required=True)
        profile_id = ID(required=True)

    success = Boolean()

    def mutate(self, info, id, profile_id):
        platform = owned_record(Platform, profile_id, id)
        if Transaction.objects(platform=platform).count():
            raise GraphQLError("Cannot delete a platform with transactions.")
        try:
            platform.delete()
            invalidate_transactions()
            success = True
        except Exception:
            success = False
    
        return DeletePlatformMutation(success=success)
