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

class  PlatformInput(InputObjectType):
    id = ID()
    name = String()
    account = ID()
    currency = ID()

class CreatePlatformMutation(Mutation):
    platform = Field(PlatformType)

    class Arguments:
        platform_data = PlatformInput(required=True)
    
    def mutate(self, info, platform_data=None):
        platform = Platform(
            name=platform_data.name,
            account=platform_data.account,
            currency=platform_data.currency
        )
        platform.save()
        invalidate_transactions()

        return CreatePlatformMutation(platform=platform)

class DeletePlatformMutation(Mutation):
    class Arguments:
        id = ID(required=True)

    success = Boolean()

    def mutate(self, info, id):
        try:
            Platform.objects.get(pk=id).delete()
            invalidate_transactions()
            success = True
        except Exception:
            success = False
    
        return DeletePlatformMutation(success=success)
