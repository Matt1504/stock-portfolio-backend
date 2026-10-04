import json
import os
import runpy
from types import SimpleNamespace
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import mongomock
from mongoengine import connect, disconnect
from mongoengine.connection import get_connection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from models.account import Account
from models.platform import Platform
from models.models import Profile, ContributionLimit, Transaction, Activity
from schemas.schema import schema
from schemas.transaction_validation import contribution_warnings
from add_nrsa_account import add_nrsa_account, ACCOUNT_NAME


class NrsaTests(unittest.TestCase):
    def setUp(self):
        disconnect()
        with patch("mongoengine.connection.MongoClient", mongomock.MongoClient):
            connect("nrsa_tests", uuidRepresentation="standard")
        Account.drop_collection()

    def tearDown(self):
        disconnect()

    @patch("add_nrsa_account.invalidate_accounts")
    def test_migration_dry_run_and_idempotence(self, invalidate):
        existing = Account(name="Registered Retirement Savings Plan", code="RRSP").save()
        self.assertIsNone(add_nrsa_account())
        invalidate.assert_not_called()
        self.assertEqual(Account.objects.count(), 1)
        added = add_nrsa_account(True)
        self.assertEqual(added.name, ACCOUNT_NAME)
        self.assertEqual(added.code, "NRSA")
        self.assertFalse(added.has_contribution_limit)
        self.assertEqual(add_nrsa_account(True).id, added.id)
        self.assertEqual(Account.objects.count(), 2)
        self.assertEqual(Account.objects.get(id=existing.id).code, "RRSP")
        self.assertEqual(invalidate.call_count, 2)

    def test_fresh_setup_contains_nrsa_once(self):
        data = json.loads((Path(__file__).resolve().parents[1] / "src/startup.json").read_text())
        self.assertEqual([account for account in data["accounts"] if account["code"] == "NRSA"], [{"name": ACCOUNT_NAME, "code": "NRSA", "has_contribution_limit": False}])


    def test_fresh_setup_preserves_platform_account_codes(self):
        source = Path(__file__).resolve().parents[1] / "src"
        data = json.loads((source / "startup.json").read_text())
        mock_database = SimpleNamespace(client=get_connection(), DATABASE="nrsa_tests")
        previous = Path.cwd()
        try:
            os.chdir(source)
            with patch.dict(sys.modules, {"database.database": mock_database}):
                runpy.run_path(str(source / "database_init.py"))
        finally:
            os.chdir(previous)
        self.assertEqual(Account.objects(code="NRSA").count(), 1)
        self.assertEqual(sorted(platform.account.code for platform in Platform.objects), sorted(item["account"]["code"] for item in data["platforms"]))


    def test_legacy_defaults_are_backfilled_without_overwriting_other_flags(self):
        legacy = Account(name="TFSA", code="TFSA").save()
        unlimited = Account(name="Other unlimited", code="OTHER", has_contribution_limit=False).save()
        Account._get_collection().update_one({"_id": legacy.id}, {"$unset": {"has_contribution_limit": ""}})
        self.assertTrue(Account.objects.get(id=legacy.id).has_contribution_limit)
        with patch("add_nrsa_account.invalidate_accounts"):
            add_nrsa_account(True)
        self.assertTrue(Account._get_collection().find_one({"_id": legacy.id})["has_contribution_limit"])
        self.assertFalse(Account.objects.get(id=unlimited.id).has_contribution_limit)

    def test_unlimited_accounts_reject_limits_and_skip_warnings(self):
        profile = Profile(name="Owner").save()
        account = Account(name=ACCOUNT_NAME, code="NRSA", has_contribution_limit=False).save()
        # Stale limits can still be inspected/deleted, but must never cause warnings.
        legacy_limit = ContributionLimit(profile=profile, account=account, amount=1).save()
        activity = Activity(name="Contribution").save()
        candidate = Transaction(account=account, activity=activity, total=10000)
        self.assertEqual(contribution_warnings(candidate, str(profile.id)), [])
        result = schema.execute('mutation($profile: ID!, $limit: ContributionLimitInput!) { createContributionLimit(profileId: $profile, contrLimitData: $limit) { contributionLimit { id } } }', variable_values={"profile": str(profile.id), "limit": {"account": str(account.id), "amount": "100", "yearEnd": "2026-12-31"}})
        self.assertTrue(result.errors)
        self.assertEqual(result.errors[0].extensions["code"], "CONTRIBUTION_LIMIT_NOT_APPLICABLE")
        self.assertEqual(ContributionLimit.objects(profile=profile, account=account).count(), 1)
        self.assertIsNotNone(ContributionLimit.objects(id=legacy_limit.id).first())
        query = schema.execute('query { accounts { edges { node { code hasContributionLimit } } } }')
        self.assertIsNone(query.errors)
        self.assertEqual(query.data["accounts"]["edges"][0]["node"], {"code": "NRSA", "hasContributionLimit": False})
