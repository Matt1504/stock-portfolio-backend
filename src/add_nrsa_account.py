"""Add the NRSA account type without modifying platforms or transactions."""
import argparse
from models.account import Account
from cache.queries import invalidate_accounts

ACCOUNT_NAME = "Non-Registered Savings Account"
ACCOUNT_CODE = "NRSA"


def add_nrsa_account(apply=False):
    if not apply:
        return Account.objects(code=ACCOUNT_CODE).first()
    account = Account.objects(code=ACCOUNT_CODE).modify(
        upsert=True, new=True, set__name=ACCOUNT_NAME, set__has_contribution_limit=False, set_on_insert__code=ACCOUNT_CODE
    )
    # Preserve any explicitly configured flags on other shared account types.
    Account.objects(has_contribution_limit__exists=False).update(set__has_contribution_limit=True)
    invalidate_accounts()
    return account


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from database.database import client
    try:
        account = add_nrsa_account(args.apply)
        print("NRSA account type is available." if account else "NRSA is missing; run with --apply.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
