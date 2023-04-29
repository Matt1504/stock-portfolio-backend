from graphene import (
    InputObjectType,
    ID,
    Mutation,
    Field,
    String,
)
from models.models import (
    Account
)
from type.account import AccountType

class  AccountInput(InputObjectType):
    id = ID()
    name = String()
    code = String()

class CreateAccountMutation(Mutation):
    account = Field(AccountType)

    class Arguments:
        account_data = AccountInput(required=True)
    
    def mutate(self, info, account_data=None):
        if Account.objects.filter(name=account_data.name):
            return
        account = Account(
            name=account_data.name,
            code=account_data.code
        )
        account.save()

        return CreateAccountMutation(account=account)