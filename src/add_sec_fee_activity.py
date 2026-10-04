"""Add SEC Fee without modifying existing activities or transactions."""
import argparse
from models.models import Activity


def add_sec_fee_activity(apply=False):
    if not apply:
        return Activity.objects(name="SEC Fee").first()
    return Activity.objects(name="SEC Fee").modify(upsert=True, new=True, set_on_insert__name="SEC Fee")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from database.database import client
    try:
        activity = add_sec_fee_activity(args.apply)
        print("SEC Fee activity is available." if activity else "SEC Fee is missing; run with --apply.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
