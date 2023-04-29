from graphene import (
    InputObjectType, 
    ID, 
    Mutation,
    Field, 
    Int,
    Boolean,
    Decimal
)
from models.models import (
    ContributionLimit,
)
from type.contribution_limit import ContributionLimitType 

class ContributionLimitInput(InputObjectType):
    id = ID()
    account = ID()
    year = Int()
    amount = Decimal()

class CreateContributionLimitMutation(Mutation):
    contribution_limit = Field(ContributionLimitType)

    class Arguments:
        contr_limit_data = ContributionLimitInput(required=True)

    def mutate(self, info, contr_limit_data=None):
        contribution_limit = ContributionLimit(
            year = contr_limit_data.year,
            account = contr_limit_data.account,
            amount = contr_limit_data.amount
        ) 
        contribution_limit.save()

        return CreateContributionLimitMutation(contribution_limit=contribution_limit)

class DeleteContributionLimitMutation(Mutation):
    class Arguments:
        id = ID(required=True)
        
    success = Boolean()

    def mutate(self, info, id):
        try:
            ContributionLimit.objects.get(pk=id).delete()
            success = True
        except Exception:
            success = False
    
        return DeleteContributionLimitMutation(success=success)