"""Add Stock Spinoff without modifying existing activities or transactions."""
import argparse
from models.models import Activity


def add_stock_spinoff_activity(apply=False):
    if not apply:
        return Activity.objects(name="Stock Spinoff").first()
    return Activity.objects(name="Stock Spinoff").modify(upsert=True, new=True, set_on_insert__name="Stock Spinoff")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from database.database import client
    try:
        activity = add_stock_spinoff_activity(args.apply)
        print("Stock Spinoff activity is available." if activity else "Stock Spinoff is missing; run with --apply.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
