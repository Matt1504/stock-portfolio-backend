"""Backfill legacy currency metadata without converting recorded amounts."""
import argparse
from decimal import Decimal
from models.models import Transaction, Platform
from cache.queries import invalidate_transactions


def migrate_transaction_currencies(apply=False):
    changed = skipped = 0
    # Group updates by platform to avoid a remote write for every transaction.
    for platform in Platform.objects:
        if not platform.currency:
            skipped += Transaction.objects(platform=platform).count()
            continue
        records = Transaction.objects(platform=platform)
        for field, value in (("price_currency", platform.currency), ("total_currency", platform.currency), ("exchange_rate", Decimal(1))):
            missing = records.filter(**{field: None})
            count = missing.count()
            changed += count
            if apply and count:
                missing.update(**{"set__" + field: value})
    if apply:
        invalidate_transactions()
    return {'fields_updated' if apply else 'fields_to_update': changed, 'skipped': skipped}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    from database.database import client
    try:
        print(migrate_transaction_currencies(args.apply))
    finally:
        client.close()


if __name__ == '__main__':
    main()
