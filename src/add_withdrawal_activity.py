"""Add Withdrawal without changing transactions or existing activity IDs."""
import argparse
from models.models import Activity


def add_withdrawal_activity(apply=False):
    existing = Activity.objects(name="Withdrawal").first()
    if existing or not apply:
        return existing
    return Activity.objects(name="Withdrawal").modify(upsert=True, new=True, set_on_insert__name="Withdrawal")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Insert the activity if missing; otherwise only check.")
    args = parser.parse_args()
    from database.database import client  # Connect only when explicitly running the command.
    activity = add_withdrawal_activity(args.apply)
    print("Withdrawal activity is available." if activity else "Withdrawal is missing. Run with --apply to add it.")
    client.close()


if __name__ == "__main__":
    main()
