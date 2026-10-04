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
    Account,
)
from graphql import GraphQLError
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
        profile = require_profile(profile_id)
        account = Account.objects(id=contr_limit_data.account).first()
        if not account:
            raise GraphQLError("Account type was not found.", extensions={"code": "ACCOUNT_NOT_FOUND"})
        if not account.has_contribution_limit:
            raise GraphQLError("{} has no contribution limit.".format(account.name or account.code), extensions={"code": "CONTRIBUTION_LIMIT_NOT_APPLICABLE"})
        contribution_limit = ContributionLimit(
            profile=profile,
            yearEnd = contr_limit_data.yearEnd,
            account = account,
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