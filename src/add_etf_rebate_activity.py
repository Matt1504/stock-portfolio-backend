"""Add ETF Rebate without modifying existing activities or transactions."""
import argparse
from models.models import Activity


def add_etf_rebate_activity(apply=False):
    if not apply:
        return Activity.objects(name="ETF Rebate").first()
    return Activity.objects(name="ETF Rebate").modify(upsert=True, new=True, set_on_insert__name="ETF Rebate")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from database.database import client
    try:
        activity = add_etf_rebate_activity(args.apply)
        print("ETF Rebate activity is available." if activity else "ETF Rebate is missing; run with --apply.")
    finally:
        client.close()


if __name__ == "__main__":
    main()
