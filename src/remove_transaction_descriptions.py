"""Remove unused transaction descriptions. Dry run unless --apply is supplied."""
import argparse
from cache.backend import cache
from models.models import Transaction


def remove_transaction_descriptions(apply=False):
    collection = Transaction._get_collection()
    query = {"description": {"$exists": True}}
    matched = collection.count_documents(query)
    modified = 0
    if apply:
        modified = collection.update_many(query, {"$unset": {"description": ""}}).modified_count
        if cache.enabled:
            # Retire active snapshots before removing the old payloads, which
            # may also contain descriptions. Leave the new generation key.
            cache.invalidate("transactions")
            pattern = cache.settings.prefix + ":transactions:*"
            generation_key = cache.settings.prefix + ":transactions:generation"
            for key in cache.client.scan_iter(match=pattern):
                if key != generation_key:
                    cache.client.delete(key)
    return {"matched": matched, "modified": modified,
            "remaining": collection.count_documents(query)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from database.database import client
    try:
        result = remove_transaction_descriptions(args.apply)
        print(result)
    finally:
        client.close()


if __name__ == "__main__":
    main()
