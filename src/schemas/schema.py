import graphene 
from mongoengine import Q
from datetime import datetime, timedelta
from graphene_mongo import MongoengineConnectionField
from graphene import ObjectType
from graphql import GraphQLError
from type.type import (
    AssetType,
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
    BulkUpdateTransactionsMutation,
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
from query_loading import materialize_references
from schemas.queries.connections import MaterializedConnectionField

from schemas.statement_import import StatementPreview, ImportStatementMutation, preview_statement

class Mutations(ObjectType):
    import_statement = ImportStatementMutation.Field()
    create_profile = CreateProfileMutation.Field()
    create_platform = CreatePlatformMutation.Field()
    delete_platform = DeletePlatformMutation.Field()
    create_stock = CreateStockMutation.Field()
    update_stock = UpdateStockMutation.Field()
    delete_stock = DeleteStockMutation.Field()
    create_transaction = CreateTransactionMutation.Field()
    update_transaction = UpdateTransactionMutation.Field()
    bulk_update_transactions = BulkUpdateTransactionsMutation.Field()
    delete_transaction = DeleteTransactionMutation.Field()
    transfer_account = TransferTransactionMutation.Field()
    create_contribution_limit = CreateContributionLimitMutation.Field()
    delete_contribution_limit = DeleteContributionLimitMutation.Field()
    create_account = CreateAccountMutation.Field()
from schemas.transaction_search import TransactionSearchResult, search_transactions

from schemas.account_transfer import AccountTransferPreview, preview as preview_account_transfer
from schemas.market_valuation import MarketValuation, market_valuation

class Query(ObjectType):
    market_valuation = graphene.Field(MarketValuation, profile_id=graphene.ID(required=True), currency=graphene.String(required=True), platform=graphene.ID(), stock=graphene.ID(), account=graphene.ID())

    def resolve_market_valuation(self, info, profile_id, currency, **args):
        return market_valuation(profile_id, currency, **args)

    preview_account_transfer = graphene.Field(AccountTransferPreview, profile_id=graphene.ID(required=True), trans_from=graphene.ID(required=True), trans_to=graphene.ID(required=True), transfer_date=graphene.Date(required=True), close_original_account=graphene.Boolean(default_value=True))
    def resolve_preview_account_transfer(self, info, profile_id, trans_from, trans_to, transfer_date, close_original_account=True):
        return preview_account_transfer(profile_id, trans_from, trans_to, transfer_date, close_original_account)

    search_transactions = graphene.Field(TransactionSearchResult, profile_id=graphene.ID(required=True), account=graphene.ID(), platform=graphene.ID(), stock=graphene.ID(), activity=graphene.ID(), currency=graphene.ID(), start_date=graphene.Date(), end_date=graphene.Date(), first=graphene.Int(default_value=100), after=graphene.String())
    def resolve_search_transactions(self, info, profile_id, **args):
        return search_transactions(profile_id, **args)

    preview_statement_import = graphene.Field(StatementPreview, profile_id=graphene.ID(required=True), platform=graphene.ID(required=True), text=graphene.String(required=True))
    def resolve_preview_statement_import(self, info, profile_id, platform, text):
        return preview_statement(info, profile_id, platform, text)


    assets = MongoengineConnectionField(AssetType)
    outstanding_gic_purchases = graphene.List(TransactionType, profile_id=graphene.ID(required=True), platform=graphene.ID(required=True), stock=graphene.ID())
    def resolve_outstanding_gic_purchases(self, info, profile_id, platform, stock=None):
        from schemas.gic import outstanding_purchases
        return outstanding_purchases(profile_id, platform, stock)

    accounts = CachedAccountsField(AccountType)
    activities = MongoengineConnectionField(ActivityType)
    currencies = MongoengineConnectionField(CurrencyType)
    profiles = MongoengineConnectionField(ProfileType)
    platforms = ProfileConnectionField(PlatformType)
    stocks = MaterializedConnectionField(StockType)
    transactions = ProfileConnectionField(TransactionType)
    contribution_limits = ProfileConnectionField(ContributionLimitType)

    # TODO: Move these to its own file similar to mutation
    transactions_by_stock = graphene.List(TransactionType, profile_id=graphene.ID(required=True), stock=graphene.ID(required=True))
    def resolve_transactions_by_stock(self, info, profile_id, stock):
        return materialize_references(personal_records(Transaction, profile_id).filter(Q(stock=stock) | Q(spinoff_source=stock)))
    
    transactions_by_account = graphene.List(TransactionType, profile_id=graphene.ID(required=True), account=graphene.ID(required=True))
    def resolve_transactions_by_account(self, info, profile_id, account):
        return transactions_by_account(account, profile_id, force_refresh=bypass_cache(info))
    
    transactions_by_platform = graphene.List(TransactionType, profile_id=graphene.ID(required=True), platform=graphene.ID(required=True))
    def resolve_transactions_by_platform(self, info, profile_id, platform):
        owned_platform(profile_id, platform)
        return materialize_references(personal_records(Transaction, profile_id).filter(platform=platform))
    
    transactions_by_activity = graphene.List(TransactionType, profile_id=graphene.ID(required=True), activity=graphene.ID(required=True))
    def resolve_transactions_by_activity(self, info, profile_id, activity):
        return materialize_references(personal_records(Transaction, profile_id).filter(activity=activity))
    
    contribution_limits_by_account = graphene.List(ContributionLimitType, profile_id=graphene.ID(required=True), account=graphene.ID(required=True))
    def resolve_contribution_limits_by_account(self, info, profile_id, account):
        return materialize_references(personal_records(ContributionLimit, profile_id).filter(account=account))
    
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
        return materialize_references(transactions.order_by("-transaction_date", "-id"))

    transactions_from_this_week = graphene.List(TransactionType, profile_id=graphene.ID(required=True))
    def resolve_transactions_from_this_week(self, info, profile_id):
        today = datetime.today()
        last_week = today - timedelta(days=7)
        return materialize_references(personal_records(Transaction, profile_id).filter(transaction_date__gte=last_week))
    
    transactions_from_last_month = graphene.List(TransactionType, profile_id=graphene.ID(required=True))
    def resolve_transactions_from_last_month(self, info, profile_id):
        today = datetime.today()
        last_month = today - timedelta(days=30)
        return materialize_references(personal_records(Transaction, profile_id).filter(transaction_date__gte=last_month))

schema = graphene.Schema(query = Query, mutation=Mutations, types=[AssetType, AccountType, ActivityType, CurrencyType, PlatformType, StockType, TransactionType, ProfileType])
