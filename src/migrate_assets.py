"""Seed asset types and assign Stock to unclassified stocks. Dry-run by default."""
import argparse
from models.asset import Asset
from models.stock import Stock

ASSET_NAMES = ("Stock", "Index Fund", "Mutual Fund", "GIC")


def migrate_assets(apply=False):
    collection = Stock._get_collection()
    missing = collection.count_documents({"asset_id": None})
    legacy = Asset.objects(name="Index/Mutual Fund").first()
    legacy_count = collection.count_documents({"asset_id": legacy.id}) if legacy else 0
    if apply:
        if legacy:
            index = Asset.objects(name="Index Fund").first()
            if index:
                collection.update_many({"asset_id": legacy.id}, {"$set": {"asset_id": index.id}})
                legacy.delete()
            else:
                legacy.name = "Index Fund"
                legacy.save()
        assets = {name: Asset.objects(name=name).modify(upsert=True, new=True, set_on_insert__name=name)
                  for name in ASSET_NAMES}
        collection.update_many({"asset_id": None}, {"$set": {"asset_id": assets["Stock"].id}})
        from cache.queries import invalidate_transactions
        invalidate_transactions()
    return {"legacy_fund_stocks": legacy_count, "unclassified_stocks": missing, "applied": apply}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write changes; otherwise only count unclassified stocks.")
    args = parser.parse_args()
    from database.database import client
    try:
        result = migrate_assets(args.apply)
        print("{}: {} stock(s) {}".format("Applied" if args.apply else "Dry run", result["unclassified_stocks"],
              "assigned Stock" if args.apply else "need an asset type"))
        print("{} combined fund classification(s) {}; Index Fund and Mutual Fund {}.".format(
            result["legacy_fund_stocks"], "migrated to Index Fund" if args.apply else "would migrate to Index Fund",
            "are available" if args.apply else "will be seeded"))
    finally:
        client.close()


if __name__ == "__main__":
    main()
