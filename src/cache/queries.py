from functools import partial

from graphene import PageInfo
from graphene_mongo import MongoengineConnectionField
from graphql_relay.connection.arrayconnection import connection_from_list_slice

from cache.backend import cache
from cache.documents import decode_documents, encode_documents
from models.models import Transaction


ACCOUNTS = "accounts"
TRANSACTIONS = "transactions"


def invalidate_accounts():
    # Account details are also embedded in transaction snapshots.
    cache.invalidate(ACCOUNTS, TRANSACTIONS)


def invalidate_transactions():
    cache.invalidate(TRANSACTIONS)


class CachedAccountsField(MongoengineConnectionField):
    """Preserve generated account filter arguments and Relay pagination."""

    def default_resolver(self, root, info, **args):
        if not cache.enabled:
            return super().default_resolver(root, info, **args)

        filters = dict(args)
        connection_args = {
            name: filters.pop(name, None)
            for name in ("first", "last", "before", "after")
        }
        account_id = filters.pop("id", None)

        def load():
            if account_id is not None:
                return [self.model.objects.get(pk=account_id)]
            return list(self.get_queryset(self.model, info, **dict(filters)))

        documents = cache.get_or_load(
            ACCOUNTS,
            {"id": account_id, "filters": filters},
            cache.settings.reference_ttl,
            load,
            encode_documents,
            partial(decode_documents, self.model),
        )
        # Cache the filtered dataset and apply pagination afterwards, so cursor
        # variants share data but retain the same edges and pageInfo contract.
        connection = connection_from_list_slice(
            list_slice=documents,
            args=connection_args,
            list_length=len(documents),
            connection_type=self.type,
            edge_type=self.type.Edge,
            pageinfo_type=PageInfo,
        )
        connection.iterable = documents
        return connection


def transactions_by_account(account):
    if not cache.enabled:
        return Transaction.objects.filter(account=account)

    def load():
        return Transaction.objects.filter(account=account).select_related(max_depth=3)

    return cache.get_or_load(
        TRANSACTIONS,
        {"query": "by_account", "account": account},
        cache.settings.transaction_ttl,
        load,
        encode_documents,
        partial(decode_documents, Transaction),
    )
