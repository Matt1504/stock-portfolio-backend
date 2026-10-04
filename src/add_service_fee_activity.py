"""Add Service Fee without modifying existing activities or transactions."""
import argparse
from models.models import Activity


def add_service_fee_activity(apply=False):
    if not apply:
        return Activity.objects(name="Service Fee").first()
    return Activity.objects(name="Service Fee").modify(upsert=True, new=True, set_on_insert__name="Service Fee")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from database.database import client
    try:
        activity = add_service_fee_activity(args.apply)
        print("Service Fee activity is available." if activity else "Service Fee is missing; run with --apply.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
