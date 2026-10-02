import os
import re
import sys
import unittest
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, patch

import fakeredis
import mongomock
from mongoengine import connect, disconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cache.backend import ReadCache
from cache.documents import decode_documents, encode_documents
from cache.queries import invalidate_transactions
from cache.settings import CacheSettings
from models.models import Account, Activity, Currency, Platform, Stock, Transaction, Profile
from schemas.schema import schema


SETTINGS = CacheSettings(
    enabled=True,
    redis_url="redis://localhost:6379/0",
    prefix="stock-portfolio:test:v1",
    reference_ttl=604800,
    transaction_ttl=86400,
    socket_timeout=0.25,
)


class ReadCacheTests(unittest.TestCase):
    def setUp(self):
        self.server = fakeredis.FakeServer()
        self.redis = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        self.cache = ReadCache(SETTINGS, client=self.redis)
        self.loader = Mock(return_value=[Account(name="TFSA", code="TFSA")])

    def read(self, parameters=None):
        return self.cache.get_or_load(
            "accounts", parameters or {}, SETTINGS.reference_ttl,
            self.loader, encode_documents,
            lambda payload: decode_documents(Account, payload),
        )

    def test_miss_hit_expiry_and_ttl(self):
        self.read()
        self.assertEqual(self.read()[0].code, "TFSA")
        self.loader.assert_called_once()
        key = self.cache._key("accounts", {})
        self.assertGreater(self.redis.ttl(key), SETTINGS.reference_ttl - 5)
        self.redis.expire(key, 0)
        self.read()
        self.assertEqual(self.loader.call_count, 2)

    def test_empty_results_are_cached(self):
        self.loader.return_value = []
        self.assertEqual(self.read(), [])
        self.assertEqual(self.read(), [])
        self.loader.assert_called_once()

    def test_filters_are_isolated_and_argument_order_is_canonical(self):
        self.read({"code": "TFSA", "name": "TFSA"})
        self.read({"name": "TFSA", "code": "TFSA"})
        self.loader.assert_called_once()
        self.read({"code": "RRSP"})
        self.assertEqual(self.loader.call_count, 2)

    def test_disabled_cache_never_contacts_redis(self):
        self.cache = ReadCache(replace(SETTINGS, enabled=False), client=Mock())
        self.read()
        self.read()
        self.assertEqual(self.loader.call_count, 2)
        self.cache.client.get.assert_not_called()

    def test_invalidation_retires_all_variants(self):
        self.read({"code": "TFSA"})
        self.read({"code": "RRSP"})
        self.cache.invalidate("accounts")
        self.read({"code": "TFSA"})
        self.read({"code": "RRSP"})
        self.assertEqual(self.loader.call_count, 4)

    def test_overlapping_load_cannot_repopulate_active_generation(self):
        def overlapping_load():
            self.cache.invalidate("accounts")
            return [Account(name="Old", code="OLD")]

        self.loader.side_effect = overlapping_load
        self.read()
        self.loader.side_effect = None
        self.assertEqual(self.read()[0].code, "TFSA")
        self.assertEqual(self.loader.call_count, 2)

    def test_evicted_generation_does_not_reactivate_old_entries(self):
        self.read()
        self.redis.delete(self.cache._generation_key("accounts"))
        self.read()
        self.assertEqual(self.loader.call_count, 2)

    def test_redis_outage_falls_back_to_loader(self):
        self.server.connected = False
        self.assertEqual(self.read()[0].code, "TFSA")
        self.loader.assert_called_once()

    def test_failed_invalidation_is_retried_before_serving_cache(self):
        self.read()
        self.server.connected = False
        self.cache.invalidate("accounts")
        self.loader.return_value = [Account(name="New", code="NEW")]
        self.assertEqual(self.read()[0].code, "NEW")
        self.server.connected = True
        self.assertEqual(self.read()[0].code, "NEW")
        self.assertEqual(self.read()[0].code, "NEW")
        self.assertEqual(self.loader.call_count, 3)

    def test_corrupt_payload_is_replaced(self):
        self.read()
        key = self.cache._key("accounts", {})
        self.redis.set(key, "{invalid")
        self.assertEqual(self.read()[0].code, "TFSA")
        self.read()
        self.assertEqual(self.loader.call_count, 2)

    def test_invalid_bson_document_is_reloaded(self):
        self.read()
        key = self.cache._key("accounts", {})
        self.redis.set(key, '[{"data": {"_id": {"$oid": "invalid"}}, "references": {}}]')
        self.assertEqual(self.read()[0].code, "TFSA")
        self.assertEqual(self.loader.call_count, 2)

    def test_environment_namespaces_do_not_share_entries(self):
        self.read()
        self.cache = ReadCache(replace(SETTINGS, prefix="stock-portfolio:production:v1"), client=self.redis)
        self.read()
        self.assertEqual(self.loader.call_count, 2)

    def test_database_failure_is_not_cached_or_hidden(self):
        self.loader.side_effect = RuntimeError("MongoDB failure")
        with self.assertRaisesRegex(RuntimeError, "MongoDB failure"):
            self.read()
        self.assertIsNone(self.redis.get(self.cache._key("accounts", {})))

    def test_cache_write_failure_does_not_fail_successful_read(self):
        def load_then_disconnect():
            self.server.connected = False
            return [Account(code="TFSA")]

        self.loader.side_effect = load_then_disconnect
        self.assertEqual(self.read()[0].code, "TFSA")


class SettingsTests(unittest.TestCase):
    def test_default_is_disabled_without_url(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = CacheSettings.from_environment()
        self.assertFalse(settings.enabled)
        self.assertEqual(settings.reference_ttl, 604800)
        self.assertEqual(settings.transaction_ttl, 86400)

    def test_url_enables_cache_and_environment_is_namespaced(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379", "APP_ENV": "test"}, clear=True):
            settings = CacheSettings.from_environment()
        self.assertTrue(settings.enabled)
        self.assertEqual(settings.prefix, "stock-portfolio:test:v1")

    def test_explicit_disable_overrides_url(self):
        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379", "CACHE_ENABLED": "false"}, clear=True):
            self.assertFalse(CacheSettings.from_environment().enabled)

    def test_nonpositive_ttl_is_rejected(self):
        with patch.dict(os.environ, {"CACHE_REFERENCE_TTL_SECONDS": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "must be positive"):
                CacheSettings.from_environment()


class GraphQLCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Do not import database.database: all tests use an isolated fake DB.
        disconnect()
        with patch("mongoengine.connection.MongoClient", mongomock.MongoClient):
            connect("portfolio_cache_tests", uuidRepresentation="standard")

    @classmethod
    def tearDownClass(cls):
        disconnect()

    def setUp(self):
        for model in (Transaction, Stock, Platform, Account, Activity, Currency, Profile):
            model.drop_collection()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        self.cache = ReadCache(SETTINGS, client=self.redis)
        self.cache_patch = patch("cache.queries.cache", self.cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)
        self.profile = Profile(name="Owner").save()
        self.account = Account(name="Tax-Free Savings", code="TFSA").save()
        self.other_account = Account(name="Retirement", code="RRSP").save()
        self.currency = Currency(name="Canadian Dollar", code="CAD").save()
        self.platform = Platform(name="Broker", account=self.account, currency=self.currency, profile=self.profile).save()
        self.destination = Platform(name="New Broker", account=self.account, currency=self.currency, profile=self.profile).save()
        self.stock = Stock(name="Example", ticker="EX", currency=self.currency).save()
        self.activity = Activity(name="Buy").save()
        self.transaction = Transaction(
            account=self.account, platform=self.platform, stock=self.stock,
            activity=self.activity, shares=2, price=Decimal("12.34"),
            total=Decimal("25.68"), fee=Decimal("1.00"),
            transaction_date=date(2025, 1, 2), description="Example purchase",
        ).save()

    def execute(self, query, variables=None, context=None):
        for field in ("transactionsByAccount", "createTransaction", "updateTransaction", "deleteTransaction", "transferAccount"):
            query = re.sub(r"\b" + field + r"\(", field + '(profileId: "' + str(self.profile.id) + '", ', query)
        query = query.replace("$account: ID)", "$account: ID!)")
        result = schema.execute(query, variables=variables, context_value=context)
        self.assertFalse(result.errors, result.errors)
        return result.data

    def transactions_query(self, account=None):
        return self.execute("""
            query($account: ID) {
                transactionsByAccount(account: $account) {
                    id shares price total fee transactionDate description
                    account { id code name }
                    activity { id name }
                    stock { id name ticker currency { id code } }
                    platform { id name account { id code } currency { id code } }
                }
            }
        """, {"account": str((account or self.account).id)})

    def test_accounts_preserve_response_and_hit_avoids_database(self):
        query = "{ accounts { edges { cursor node { id name code } } pageInfo { hasNextPage endCursor } } }"
        with patch("cache.queries.cache", ReadCache(replace(SETTINGS, enabled=False))):
            expected = self.execute(query)
        self.assertEqual(self.execute(query), expected)
        with patch.object(mongomock.collection.Collection, "find", side_effect=AssertionError("unexpected MongoDB read")):
            self.assertEqual(self.execute(query), expected)

    def test_cold_request_bypasses_and_replaces_cached_accounts_and_transactions(self):
        query = 'query($account: ID) { accounts { edges { node { id name } } } transactionsByAccount(account: $account) { id shares } }'
        variables = {"account": str(self.account.id)}
        cached = self.execute(query, variables)
        Account.objects(id=self.account.id).update_one(set__name="Changed directly")
        Transaction.objects(account=self.account).update(set__shares=9)
        self.assertEqual(self.execute(query, variables), cached)
        refreshed = self.execute(query, variables, SimpleNamespace(headers={"X-Cache-Bypass": "true"}))
        self.assertEqual(refreshed["transactionsByAccount"][0]["shares"], 9)
        account = next(edge["node"] for edge in refreshed["accounts"]["edges"] if edge["node"]["id"] == str(self.account.id))
        self.assertEqual(account["name"], "Changed directly")
        with patch.object(mongomock.collection.Collection, "find", side_effect=AssertionError("unexpected MongoDB read")):
            self.assertEqual(self.execute(query, variables), refreshed)

    def test_account_filters_and_cursor_pagination_share_cached_data(self):
        first = self.execute("{ accounts(first: 1) { edges { node { code } } pageInfo { hasNextPage endCursor } } }")
        cursor = first["accounts"]["pageInfo"]["endCursor"]
        self.assertTrue(first["accounts"]["pageInfo"]["hasNextPage"])
        with patch.object(mongomock.collection.Collection, "find", side_effect=AssertionError("unexpected MongoDB read")):
            second = self.execute("query($after: String) { accounts(first: 1, after: $after) { edges { node { code } } } }", {"after": cursor})
        self.assertEqual(second["accounts"]["edges"][0]["node"]["code"], "RRSP")
        filtered = self.execute('{ accounts(code: "TFSA") { edges { node { code } } } }')
        self.assertEqual(len(filtered["accounts"]["edges"]), 1)
        by_id = self.execute("query($id: ID) { accounts(id: $id) { edges { node { id } } } }", {"id": str(self.account.id)})
        self.assertEqual(by_id["accounts"]["edges"][0]["node"]["id"], str(self.account.id))

    def test_transactions_preserve_nested_response_and_hit_avoids_database(self):
        with patch("cache.queries.cache", ReadCache(replace(SETTINGS, enabled=False))):
            expected = self.transactions_query()
        self.assertEqual(self.transactions_query(), expected)
        with patch.object(mongomock.collection.Collection, "find", side_effect=AssertionError("unexpected MongoDB read")):
            self.assertEqual(self.transactions_query(), expected)
        key = self.cache._key("transactions", {"query": "by_account", "account": str(self.account.id), "profile": str(self.profile.id)})
        self.assertGreater(self.redis.ttl(key), SETTINGS.transaction_ttl - 5)

    def test_cached_data_supports_a_different_graphql_selection(self):
        self.execute("query($account: ID) { transactionsByAccount(account: $account) { id } }", {"account": str(self.account.id)})
        with patch.object(mongomock.collection.Collection, "find", side_effect=AssertionError("unexpected MongoDB read")):
            row = self.transactions_query()["transactionsByAccount"][0]
        self.assertEqual(row["stock"]["currency"]["code"], "CAD")
        self.assertEqual(row["transactionDate"], "2025-01-02")

    def test_transaction_account_filters_are_isolated(self):
        self.assertEqual(len(self.transactions_query()["transactionsByAccount"]), 1)
        self.assertEqual(self.transactions_query(self.other_account)["transactionsByAccount"], [])

    def test_create_account_invalidates_cached_accounts(self):
        query = "{ accounts { edges { node { code } } } }"
        self.execute(query)
        self.execute('mutation { createAccount(accountData: {name: "First Home", code: "FHSA"}) { account { id } } }')
        self.assertEqual(len(self.execute(query)["accounts"]["edges"]), 3)

    def test_create_update_delete_transaction_refresh_cached_list(self):
        self.transactions_query()
        created = self.execute("""
            mutation($trans: TransactionInput!) {
                createTransaction(transData: $trans) { transaction { id } }
            }
        """, {"trans": {
            "account": str(self.account.id), "platform": str(self.platform.id),
            "activity": str(self.activity.id), "stock": str(self.stock.id),
            "shares": 1, "total": "12.34", "transactionDate": "2025-01-03",
        }})
        self.assertEqual(len(self.transactions_query()["transactionsByAccount"]), 2)
        self.execute("""
            mutation($trans: TransactionInput!) {
                updateTransaction(transData: $trans) { trans { id } }
            }
        """, {"trans": {"id": str(self.transaction.id), "total": "30.00"}})
        rows = self.transactions_query()["transactionsByAccount"]
        self.assertEqual(next(row for row in rows if row["id"] == str(self.transaction.id))["total"], 30.0)
        self.execute("mutation($id: ID!) { deleteTransaction(id: $id) { success } }", {"id": created["createTransaction"]["transaction"]["id"]})
        self.assertEqual(len(self.transactions_query()["transactionsByAccount"]), 1)

    def test_stock_edit_refreshes_embedded_transaction_details(self):
        self.transactions_query()
        self.execute("mutation($stock: StockInput!) { updateStock(stockData: $stock) { stock { id } } }", {"stock": {"id": str(self.stock.id), "name": "Renamed"}})
        self.assertEqual(self.transactions_query()["transactionsByAccount"][0]["stock"]["name"], "Renamed")

    def test_invalidation_refreshes_old_and_new_account_lists(self):
        self.transactions_query()
        self.transactions_query(self.other_account)
        self.transaction.account = self.other_account
        self.transaction.save()
        invalidate_transactions()
        self.assertEqual(self.transactions_query()["transactionsByAccount"], [])
        self.assertEqual(len(self.transactions_query(self.other_account)["transactionsByAccount"]), 1)

    def test_transfer_refreshes_cached_platform_details(self):
        self.transactions_query()
        result = self.execute("mutation($from: ID!, $to: ID!) { transferAccount(transFrom: $from, transTo: $to) { success } }", {"from": str(self.platform.id), "to": str(self.destination.id)})
        self.assertTrue(result["transferAccount"]["success"])
        self.assertEqual(self.transactions_query()["transactionsByAccount"][0]["platform"]["name"], "New Broker")

    def test_partial_transfer_failure_also_invalidates(self):
        second = Transaction(account=self.account, platform=self.platform, activity=self.activity, total=1).save()
        self.transactions_query()
        original_save = Transaction.save

        def fail_second(document, *args, **kwargs):
            if document.id == second.id:
                raise RuntimeError("Transfer interrupted")
            return original_save(document, *args, **kwargs)

        with patch.object(Transaction, "save", fail_second):
            result = self.execute("mutation($from: ID!, $to: ID!) { transferAccount(transFrom: $from, transTo: $to) { success } }", {"from": str(self.platform.id), "to": str(self.destination.id)})
        self.assertFalse(result["transferAccount"]["success"])
        rows = self.transactions_query()["transactionsByAccount"]
        self.assertEqual(next(row for row in rows if row["id"] == str(self.transaction.id))["platform"]["name"], "New Broker")


if __name__ == "__main__":
    unittest.main()
