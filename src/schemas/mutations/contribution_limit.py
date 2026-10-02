from graphene import (
    InputObjectType, 
    ID, 
    Mutation,
    Field, 
    Date,
    Boolean,
    Decimal
)
from models.models import (
    ContributionLimit,
)
from type.contribution_limit import ContributionLimitType
from schemas.profiles import require_profile, owned_record

class ContributionLimitInput(InputObjectType):
    id = ID()
    account = ID()
    yearEnd = Date()
    amount = Decimal()

class CreateContributionLimitMutation(Mutation):
    contribution_limit = Field(ContributionLimitType)

    class Arguments:
        contr_limit_data = ContributionLimitInput(required=True)
        profile_id = ID(required=True)

    def mutate(self, info, profile_id, contr_limit_data=None):
        contribution_limit = ContributionLimit(
            profile=require_profile(profile_id),
            yearEnd = contr_limit_data.yearEnd,
            account = contr_limit_data.account,
            amount = contr_limit_data.amount
        ) 
        contribution_limit.save()

        return CreateContributionLimitMutation(contribution_limit=contribution_limit)

class DeleteContributionLimitMutation(Mutation):
    class Arguments:
        id = ID(required=True)
        profile_id = ID(required=True)
        
    success = Boolean()

    def mutate(self, info, id, profile_id):
        limit = owned_record(ContributionLimit, profile_id, id)
        try:
            limit.delete()
            success = True
        except Exception:
            success = False
    
        return DeleteContributionLimitMutation(success=success)