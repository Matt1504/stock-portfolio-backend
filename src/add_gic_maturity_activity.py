"""Seed GIC Maturity without changing existing transaction records."""
import argparse
from models.models import Activity, Transaction


def add_gic_maturity_activity(apply=False):
    if not apply:
        return Activity.objects(name="GIC Maturity").first()
    activity = Activity.objects(name="GIC Maturity").modify(upsert=True, new=True, set_on_insert__name="GIC Maturity")
    Transaction.ensure_indexes()
    return activity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from database.database import client
    try:
        activity = add_gic_maturity_activity(args.apply)
        print("GIC Maturity activity is available." if activity else "GIC Maturity is missing; run with --apply.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
