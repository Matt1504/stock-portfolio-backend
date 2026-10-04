import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import fakeredis
import mongomock
from mongoengine import connect, disconnect
from mongoengine.errors import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from models.models import Profile, Platform, Account, Currency, Transaction, Stock, Activity, ContributionLimit
from schemas.schema import schema
from cache.backend import ReadCache
from cache.settings import CacheSettings
from migrate_profiles import migrate_profiles
from add_withdrawal_activity import add_withdrawal_activity


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        disconnect()
        with patch("mongoengine.connection.MongoClient", mongomock.MongoClient):
            connect("portfolio_profile_tests", uuidRepresentation="standard")

    @classmethod
    def tearDownClass(cls):
        disconnect()

    def setUp(self):
        for model in (Transaction, ContributionLimit, Platform, Profile, Account, Currency, Stock, Activity):
            model.drop_collection()
        self.alice = Profile(name="Alice").save()
        self.bob = Profile(name="Bob").save()
        self.account = Account(name="Tax-Free Savings", code="TFSA").save()
        self.currency = Currency(name="Canadian Dollar", code="CAD").save()
        self.stock = Stock(name="Shared stock", ticker="EX", currency=self.currency).save()
        self.activity = Activity(name="Buy").save()
        self.pa = Platform(name="Wealthsimple", account=self.account, currency=self.currency, profile=self.alice).save()
        self.pb = Platform(name="Wealthsimple", account=self.account, currency=self.currency, profile=self.bob).save()
        self.ta = Transaction(platform=self.pa, account=self.account, activity=self.activity, stock=self.stock,
                              shares=2, total=20, fee=1, transaction_date=date.today()).save()
        self.tb = Transaction(platform=self.pb, account=self.account, activity=self.activity, stock=self.stock,
                              shares=7, total=70, transaction_date=date.today()).save()
        self.la = ContributionLimit(profile=self.alice, account=self.account, amount=1000, yearEnd=date.today()).save()
        self.lb = ContributionLimit(profile=self.bob, account=self.account, amount=9000, yearEnd=date.today()).save()
        self.cache = ReadCache(CacheSettings(True, "redis://localhost", "profiles:test:v1", 604800, 86400, 0.25), fakeredis.FakeRedis(decode_responses=True))
        self.cache_patch = patch("cache.queries.cache", self.cache)
        self.cache_patch.start()
        self.addCleanup(self.cache_patch.stop)

    def execute(self, query, variables=None, errors=False):
        result = schema.execute(query, variables=variables)
        if errors:
            self.assertTrue(result.errors)
        else:
            self.assertFalse(result.errors, result.errors)
        return result

    def query_rows(self, field, profile=None, **arguments):
        arguments["profileId"] = str((profile or self.alice).id)
        args = ", ".join('{}: "{}"'.format(key, value) for key, value in arguments.items())
        return self.execute('{ ' + field + '(' + args + ') { id shares total } }').data[field]

    def test_all_transaction_lists_are_scoped(self):
        for field, args in [("transactionsByStock", {"stock": self.stock.id}),
                            ("transactionsByAccount", {"account": self.account.id}),
                            ("transactionsByActivity", {"activity": self.activity.id}),
                            ("transactionsByPlatform", {"platform": self.pa.id}),
                            ("transactionsFromLastMonth", {}), ("transactionsFromThisWeek", {})]:
            with self.subTest(field=field):
                rows = self.query_rows(field, **args)
                self.assertEqual([row["id"] for row in rows], [str(self.ta.id)])
        self.assertEqual(self.query_rows("transactionsByStock", self.bob, stock=self.stock.id)[0]["shares"], 7)

    def test_personal_connections_and_id_filters_are_scoped(self):
        for field, own, foreign in [("platforms", self.pa, self.pb), ("transactions", self.ta, self.tb), ("contributionLimits", self.la, self.lb)]:
            with self.subTest(field=field):
                query = '{ ' + field + '(profileId: "' + str(self.alice.id) + '") { edges { node { id } } } }'
                self.assertEqual(self.execute(query).data[field]["edges"][0]["node"]["id"], str(own.id))
                query = '{ ' + field + '(profileId: "' + str(self.alice.id) + '", id: "' + str(foreign.id) + '") { edges { node { id } } } }'
                self.assertEqual(self.execute(query).data[field]["edges"], [])

    def test_generated_profile_filter_cannot_override_scope(self):
        query = '{ platforms(profileId: "' + str(self.alice.id) + '", profile: "' + str(self.bob.id) + '") { edges { node { id } } } }'
        self.assertEqual(self.execute(query).data["platforms"]["edges"], [])

    def test_contribution_limits_by_account_are_scoped(self):
        query = '{ contributionLimitsByAccount(profileId: "' + str(self.alice.id) + '", account: "' + str(self.account.id) + '") { amount } }'
        self.assertEqual(self.execute(query).data["contributionLimitsByAccount"], [{"amount": 1000.0}])

    def test_date_range_includes_both_boundaries_and_only_selected_profile(self):
        records = []
        for day in [date(2024, 12, 31), date(2025, 1, 1), date(2025, 1, 31), date(2025, 2, 1)]:
            records.append(Transaction(platform=self.pa, account=self.account, activity=self.activity,
                                       total=1, transaction_date=day).save())
        Transaction(platform=self.pb, account=self.account, activity=self.activity, total=1,
                    transaction_date=date(2025, 1, 15)).save()
        result = self.execute(
            'query($profile: ID!, $start: Date, $end: Date) { transactionsByDateRange(profileId: $profile, startDate: $start, endDate: $end) { id transactionDate } }',
            {"profile": str(self.alice.id), "start": "2025-01-01", "end": "2025-01-31"},
        ).data["transactionsByDateRange"]
        self.assertEqual([row["id"] for row in result], [str(records[2].id), str(records[1].id)])

    def test_date_range_supports_one_day_open_ended_and_all_history(self):
        old = Transaction(platform=self.pa, account=self.account, activity=self.activity,
                          total=1, transaction_date=date(2025, 1, 1)).save()
        query = 'query($profile: ID!, $start: Date, $end: Date) { transactionsByDateRange(profileId: $profile, startDate: $start, endDate: $end) { id } }'
        cases = [({"start": "2025-01-01", "end": "2025-01-01"}, {str(old.id)}),
                 ({"end": "2025-01-01"}, {str(old.id)}),
                 ({"start": "2025-01-02"}, {str(self.ta.id)}),
                 ({}, {str(old.id), str(self.ta.id)})]
        for dates, expected in cases:
            with self.subTest(dates=dates):
                result = self.execute(query, {"profile": str(self.alice.id), **dates}).data["transactionsByDateRange"]
                self.assertEqual({row["id"] for row in result}, expected)

    def test_date_range_rejects_reversed_dates_and_missing_profile(self):
        self.execute('{ transactionsByDateRange(profileId: "' + str(self.alice.id) + '", startDate: "2025-02-01", endDate: "2025-01-01") { id } }', errors=True)
        self.execute('{ transactionsByDateRange(startDate: "2025-01-01") { id } }', errors=True)

    def test_missing_profile_is_rejected(self):
        for query in ['{ platforms { edges { node { id } } } }', '{ transactionsFromLastMonth { id } }',
                      'mutation { deleteTransaction(id: "' + str(self.ta.id) + '") { success } }']:
            self.execute(query, errors=True)

    def test_unknown_profile_is_rejected(self):
        self.execute('{ transactionsFromLastMonth(profileId: "000000000000000000000000") { id } }', errors=True)

    def test_foreign_platform_query_is_rejected(self):
        self.execute('{ transactionsByPlatform(profileId: "' + str(self.alice.id) + '", platform: "' + str(self.pb.id) + '") { id } }', errors=True)

    def test_shared_stock_and_account_definitions(self):
        result = self.execute('{ stocks { edges { node { id } } } accounts { edges { node { id } } } profiles { edges { node { name } } } }').data
        self.assertEqual(len(result["stocks"]["edges"]), 1)
        self.assertEqual(len(result["accounts"]["edges"]), 1)
        self.assertEqual(len(result["profiles"]["edges"]), 2)

    def test_create_profile_validates_name(self):
        data = self.execute('mutation { createProfile(name: "  Charlie  ") { profile { name } } }').data
        self.assertEqual(data["createProfile"]["profile"]["name"], "Charlie")
        self.execute('mutation { createProfile(name: "   ") { profile { id } } }', errors=True)
        self.execute('mutation($name: String!) { createProfile(name: $name) { profile { id } } }', {"name": "x" * 101}, errors=True)

    def test_create_platform_assigns_profile(self):
        result = self.execute('mutation($profile: ID!, $platform: PlatformInput!) { createPlatform(profileId: $profile, platformData: $platform) { platform { id profile { id } } } }',
                              {"profile": str(self.bob.id), "platform": {"name": "Other", "account": str(self.account.id), "currency": str(self.currency.id)}})
        self.assertEqual(result.data["createPlatform"]["platform"]["profile"]["id"], str(self.bob.id))

    def create_transaction(self, platform, account=None, errors=False):
        return self.execute('mutation($profile: ID!, $trans: TransactionInput!) { createTransaction(profileId: $profile, transData: $trans) { transaction { id account { id } } } }',
                            {"profile": str(self.alice.id), "trans": {"platform": str(platform.id), **({"account": str(account.id)} if account else {}), "activity": str(self.activity.id), "total": "5"}}, errors)

    def test_create_transaction_derives_account_and_rejects_foreign_platform(self):
        result = self.create_transaction(self.pa)
        self.assertEqual(result.data["createTransaction"]["transaction"]["account"]["id"], str(self.account.id))
        self.create_transaction(self.pb, errors=True)
        self.assertEqual(Transaction.objects(platform=self.pb).count(), 1)

    def test_contribution_cannot_be_created_or_updated_with_a_stock(self):
        contribution = Activity(name="Contribution").save()
        variables = {"profile": str(self.alice.id), "trans": {"platform": str(self.pa.id), "activity": str(contribution.id), "stock": str(self.stock.id), "total": "50"}}
        self.execute('mutation($profile: ID!, $trans: TransactionInput!) { createTransaction(profileId: $profile, transData: $trans) { transaction { id } } }', variables, errors=True)
        self.assertEqual(Transaction.objects.count(), 2)
        variables["trans"] = {"id": str(self.ta.id), "activity": str(contribution.id)}
        self.execute('mutation($profile: ID!, $trans: TransactionInput!) { updateTransaction(profileId: $profile, transData: $trans) { trans { id } } }', variables, errors=True)
        self.assertEqual(self.ta.reload().activity.id, self.activity.id)
        variables["trans"]["stock"] = None
        result = self.execute('mutation($profile: ID!, $trans: TransactionInput!) { updateTransaction(profileId: $profile, transData: $trans) { trans { stock { id } } } }', variables)
        self.assertIsNone(result.data["updateTransaction"]["trans"]["stock"])

    def test_interest_and_withholding_tax_accept_optional_stock(self):
        query = 'mutation($profile: ID!, $trans: TransactionInput!) { createTransaction(profileId: $profile, transData: $trans) { transaction { stock { id } total } } }'
        for name in ("Withholding Tax", "Interest"):
            activity = Activity(name=name).save()
            for stock in (None, str(self.stock.id)):
                with self.subTest(activity=name, stock=stock):
                    result = self.execute(query, {"profile": str(self.alice.id), "trans": {"platform": str(self.pa.id), "activity": str(activity.id), "stock": stock, "total": "25"}})
                    self.assertIsNone(result.errors)
                    self.assertEqual(result.data["createTransaction"]["transaction"]["stock"], {"id": stock} if stock else None)

    def test_withdrawal_setup_is_idempotent_and_transactions_are_cash_only(self):
        self.assertIsNone(add_withdrawal_activity())
        self.assertEqual(Activity.objects(name="Withdrawal").count(), 0)
        withdrawal = add_withdrawal_activity(True)
        self.assertEqual(add_withdrawal_activity(True).id, withdrawal.id)
        self.assertEqual(Activity.objects(name="Withdrawal").count(), 1)
        self.assertEqual(Transaction.objects.count(), 2)
        query = 'mutation($profile: ID!, $trans: TransactionInput!) { createTransaction(profileId: $profile, transData: $trans) { transaction { stock { id } total } } }'
        variables = {"profile": str(self.alice.id), "trans": {"platform": str(self.pa.id), "activity": str(withdrawal.id), "total": "25"}}
        self.assertIsNone(self.execute(query, variables).data["createTransaction"]["transaction"]["stock"])
        variables["trans"]["stock"] = str(self.stock.id)
        self.execute(query, variables, errors=True)

    def test_mismatched_account_is_rejected(self):
        other = Account(code="RRSP").save()
        self.create_transaction(self.pa, other, errors=True)

    def test_update_rejects_foreign_record_and_platform(self):
        query = 'mutation($profile: ID!, $trans: TransactionInput!) { updateTransaction(profileId: $profile, transData: $trans) { trans { id } } }'
        for trans in [{"id": str(self.tb.id), "total": "1"}, {"id": str(self.ta.id), "platform": str(self.pb.id)}]:
            self.execute(query, {"profile": str(self.alice.id), "trans": trans}, errors=True)
        self.assertEqual(self.tb.reload().total, 70)
        self.assertEqual(self.ta.reload().platform.id, self.pa.id)

    def test_update_platform_and_account_require_owned_destination_and_reviewed_currency_amounts(self):
        destination = Platform(name="Other Broker", account=self.account, currency=self.currency, profile=self.alice).save()
        wrong_account = Platform(name="RRSP Broker", account=Account(code="RRSP").save(), currency=self.currency, profile=self.alice).save()
        wrong_currency = Platform(name="USD Broker", account=self.account, currency=Currency(code="USD").save(), profile=self.alice).save()
        query = 'mutation($profile: ID!, $trans: TransactionInput!) { updateTransaction(profileId: $profile, transData: $trans) { trans { id platform { id } } } }'
        for platform in (wrong_currency, self.pb):
            self.execute(query, {"profile": str(self.alice.id), "trans": {"id": str(self.ta.id), "platform": str(platform.id)}}, errors=True)
            self.assertEqual(self.ta.reload().platform.id, self.pa.id)
        result = self.execute(query, {"profile": str(self.alice.id), "trans": {"id": str(self.ta.id), "platform": str(destination.id)}})
        self.assertEqual(result.data["updateTransaction"]["trans"]["platform"]["id"], str(destination.id))
        self.assertEqual(self.query_rows("transactionsByPlatform", platform=self.pa.id), [])
        self.assertEqual(self.query_rows("transactionsByPlatform", platform=destination.id)[0]["id"], str(self.ta.id))
        self.execute(query, {"profile": str(self.alice.id), "trans": {"id": str(self.ta.id), "platform": str(wrong_account.id), "account": str(self.account.id)}}, errors=True)
        self.assertEqual(self.ta.reload().platform.id, destination.id)
        self.execute(query, {"profile": str(self.alice.id), "trans": {"id": str(self.ta.id), "platform": str(wrong_account.id), "account": str(wrong_account.account.id)}})
        self.assertEqual(self.ta.reload().account.id, wrong_account.account.id)
        self.assertEqual(self.ta.platform.id, wrong_account.id)
        self.assertEqual(self.query_rows("transactionsByAccount", account=wrong_account.account.id)[0]["id"], str(self.ta.id))
        self.assertEqual(self.query_rows("transactionsByAccount", account=self.account.id), [])

    def test_fractional_shares_create_update_and_query_preserve_precision(self):
        result = self.execute('mutation($profile: ID!, $trans: TransactionInput!) { createTransaction(profileId: $profile, transData: $trans) { transaction { id shares } } }',
                              {"profile": str(self.alice.id), "trans": {"platform": str(self.pa.id), "activity": str(self.activity.id), "shares": 0.12345678, "total": "10"}})
        created = result.data["createTransaction"]["transaction"]
        self.assertEqual(created["shares"], 0.12345678)
        self.execute('mutation($profile: ID!, $trans: TransactionInput!) { updateTransaction(profileId: $profile, transData: $trans) { trans { shares } } }',
                     {"profile": str(self.alice.id), "trans": {"id": created["id"], "shares": 1.00000001}})
        rows = self.query_rows("transactionsByAccount", account=self.account.id)
        self.assertEqual(next(row["shares"] for row in rows if row["id"] == created["id"]), 1.00000001)
        self.assertEqual(next(row["shares"] for row in rows if row["id"] == str(self.ta.id)), 2)

    def test_update_can_set_fee_and_total_to_zero(self):
        self.execute('mutation($profile: ID!, $trans: TransactionInput!) { updateTransaction(profileId: $profile, transData: $trans) { trans { fee total } } }',
                     {"profile": str(self.alice.id), "trans": {"id": str(self.ta.id), "fee": "0", "total": "0"}})
        self.assertEqual(self.ta.reload().fee, 0)
        self.assertEqual(self.ta.total, 0)

    def test_delete_checks_ownership_and_retains_platform_with_transactions(self):
        for field, record in [("deleteTransaction", self.tb), ("deletePlatform", self.pb), ("deleteContributionLimit", self.lb), ("deletePlatform", self.pa)]:
            self.execute('mutation { ' + field + '(profileId: "' + str(self.alice.id) + '", id: "' + str(record.id) + '") { success } }', errors=True)
        self.assertEqual(Transaction.objects.count(), 2)
        self.assertEqual(Platform.objects.count(), 2)

    def test_delete_transaction_refreshes_cached_lists_and_preserves_other_profiles(self):
        self.query_rows("transactionsByAccount", account=self.account.id)
        result = self.execute('mutation($profile: ID!, $id: ID!) { deleteTransaction(profileId: $profile, id: $id) { success } }',
                              {"profile": str(self.alice.id), "id": str(self.ta.id)})
        self.assertTrue(result.data["deleteTransaction"]["success"])
        self.assertEqual(self.query_rows("transactionsByAccount", account=self.account.id), [])
        self.assertEqual(self.query_rows("transactionsByAccount", self.bob, account=self.account.id)[0]["id"], str(self.tb.id))
        self.assertEqual(Transaction.objects.count(), 1)

    def test_transfer_cannot_cross_profiles(self):
        self.execute('mutation { transferAccount(profileId: "' + str(self.alice.id) + '", transFrom: "' + str(self.pa.id) + '", transTo: "' + str(self.pb.id) + '") { success } }', errors=True)
        self.assertEqual(self.ta.reload().platform.id, self.pa.id)

    def test_transfer_checks_account_and_currency(self):
        other = Platform(name="Other", account=Account(code="RRSP").save(), currency=self.currency, profile=self.alice).save()
        self.execute('mutation { transferAccount(profileId: "' + str(self.alice.id) + '", transFrom: "' + str(self.pa.id) + '", transTo: "' + str(other.id) + '") { success } }', errors=True)
        self.assertEqual(self.ta.reload().platform.id, self.pa.id)

    def test_create_contribution_limit_assigns_profile(self):
        result = self.execute('mutation($profile: ID!, $limit: ContributionLimitInput!) { createContributionLimit(profileId: $profile, contrLimitData: $limit) { contributionLimit { profile { id } } } }',
                              {"profile": str(self.bob.id), "limit": {"account": str(self.account.id), "amount": "500", "yearEnd": "2026-12-31"}})
        self.assertEqual(result.data["createContributionLimit"]["contributionLimit"]["profile"]["id"], str(self.bob.id))

    def test_redis_separates_profiles_for_same_account(self):
        alice = self.query_rows("transactionsByAccount", account=self.account.id)
        bob = self.query_rows("transactionsByAccount", self.bob, account=self.account.id)
        self.assertEqual(alice[0]["shares"], 2)
        self.assertEqual(bob[0]["shares"], 7)
        with patch.object(mongomock.collection.Collection, "find", side_effect=AssertionError("unexpected DB read")):
            self.assertEqual(self.query_rows("transactionsByAccount", account=self.account.id), alice)
            self.assertEqual(self.query_rows("transactionsByAccount", self.bob, account=self.account.id), bob)

    def test_new_platforms_require_ownership(self):
        with self.assertRaises(ValidationError):
            Platform(name="Missing profile").save()

    def test_migration_dry_run_apply_and_rerun(self):
        Platform._get_collection().update_one({"_id": self.pa.id}, {"$unset": {"profile": ""}})
        ContributionLimit._get_collection().update_one({"_id": self.la.id}, {"$unset": {"profile": ""}})
        result = migrate_profiles(name="Legacy owner")
        self.assertEqual(result["platforms"], 1)
        self.assertFalse(Profile.objects(name="Legacy owner"))
        result = migrate_profiles(name="Legacy owner", apply=True)
        owner = Profile.objects.get(name="Legacy owner")
        self.assertEqual(self.pa.reload().profile.id, owner.id)
        self.assertEqual(self.la.reload().profile.id, owner.id)
        self.assertEqual(self.pb.reload().profile.id, self.bob.id)
        self.assertEqual(migrate_profiles(name="Legacy owner", apply=True)["platforms"], 0)
        self.assertEqual(Profile.objects(name="Legacy owner").count(), 1)
        self.assertEqual(Transaction.objects.count(), 2)

    def test_migration_stops_before_writes_for_orphan_transactions(self):
        Transaction._get_collection().update_one({"_id": self.ta.id}, {"$unset": {"platform": ""}})
        with self.assertRaisesRegex(ValueError, "missing platform"):
            migrate_profiles(name="Legacy owner", apply=True)
        self.assertFalse(Profile.objects(name="Legacy owner"))

    def test_migration_stops_before_writes_for_account_mismatch(self):
        self.ta.account = Account(code="RRSP").save()
        self.ta.save()
        with self.assertRaisesRegex(ValueError, "account/platform mismatch"):
            migrate_profiles(name="Legacy owner", apply=True)
        self.assertFalse(Profile.objects(name="Legacy owner"))


if __name__ == "__main__":
    unittest.main()
