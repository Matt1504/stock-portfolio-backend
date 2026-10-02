import graphene 
from datetime import datetime, timedelta
from graphene_mongo import MongoengineConnectionField
from graphene import ObjectType
from graphql import GraphQLError
from type.type import (
    AccountType,
    ActivityType,
    CurrencyType,
    PlatformType,
    StockType,
    TransactionType,
    ContributionLimitType,
    ProfileType
)
from schemas.mutations.stock import (
    CreateStockMutation,
    UpdateStockMutation,
    DeleteStockMutation
)
from schemas.mutations.transaction import (
    CreateTransactionMutation,
    UpdateTransactionMutation,
    DeleteTransactionMutation,
    TransferTransactionMutation
)
from schemas.mutations.platform import (
    CreatePlatformMutation,
    DeletePlatformMutation,
)
from schemas.mutations.contribution_limit import (
    CreateContributionLimitMutation,
    DeleteContributionLimitMutation
)
from schemas.mutations.account import (
    CreateAccountMutation
)
from models.models import Transaction, ContributionLimit
from schemas.profiles import ProfileConnectionField, personal_records, owned_platform
from schemas.mutations.profile import CreateProfileMutation
from cache.queries import CachedAccountsField, transactions_by_account, bypass_cache

class Mutations(ObjectType):
    create_profile = CreateProfileMutation.Field()
    create_platform = CreatePlatformMutation.Field()
    delete_platform = DeletePlatformMutation.Field()
    create_stock = CreateStockMutation.Field()
    update_stock = UpdateStockMutation.Field()
    delete_stock = DeleteStockMutation.Field()
    create_transaction = CreateTransactionMutation.Field()
    update_transaction = UpdateTransactionMutation.Field()
    delete_transaction = DeleteTransactionMutation.Field()
    transfer_account = TransferTransactionMutation.Field()
    create_contribution_limit = CreateContributionLimitMutation.Field()
    delete_contribution_limit = DeleteContributionLimitMutation.Field()
    create_account = CreateAccountMutation.Field()
class Query(ObjectType):

    accounts = CachedAccountsField(AccountType)
    activities = MongoengineConnectionField(ActivityType)
    currencies = MongoengineConnectionField(CurrencyType)
    profiles = MongoengineConnectionField(ProfileType)
    platforms = ProfileConnectionField(PlatformType)
    stocks = MongoengineConnectionField(StockType)
    transactions = ProfileConnectionField(TransactionType)
    contribution_limits = ProfileConnectionField(ContributionLimitType)

    # TODO: Move these to its own file similar to mutation
    transactions_by_stock = graphene.List(TransactionType, profile_id=graphene.ID(required=True), stock=graphene.ID(required=True))
    def resolve_transactions_by_stock(self, info, profile_id, stock):
        return personal_records(Transaction, profile_id).filter(stock=stock)
    
    transactions_by_account = graphene.List(TransactionType, profile_id=graphene.ID(required=True), account=graphene.ID(required=True))
    def resolve_transactions_by_account(self, info, profile_id, account):
        return transactions_by_account(account, profile_id, force_refresh=bypass_cache(info))
    
    transactions_by_platform = graphene.List(TransactionType, profile_id=graphene.ID(required=True), platform=graphene.ID(required=True))
    def resolve_transactions_by_platform(self, info, profile_id, platform):
        owned_platform(profile_id, platform)
        return personal_records(Transaction, profile_id).filter(platform=platform)
    
    transactions_by_activity = graphene.List(TransactionType, profile_id=graphene.ID(required=True), activity=graphene.ID(required=True))
    def resolve_transactions_by_activity(self, info, profile_id, activity):
        return personal_records(Transaction, profile_id).filter(activity=activity)
    
    contribution_limits_by_account = graphene.List(ContributionLimitType, profile_id=graphene.ID(required=True), account=graphene.ID(required=True))
    def resolve_contribution_limits_by_account(self, info, profile_id, account):
        return personal_records(ContributionLimit, profile_id).filter(account=account)
    
    transactions_by_date_range = graphene.List(
        TransactionType, profile_id=graphene.ID(required=True),
        start_date=graphene.Date(), end_date=graphene.Date(),
    )
    def resolve_transactions_by_date_range(self, info, profile_id, start_date=None, end_date=None):
        if start_date and end_date and start_date > end_date:
            raise GraphQLError("Start date must be on or before end date.")
        transactions = personal_records(Transaction, profile_id)
        if start_date:
            transactions = transactions.filter(transaction_date__gte=start_date)
        if end_date:
            transactions = transactions.filter(transaction_date__lte=end_date)
        return transactions.order_by("-transaction_date", "-id")

    transactions_from_this_week = graphene.List(TransactionType, profile_id=graphene.ID(required=True))
    def resolve_transactions_from_this_week(self, info, profile_id):
        today = datetime.today()
        last_week = today - timedelta(days=7)
        return personal_records(Transaction, profile_id).filter(transaction_date__gte=last_week)
    
    transactions_from_last_month = graphene.List(TransactionType, profile_id=graphene.ID(required=True))
    def resolve_transactions_from_last_month(self, info, profile_id):
        today = datetime.today()
        last_month = today - timedelta(days=30)
        return personal_records(Transaction, profile_id).filter(transaction_date__gte=last_month)

schema = graphene.Schema(query = Query, mutation=Mutations, types=[AccountType, ActivityType, CurrencyType, PlatformType, StockType, TransactionType, ProfileType])
